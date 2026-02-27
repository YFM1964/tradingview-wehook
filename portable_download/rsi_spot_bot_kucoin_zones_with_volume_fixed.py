#!/usr/bin/env python3
"""
RSI Spot Trading Bot - Buy/Sell Strategy
Multi-trade in RSI Zones with momentum confirmation
"""

import ccxt
import pandas as pd
import time
import json
from datetime import datetime
from collections import deque
import os
import traceback

# ============================================================================
# Initial Bot Configuration
# ============================================================================

INITIAL_CONFIG = {
    # Mode
    'MODE': 'demo',  # 'demo' or 'live'
    
    # Market Type
    'MARKET_TYPE': 'spot',
    
    # Market
    'SYMBOL': 'XRP-USDT',  # KuCoin Spot
    'EXCHANGE': 'kucoin',
    
    # Check Interval
    'CHECK_INTERVAL_SECONDS': 60,
    
    # Initial Balance (for demo mode)
    'INITIAL_BALANCE_USDT': 1000,  # Starting USDT
    'INITIAL_BALANCE_COIN': 0,     # Starting coin (0 = start with USDT only)
    
    # RSI Settings
    'RSI_PERIOD': 14,
    
    # API (for live mode)
    'API_KEY': '',
    'API_SECRET': '',
    'API_PASSPHRASE': '',
}

# Dynamic config (reloadable from config_symbol.json)
DYNAMIC_CONFIG_DEFAULTS = {
    'TIMEFRAME': '1h',
    'RSI_MOMENTUM_MIN_CHANGE': 4.0,
    'RSI_MOMENTUM_LOOKBACK': 4,
    'MIN_PRICE_DIFF': 0.02,

    # Position sizing
    'POSITION_SIZING_MODE': 'percent_cycle_base',  # 'percent_cycle_base' or 'fixed_usdt'
    'POSITION_SIZE_PERCENT': 100.0,  # Per-entry size from cycle base (100 = all-in single entry)
    'POSITION_SIZE_USDT': 100.0,     # Used when POSITION_SIZING_MODE = 'fixed_usdt'
    'POSITION_ALLOW_SCALE_IN': True,
    'POSITION_MAX_ENTRIES': 10,
    'MIN_ORDER_USDT': 10.0,

    # Network / Retry settings
    'NETWORK_TIMEOUT_MS': 20000,
    'NETWORK_MAX_RETRIES': 3,
    'NETWORK_RETRY_DELAY_SECONDS': 2.0,
    'NETWORK_BACKOFF_MULTIPLIER': 2.0,
    
    # Volume Filter Settings
    'USE_VOLUME_FILTER': False,
    'VOLUME_MA_PERIOD': 20,
    'VOLUME_MULTIPLIER': 1.2,
    
    'USE_RSI_ZONES': True,
    'RSI_OVERSOLD_RANGE': [30, 10],
    'RSI_OVERBOUGHT_RANGE': [70, 95],
    
    # Stop Loss Settings
    'USE_STOP_LOSS': False,
    'STOP_LOSS_USDT': 0.10,
    
    # Take Profit Settings  
    'USE_TAKE_PROFIT': False,
    'TAKE_PROFIT_USDT': 0.10,
    
    # Trailing Stop Settings
    'USE_TRAILING_STOP': False,
    'TRAILING_STOP_TYPE': 'percent',  # 'usdt' or 'percent'
    'TRAILING_STOP_USDT': 0.05,
    'TRAILING_STOP_PERCENT': 3.0,
    
    # Manual Control
    'ENABLE_MANUAL_CONTROL': False,
    'MANUAL_COMMAND': None,
}

# ============================================================================
# Bot Code
# ============================================================================

class RSISpotBot:
    def __init__(self):
        self.config = INITIAL_CONFIG.copy()
        self.dynamic_config = DYNAMIC_CONFIG_DEFAULTS.copy()
        
        if self.config['MODE'] not in ['demo', 'live']:
            raise ValueError("MODE must be 'demo' or 'live'")
        
        mode_suffix = self.config['MODE']
        symbol_clean = self.config['SYMBOL'].replace('/', '_').replace('-', '_').lower()
        self.log_dir = f'logs_{symbol_clean}_rsi_spot_{mode_suffix}'
        os.makedirs(self.log_dir, exist_ok=True)
        
        self.config_file = f'{self.log_dir}/config_{symbol_clean}.json'
        self.error_log_file = f'{self.log_dir}/error_log_{symbol_clean}.txt'
        
        # Load or create config
        self._load_or_create_config()
        
        self.exchange = self._init_exchange()
        
        # Auto-detect correct symbol
        if self.config['EXCHANGE'].lower() == 'kucoin':
            self.config['SYMBOL'] = self._find_kucoin_symbol()
        
        # Extract base currency
        if '/' in self.config['SYMBOL']:
            self.base_currency = self.config['SYMBOL'].split('/')[0]
        elif '-' in self.config['SYMBOL']:
            self.base_currency = self.config['SYMBOL'].split('-')[0]
        else:
            self.base_currency = self.config['SYMBOL'].replace('USDT', '')
        self.quote_currency = 'USDT'
        
        # Spot Balance Tracking
        self.balance_usdt = self.config['INITIAL_BALANCE_USDT']
        self.balance_coin = self.config['INITIAL_BALANCE_COIN']
        self.starting_balance_usdt = self.balance_usdt
        self.starting_balance_coin = self.balance_coin
        
        self.current_position = None  # {'side': 'BUY', 'entry_price': 2.0, 'amount': 500, 'invested_usdt': 1000, 'entries': 1}
        self.total_pnl = 0.0

        # Cycle-based position sizing state
        self.cycle_base_balance_usdt = None
        self.cycle_invested_usdt = 0.0
        
        # Trailing Stop tracking
        self.highest_price_since_entry = None
        
        # RSI history
        self.rsi_history = deque(maxlen=self.dynamic_config['RSI_MOMENTUM_LOOKBACK'])
        
        # Trading history
        self.trades = []
        self.start_price = None
        
        # Config modification tracking
        self.last_config_mtime = os.path.getmtime(self.config_file) if os.path.exists(self.config_file) else 0
        
        self._print_startup_message()

    @staticmethod
    def _safe_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_bool(value, default=False):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ('true', '1', 'yes', 'y', 'on'):
                return True
            if normalized in ('false', '0', 'no', 'n', 'off', ''):
                return False
        return default

    def _prepare_dynamic_config(self, loaded_config):
        """Merge dynamic config with defaults and normalize values."""
        merged = DYNAMIC_CONFIG_DEFAULTS.copy()
        if isinstance(loaded_config, dict):
            merged.update(loaded_config)
        return self._validate_dynamic_config(merged)

    def _validate_dynamic_config(self, config):
        """Validate and normalize dynamic config values."""
        cfg = config.copy()

        cfg['TIMEFRAME'] = str(cfg.get('TIMEFRAME', '1h'))
        cfg['RSI_MOMENTUM_MIN_CHANGE'] = max(0.0, self._safe_float(cfg.get('RSI_MOMENTUM_MIN_CHANGE'), 4.0))
        cfg['RSI_MOMENTUM_LOOKBACK'] = self._safe_int(cfg.get('RSI_MOMENTUM_LOOKBACK'), 4)
        if cfg['RSI_MOMENTUM_LOOKBACK'] not in (3, 4):
            cfg['RSI_MOMENTUM_LOOKBACK'] = 4

        cfg['MIN_PRICE_DIFF'] = max(0.0, self._safe_float(cfg.get('MIN_PRICE_DIFF'), 0.02))

        sizing_mode = str(cfg.get('POSITION_SIZING_MODE', 'percent_cycle_base')).lower()
        if sizing_mode not in ('percent_cycle_base', 'fixed_usdt'):
            sizing_mode = 'percent_cycle_base'
        cfg['POSITION_SIZING_MODE'] = sizing_mode
        cfg['POSITION_SIZE_PERCENT'] = min(100.0, max(0.1, self._safe_float(cfg.get('POSITION_SIZE_PERCENT'), 100.0)))
        cfg['POSITION_SIZE_USDT'] = max(0.1, self._safe_float(cfg.get('POSITION_SIZE_USDT'), 100.0))
        cfg['POSITION_ALLOW_SCALE_IN'] = self._safe_bool(cfg.get('POSITION_ALLOW_SCALE_IN', True), True)
        cfg['POSITION_MAX_ENTRIES'] = max(1, self._safe_int(cfg.get('POSITION_MAX_ENTRIES'), 10))
        cfg['MIN_ORDER_USDT'] = max(0.0, self._safe_float(cfg.get('MIN_ORDER_USDT'), 10.0))

        cfg['NETWORK_TIMEOUT_MS'] = max(5000, self._safe_int(cfg.get('NETWORK_TIMEOUT_MS'), 20000))
        cfg['NETWORK_MAX_RETRIES'] = max(1, self._safe_int(cfg.get('NETWORK_MAX_RETRIES'), 3))
        cfg['NETWORK_RETRY_DELAY_SECONDS'] = max(0.1, self._safe_float(cfg.get('NETWORK_RETRY_DELAY_SECONDS'), 2.0))
        cfg['NETWORK_BACKOFF_MULTIPLIER'] = max(1.0, self._safe_float(cfg.get('NETWORK_BACKOFF_MULTIPLIER'), 2.0))

        cfg['USE_VOLUME_FILTER'] = self._safe_bool(cfg.get('USE_VOLUME_FILTER', False), False)
        cfg['VOLUME_MA_PERIOD'] = max(2, self._safe_int(cfg.get('VOLUME_MA_PERIOD'), 20))
        cfg['VOLUME_MULTIPLIER'] = max(0.1, self._safe_float(cfg.get('VOLUME_MULTIPLIER'), 1.2))

        cfg['USE_RSI_ZONES'] = self._safe_bool(cfg.get('USE_RSI_ZONES', True), True)
        oversold = cfg.get('RSI_OVERSOLD_RANGE', [30, 10])
        if not isinstance(oversold, list) or len(oversold) != 2:
            oversold = [30, 10]
        oversold_a = self._safe_float(oversold[0], 30.0)
        oversold_b = self._safe_float(oversold[1], 10.0)
        cfg['RSI_OVERSOLD_RANGE'] = [max(oversold_a, oversold_b), min(oversold_a, oversold_b)]

        overbought = cfg.get('RSI_OVERBOUGHT_RANGE', [70, 95])
        if not isinstance(overbought, list) or len(overbought) != 2:
            overbought = [70, 95]
        overbought_a = self._safe_float(overbought[0], 70.0)
        overbought_b = self._safe_float(overbought[1], 95.0)
        cfg['RSI_OVERBOUGHT_RANGE'] = [min(overbought_a, overbought_b), max(overbought_a, overbought_b)]

        cfg['USE_STOP_LOSS'] = self._safe_bool(cfg.get('USE_STOP_LOSS', False), False)
        cfg['STOP_LOSS_USDT'] = max(0.0, self._safe_float(cfg.get('STOP_LOSS_USDT'), 0.10))

        cfg['USE_TAKE_PROFIT'] = self._safe_bool(cfg.get('USE_TAKE_PROFIT', False), False)
        cfg['TAKE_PROFIT_USDT'] = max(0.0, self._safe_float(cfg.get('TAKE_PROFIT_USDT'), 0.10))

        cfg['USE_TRAILING_STOP'] = self._safe_bool(cfg.get('USE_TRAILING_STOP', False), False)
        trailing_type = str(cfg.get('TRAILING_STOP_TYPE', 'percent')).lower()
        cfg['TRAILING_STOP_TYPE'] = 'usdt' if trailing_type == 'usdt' else 'percent'
        cfg['TRAILING_STOP_USDT'] = max(0.0, self._safe_float(cfg.get('TRAILING_STOP_USDT'), 0.05))
        cfg['TRAILING_STOP_PERCENT'] = max(0.1, self._safe_float(cfg.get('TRAILING_STOP_PERCENT'), 3.0))

        cfg['ENABLE_MANUAL_CONTROL'] = self._safe_bool(cfg.get('ENABLE_MANUAL_CONTROL', False), False)
        cfg['MANUAL_COMMAND'] = cfg.get('MANUAL_COMMAND')

        return cfg
    
    def _load_or_create_config(self):
        """Load config from file or create default"""
        loaded_config = {}
        file_exists = os.path.exists(self.config_file)

        if file_exists:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
            print(f'Config loaded from: {self.config_file}')
        else:
            print(f'Config created: {self.config_file}')

        normalized_config = self._prepare_dynamic_config(loaded_config)
        self.dynamic_config = normalized_config

        # Keep config file consistent with current schema/defaults.
        if (not file_exists) or (loaded_config != normalized_config):
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.dynamic_config, f, indent=4, ensure_ascii=False)
            if file_exists:
                print('Config normalized with defaults/validated values')
    
    def _init_exchange(self):
        """Initialize CCXT exchange"""
        exchange_options = {
            'enableRateLimit': True,
            'timeout': self.dynamic_config['NETWORK_TIMEOUT_MS'],
            'options': {'defaultType': 'spot'}
        }

        if self.config['MODE'] == 'demo':
            # Demo mode: no API needed
            exchange = ccxt.kucoin(exchange_options)
            return exchange
        else:
            # Live mode: API required
            exchange_options.update({
                'apiKey': self.config['API_KEY'],
                'secret': self.config['API_SECRET'],
                'password': self.config['API_PASSPHRASE'],
            })
            exchange = ccxt.kucoin(exchange_options)
            return exchange
    
    def _find_kucoin_symbol(self):
        """Auto-detect correct KuCoin symbol format"""
        symbol_input = self.config['SYMBOL']
        print(f'Testing symbol: {symbol_input}')
        
        # Try various formats
        formats_to_try = [
            symbol_input,
            symbol_input.replace('-', '/'),
            symbol_input.replace('/', '-'),
            symbol_input.replace('USDTM', '-USDT'),
        ]
        
        for fmt in formats_to_try:
            try:
                self.exchange.fetch_ticker(fmt)
                print(f'✅ Found working symbol: {fmt}')
                return fmt
            except:
                continue
        
        print(f'⚠️ Could not find symbol, using: {symbol_input}')
        return symbol_input
    
    def _print_startup_message(self):
        """Print startup information"""
        print('=' * 80)
        print(f'RSI Spot Bot Started - MODE: {self.config["MODE"].upper()}')
        print(f'Exchange: {self.config["EXCHANGE"].upper()} | Symbol: {self.config["SYMBOL"]} (Spot)')
        print(f'Timeframe: {self.dynamic_config["TIMEFRAME"]}')
        print(f'Check Interval: {self.config["CHECK_INTERVAL_SECONDS"]} seconds')
        print(f'RSI Period: {self.config["RSI_PERIOD"]} | Min Change: {self.dynamic_config["RSI_MOMENTUM_MIN_CHANGE"]}')
        print(f'RSI Momentum Lookback: {self.dynamic_config["RSI_MOMENTUM_LOOKBACK"]} points')

        # Position sizing
        sizing_mode = self.dynamic_config.get('POSITION_SIZING_MODE', 'percent_cycle_base')
        if sizing_mode == 'fixed_usdt':
            print(f'Position Sizing: FIXED {self.dynamic_config["POSITION_SIZE_USDT"]:.2f} USDT per entry')
        else:
            print(f'Position Sizing: {self.dynamic_config["POSITION_SIZE_PERCENT"]:.2f}% of cycle base per entry')
            print('  Cycle base means entry size stays fixed per cycle (no half-of-half staircase)')
        configured_max_entries = self.dynamic_config.get('POSITION_MAX_ENTRIES', 10)
        print(
            f'Scale-in: {"ENABLED" if self.dynamic_config["POSITION_ALLOW_SCALE_IN"] else "DISABLED"} '
            f'| Max Entries (configured/effective): {configured_max_entries}/{self._get_effective_max_entries()}'
        )
        print(f'Min Order: {self.dynamic_config["MIN_ORDER_USDT"]:.2f} USDT')
        
        # RSI Zones
        if self.dynamic_config['USE_RSI_ZONES']:
            print(f'RSI Zones: ENABLED')
            print(f'  Oversold: {self.dynamic_config["RSI_OVERSOLD_RANGE"]}')
            print(f'  Overbought: {self.dynamic_config["RSI_OVERBOUGHT_RANGE"]}')
        else:
            print(f'RSI Zones: DISABLED')
        
        # Stop Loss
        if self.dynamic_config['USE_STOP_LOSS']:
            print(f'Stop Loss: ENABLED at {self.dynamic_config["STOP_LOSS_USDT"]:.4f} USDT')
        else:
            print(f'Stop Loss: DISABLED')
        
        # Take Profit
        if self.dynamic_config['USE_TAKE_PROFIT']:
            print(f'Take Profit: ENABLED at {self.dynamic_config["TAKE_PROFIT_USDT"]:.4f} USDT')
        else:
            print(f'Take Profit: DISABLED')
        
        print(f'Starting Balance: {self.balance_usdt:.4f} USDT + {self.balance_coin:.4f} {self.base_currency}')
        print(f'Strategy: BUY on momentum UP, SELL on momentum DOWN in RSI zones')
        print(f'Config file: {self.config_file}')
        print('=' * 80)
    
    def _log_error(self, message):
        """Log errors to file"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(self.error_log_file, 'a', encoding='utf-8') as f:
            f.write(f'{timestamp} | {message}\n')
            trace_text = traceback.format_exc()
            if trace_text and trace_text.strip() != 'NoneType: None':
                f.write(trace_text + '\n')
            f.write('\n')

    def _call_exchange_with_retry(self, operation_name, method, *args, **kwargs):
        """Retry exchange calls for transient network errors."""
        retries = self.dynamic_config.get('NETWORK_MAX_RETRIES', 3)
        retry_delay = self.dynamic_config.get('NETWORK_RETRY_DELAY_SECONDS', 2.0)
        backoff = self.dynamic_config.get('NETWORK_BACKOFF_MULTIPLIER', 2.0)
        last_error = None

        for attempt in range(1, retries + 1):
            try:
                return method(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.RequestTimeout, ccxt.ExchangeNotAvailable, ccxt.DDoSProtection) as e:
                last_error = e
                if attempt >= retries:
                    break

                sleep_seconds = retry_delay * (backoff ** (attempt - 1))
                print(
                    f'   Network issue in {operation_name} ({type(e).__name__}), '
                    f'retry {attempt}/{retries} in {sleep_seconds:.1f}s...'
                )
                time.sleep(sleep_seconds)
            except Exception:
                raise

        raise last_error
    
    def _reload_config_if_changed(self):
        """Check and reload config if file was modified"""
        try:
            current_mtime = os.path.getmtime(self.config_file)
            if current_mtime > self.last_config_mtime:
                self.last_config_mtime = current_mtime
                
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    new_config_raw = json.load(f)
                new_config = self._prepare_dynamic_config(new_config_raw)
                
                # Only reload if content actually changed
                if new_config != self.dynamic_config:
                    old_lookback = self.dynamic_config.get('RSI_MOMENTUM_LOOKBACK', 4)
                    old_timeout = self.dynamic_config.get('NETWORK_TIMEOUT_MS')
                    self.dynamic_config = new_config

                    # Update RSI history window if lookback changed.
                    new_lookback = self.dynamic_config.get('RSI_MOMENTUM_LOOKBACK', 4)
                    if new_lookback != old_lookback:
                        self.rsi_history = deque(list(self.rsi_history), maxlen=new_lookback)

                    # Keep exchange timeout in sync with config.
                    if old_timeout != self.dynamic_config.get('NETWORK_TIMEOUT_MS'):
                        self.exchange.timeout = self.dynamic_config['NETWORK_TIMEOUT_MS']

                    # Persist normalized values if user edited invalid entries.
                    if new_config_raw != self.dynamic_config:
                        with open(self.config_file, 'w', encoding='utf-8') as f:
                            json.dump(self.dynamic_config, f, indent=4, ensure_ascii=False)

                    print('\n⚙️  Config reloaded')
        except Exception as e:
            self._log_error(f'Error reloading config: {e}')
    
    def get_price(self):
        """Get current price"""
        try:
            ticker = self._call_exchange_with_retry(
                'fetch_ticker',
                self.exchange.fetch_ticker,
                self.config['SYMBOL']
            )
            return ticker['last']
        except Exception as e:
            self._log_error(f'Error getting price: {e}')
            return None
    
    def calculate_rsi(self):
        """Calculate RSI using Wilder's smoothing (matches TradingView)"""
        try:
            timeframe = self.dynamic_config['TIMEFRAME']
            period = self.config['RSI_PERIOD']
            
            ohlcv = self._call_exchange_with_retry(
                'fetch_ohlcv',
                self.exchange.fetch_ohlcv,
                self.config['SYMBOL'],
                timeframe=timeframe,
                limit=period + 50  # More data for accurate Wilder's smoothing
            )

            if not ohlcv or len(ohlcv) < period + 1:
                return None, None
            
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # Calculate RSI using Wilder's smoothing (same as TradingView)
            close = df['close'].copy()
            delta = close.diff()
            
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            
            # Initial average using simple moving average
            avg_gain = gain.rolling(window=period, min_periods=period).mean()
            avg_loss = loss.rolling(window=period, min_periods=period).mean()
            
            # Apply Wilder's smoothing for subsequent values
            for i in range(period, len(df)):
                avg_gain.iloc[i] = (avg_gain.iloc[i-1] * (period - 1) + gain.iloc[i]) / period
                avg_loss.iloc[i] = (avg_loss.iloc[i-1] * (period - 1) + loss.iloc[i]) / period
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            current_rsi = rsi.iloc[-1]
            
            if pd.isna(current_rsi):
                return None, df
            return current_rsi, df
        except Exception as e:
            self._log_error(f'Error calculating RSI: {e}')
            return None, None
    
    def detect_momentum_change(self):
        """Detect RSI momentum reversal with configurable lookback"""
        lookback = self.dynamic_config.get('RSI_MOMENTUM_LOOKBACK', 3)
        
        # Need at least 'lookback' points
        if len(self.rsi_history) < lookback:
            return None, None
        
        # Get last 'lookback' RSI values
        rsi_values = list(self.rsi_history)[-lookback:]
        
        # Calculate slopes between consecutive points
        slopes = []
        for i in range(len(rsi_values) - 1):
            slope = rsi_values[i + 1] - rsi_values[i]
            slopes.append(slope)
        
        # For 3-point: check if slopes[0] and slopes[1] have opposite signs
        # For 4-point: check if trend changed (early slopes vs late slopes)
        
        signal = None
        reason = None
        
        if lookback == 3:
            # Original logic: V or ∧ shape
            slope_old = slopes[0]
            slope_new = slopes[1]
            
            # BUY: downtrend to uptrend
            if slope_old < 0 and slope_new > 0:
                if abs(slope_new) >= self.dynamic_config['RSI_MOMENTUM_MIN_CHANGE']:
                    signal = 'BUY'
                    reason = f'RSI reversal: {rsi_values[0]:.2f}->{rsi_values[1]:.2f}->{rsi_values[2]:.2f} (UP {slope_new:.2f})'
            
            # SELL: uptrend to downtrend
            elif slope_old > 0 and slope_new < 0:
                if abs(slope_new) >= self.dynamic_config['RSI_MOMENTUM_MIN_CHANGE']:
                    signal = 'SELL'
                    reason = f'RSI reversal: {rsi_values[0]:.2f}->{rsi_values[1]:.2f}->{rsi_values[2]:.2f} (DOWN {slope_new:.2f})'
        
        elif lookback == 4:
            # Curved reversal: compare early trend vs late trend
            early_trend = slopes[0] + slopes[1]  # First two slopes
            late_trend = slopes[2]  # Last slope
            
            # BUY: early downtrend, late uptrend (curved bottom)
            if early_trend < 0 and late_trend > 0:
                if abs(late_trend) >= self.dynamic_config['RSI_MOMENTUM_MIN_CHANGE']:
                    signal = 'BUY'
                    reason = f'RSI reversal: {rsi_values[0]:.2f}->{rsi_values[1]:.2f}->{rsi_values[2]:.2f}->{rsi_values[3]:.2f} (UP {late_trend:.2f})'
            
            # SELL: early uptrend, late downtrend (curved top)
            elif early_trend > 0 and late_trend < 0:
                if abs(late_trend) >= self.dynamic_config['RSI_MOMENTUM_MIN_CHANGE']:
                    signal = 'SELL'
                    reason = f'RSI reversal: {rsi_values[0]:.2f}->{rsi_values[1]:.2f}->{rsi_values[2]:.2f}->{rsi_values[3]:.2f} (DOWN {late_trend:.2f})'
        
        return signal, reason
    
    def check_rsi_zone(self, rsi):
        """Check if RSI is in oversold/overbought zone"""
        oversold = self.dynamic_config['RSI_OVERSOLD_RANGE']
        overbought = self.dynamic_config['RSI_OVERBOUGHT_RANGE']
        
        if oversold[1] <= rsi <= oversold[0]:
            return 'OVERSOLD'
        elif overbought[0] <= rsi <= overbought[1]:
            return 'OVERBOUGHT'
        return None
    
    def check_price_filter(self, current_price):
        """Check price difference from last trade"""
        if len(self.trades) == 0:
            if self.start_price is None:
                return True, 0
            price_diff = abs(current_price - self.start_price)
            if price_diff < self.dynamic_config['MIN_PRICE_DIFF']:
                return False, price_diff
            return True, price_diff
        
        last_trade = self.trades[-1]
        if 'exit_price' in last_trade:
            last_price = last_trade['exit_price']
        elif 'price' in last_trade:
            last_price = last_trade['price']
        else:
            return True, 0
        
        price_diff = abs(current_price - last_price)
        if price_diff < self.dynamic_config['MIN_PRICE_DIFF']:
            return False, price_diff
        return True, price_diff
    
    def check_volume_filter(self, df):
        """Check if current volume is above average"""
        if not self.dynamic_config['USE_VOLUME_FILTER']:
            return True, None, None
        
        if len(df) < self.dynamic_config['VOLUME_MA_PERIOD']:
            return True, None, None  # Not enough data
        
        # Calculate volume moving average
        volume_ma_period = self.dynamic_config['VOLUME_MA_PERIOD']
        volume_ma = df['volume'].rolling(window=volume_ma_period).mean()
        
        current_volume = df['volume'].iloc[-1]
        avg_volume = volume_ma.iloc[-1]
        
        if pd.isna(avg_volume) or avg_volume == 0:
            return True, current_volume, None
        
        # Check if current volume exceeds threshold
        volume_threshold = avg_volume * self.dynamic_config['VOLUME_MULTIPLIER']
        volume_ok = current_volume > volume_threshold
        
        return volume_ok, current_volume, avg_volume
    
    def get_unrealized_pnl(self, current_price):
        """Calculate unrealized PNL for current position"""
        if not self.current_position:
            return 0.0, 0.0

        amount = self.current_position['amount']
        invested_usdt = self.current_position.get('invested_usdt', self.current_position['entry_price'] * amount)
        market_value = current_price * amount

        pnl_usdt = market_value - invested_usdt
        pnl_pct = (pnl_usdt / invested_usdt) * 100 if invested_usdt > 0 else 0.0

        return pnl_usdt, pnl_pct

    def _reset_cycle_tracking(self):
        self.cycle_base_balance_usdt = None
        self.cycle_invested_usdt = 0.0

    def _get_effective_max_entries(self):
        configured_max_entries = max(1, self.dynamic_config.get('POSITION_MAX_ENTRIES', 1))
        sizing_mode = self.dynamic_config.get('POSITION_SIZING_MODE', 'percent_cycle_base')

        if sizing_mode == 'percent_cycle_base':
            percent = self.dynamic_config.get('POSITION_SIZE_PERCENT', 100.0)
            if percent <= 0:
                return 1
            budget_limited_entries = max(1, int((100.0 + 1e-9) // percent))
            return min(configured_max_entries, budget_limited_entries)

        return configured_max_entries

    def _calculate_buy_usdt(self):
        """Calculate entry size in USDT using configured sizing rules."""
        min_order_usdt = self.dynamic_config.get('MIN_ORDER_USDT', 10.0)
        if self.balance_usdt <= 0:
            return None, 'No USDT balance'

        existing_entries = self.current_position.get('entries', 0) if self.current_position else 0
        allow_scale_in = self.dynamic_config.get('POSITION_ALLOW_SCALE_IN', False)
        if self.current_position and not allow_scale_in:
            return None, 'Already holding coin - Scale-in disabled'

        max_entries = self._get_effective_max_entries()
        if existing_entries >= max_entries:
            return None, f'Max entries reached ({max_entries})'

        sizing_mode = self.dynamic_config.get('POSITION_SIZING_MODE', 'percent_cycle_base')

        if sizing_mode == 'fixed_usdt':
            target_entry_usdt = self.dynamic_config.get('POSITION_SIZE_USDT', 100.0)
            usdt_to_use = min(target_entry_usdt, self.balance_usdt)
        else:
            if self.current_position is None:
                # New cycle starts from current free USDT.
                self.cycle_base_balance_usdt = self.balance_usdt
                self.cycle_invested_usdt = 0.0
            elif self.cycle_base_balance_usdt is None:
                # Fallback for safety (e.g. runtime state inconsistency).
                invested = self.current_position.get('invested_usdt', 0.0)
                self.cycle_base_balance_usdt = self.balance_usdt + invested
                self.cycle_invested_usdt = invested

            percent = self.dynamic_config.get('POSITION_SIZE_PERCENT', 100.0)
            target_entry_usdt = self.cycle_base_balance_usdt * (percent / 100.0)
            remaining_cycle_budget = max(0.0, self.cycle_base_balance_usdt - self.cycle_invested_usdt)
            usdt_to_use = min(target_entry_usdt, remaining_cycle_budget, self.balance_usdt)

        if usdt_to_use <= 0:
            return None, 'No available budget for new entry'
        if usdt_to_use < min_order_usdt:
            return None, f'Order size {usdt_to_use:.2f} < min order {min_order_usdt:.2f} USDT'

        return usdt_to_use, None
    
    def buy_coin(self, current_price, reason):
        """Buy coin with available USDT"""
        entry_usdt, sizing_error = self._calculate_buy_usdt()
        if entry_usdt is None:
            print(f'   {sizing_error} - Cannot buy')
            return

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        amount = entry_usdt / current_price

        if self.current_position:
            old_amount = self.current_position['amount']
            old_invested = self.current_position.get('invested_usdt', old_amount * self.current_position['entry_price'])
            new_amount = old_amount + amount
            new_invested = old_invested + entry_usdt

            self.current_position['amount'] = new_amount
            self.current_position['invested_usdt'] = new_invested
            self.current_position['entry_price'] = new_invested / new_amount if new_amount > 0 else current_price
            self.current_position['entries'] = self.current_position.get('entries', 1) + 1
        else:
            self.current_position = {
                'side': 'BUY',
                'entry_price': current_price,
                'amount': amount,
                'invested_usdt': entry_usdt,
                'entries': 1
            }

        # Initialize/update trailing stop tracking
        if self.highest_price_since_entry is None:
            self.highest_price_since_entry = current_price
        else:
            self.highest_price_since_entry = max(self.highest_price_since_entry, current_price)

        # Update balances
        self.balance_coin += amount
        self.balance_usdt -= entry_usdt
        if self.balance_usdt < 1e-10:
            self.balance_usdt = 0.0
        self.cycle_invested_usdt += entry_usdt

        entries = self.current_position.get('entries', 1)
        avg_entry = self.current_position.get('entry_price', current_price)

        # Record trade
        self.trades.append({
            'timestamp': timestamp,
            'type': 'BUY',
            'price': current_price,
            'amount': amount,
            'order_usdt': entry_usdt,
            'entry_number': entries,
            'avg_entry_price': avg_entry,
            'reason': reason,
            'balance_usdt': self.balance_usdt,
            'balance_coin': self.balance_coin
        })

        print(f'\n{timestamp} | ━━━ BUY {self.base_currency} ━━━')
        print(f'   Price: {current_price:.4f}')
        print(f'   Amount: {amount:.2f} {self.base_currency} | Used: {entry_usdt:.2f} USDT')
        print(f'   Avg Entry: {avg_entry:.4f} | Entries: {entries}/{self._get_effective_max_entries()}')
        if self.dynamic_config.get('POSITION_SIZING_MODE') == 'percent_cycle_base' and self.cycle_base_balance_usdt:
            print(f'   Cycle Base: {self.cycle_base_balance_usdt:.2f} USDT | Invested: {self.cycle_invested_usdt:.2f} USDT')
        print(f'   Reason: {reason}')
        print(f'   Balance: {self.balance_usdt:.2f} USDT + {self.balance_coin:.2f} {self.base_currency}')
    
    def sell_coin(self, current_price, reason):
        """Sell all held coin for USDT"""
        # If not holding coin, ignore
        if not self.current_position:
            print('   No coin to sell - Signal ignored')
            return
        
        amount_in_position = self.current_position.get('amount', 0.0)

        # Check if have coin in tracked position
        if amount_in_position <= 0:
            print('   No coin balance - Cannot sell')
            return
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        entry_price = self.current_position['entry_price']
        amount = amount_in_position
        invested_usdt = self.current_position.get('invested_usdt', entry_price * amount)
        entries = self.current_position.get('entries', 1)

        # Calculate PNL
        usdt_received = amount * current_price
        pnl_usdt = usdt_received - invested_usdt
        pnl_pct = (pnl_usdt / invested_usdt) * 100 if invested_usdt > 0 else 0.0

        # Update balances (keep unused USDT that remained outside the position)
        self.balance_usdt += usdt_received
        self.balance_coin = max(0.0, self.balance_coin - amount)
        if self.balance_coin < 1e-10:
            self.balance_coin = 0.0
        self.total_pnl += pnl_usdt
        
        # Clear position
        self.current_position = None
        
        # Reset trailing stop tracking
        self.highest_price_since_entry = None
        self._reset_cycle_tracking()
        
        # Record trade
        self.trades.append({
            'timestamp': timestamp,
            'type': 'SELL',
            'entry_price': entry_price,
            'exit_price': current_price,
            'amount': amount,
            'invested_usdt': invested_usdt,
            'entries': entries,
            'pnl_usdt': pnl_usdt,
            'pnl_pct': pnl_pct,
            'reason': reason,
            'balance_usdt': self.balance_usdt,
            'balance_coin': self.balance_coin
        })
        
        print(f'\n{timestamp} | ━━━ SELL {self.base_currency} ━━━')
        print(f'   Entry: {entry_price:.4f} → Exit: {current_price:.4f}')
        print(f'   Amount: {amount:.2f} {self.base_currency}')
        print(f'   Entries Closed: {entries} | Invested: {invested_usdt:.2f} USDT')
        print(f'   PNL: {pnl_usdt:+.2f} USDT ({pnl_pct:+.2f}%)')
        print(f'   Reason: {reason}')
        print(f'   Balance: {self.balance_usdt:.2f} USDT (Total PNL: {self.total_pnl:+.2f} USDT)')
    
    def save_trade_history(self):
        """Save trade history to file (table format, same as Futures bot)"""
        if not self.trades:
            return
        
        symbol_clean = self.config['SYMBOL'].replace('/', '_').replace('-', '_').lower()
        filename = f'{self.log_dir}/trade_history_{symbol_clean}.txt'
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                # Table header
                f.write(f'{"Timestamp":<20} | {"Type":<6} | {"Side":<6} | {"Entry":<10} | {"Exit":<10} | {"PNL":<15} | {"PNL%":<10} | {"Balance":<15} | {"Reason":<50}\n')
                f.write('-' * 180 + '\n')
                
                for trade in self.trades:
                    timestamp = trade['timestamp']
                    trade_type = trade['type']
                    
                    if trade_type == 'BUY':
                        # BUY
                        side = 'BUY'
                        entry = f"{trade.get('price', 0):.4f}"
                        exit_price = "-"
                        pnl = "-"
                        pnl_pct = "-"
                        balance = f"{trade.get('balance_usdt', 0):.2f}"
                        reason = trade.get('reason', '')
                        
                        f.write(f'{timestamp:<20} | {trade_type:<6} | {side:<6} | {entry:<10} | {exit_price:<10} | {pnl:<15} | {pnl_pct:<10} | {balance:<15} | {reason:<50}\n')
                    
                    elif trade_type == 'SELL':
                        # SELL
                        side = 'SELL'
                        entry = f"{trade.get('entry_price', 0):.4f}"
                        exit_price = f"{trade.get('exit_price', 0):.4f}"
                        pnl = f"{trade.get('pnl_usdt', 0):+.2f}"
                        pnl_pct = f"{trade.get('pnl_pct', 0):+.2f}%"
                        balance = f"{trade.get('balance_usdt', 0):.2f}"
                        reason = trade.get('reason', '')
                        
                        f.write(f'{timestamp:<20} | {trade_type:<6} | {side:<6} | {entry:<10} | {exit_price:<10} | {pnl:<15} | {pnl_pct:<10} | {balance:<15} | {reason:<50}\n')
                
                # Summary
                f.write('\n' + '=' * 180 + '\n')
                f.write(f'SUMMARY:\n')
                f.write(f'Starting Balance: {self.starting_balance_usdt:.2f} USDT\n')
                f.write(f'Current Balance: {self.balance_usdt:.2f} USDT\n')
                f.write(f'Total PNL: {self.total_pnl:+.2f} USDT ({(self.total_pnl/self.starting_balance_usdt*100):+.2f}%)\n')
                
        except Exception as e:
            self._log_error(f'Error saving trade history: {e}')
    
    def run(self):
        """Main trading loop"""
        print('\nStarting trading loop...')
        
        try:
            while True:
                try:
                    # Reload config if changed
                    self._reload_config_if_changed()
                    
                    # Get current price
                    current_price = self.get_price()
                    if current_price is None:
                        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        print(f'{timestamp} | Price unavailable, waiting for next cycle...')
                        time.sleep(self.config['CHECK_INTERVAL_SECONDS'])
                        continue
                    
                    if self.start_price is None:
                        self.start_price = current_price
                        print(f'Start price set: {current_price:.4f}')
                    
                    # Calculate RSI
                    rsi, df = self.calculate_rsi()
                    
                    # Update RSI history (only if RSI is available)
                    if rsi is not None:
                        self.rsi_history.append(rsi)
                    
                    # Detect momentum (only if RSI is available)
                    signal = None
                    reason = None
                    if rsi is not None:
                        signal, reason = self.detect_momentum_change()
                    
                    # Check RSI zone (only if RSI is available)
                    zone = None
                    if rsi is not None:
                        zone = self.check_rsi_zone(rsi)
                    
                    # Display momentum
                    momentum_display = ''
                    if len(self.rsi_history) >= 2:
                        if self.rsi_history[-1] > self.rsi_history[-2]:
                            momentum_display = 'UP'
                        elif self.rsi_history[-1] < self.rsi_history[-2]:
                            momentum_display = 'DOWN'
                    
                    zone_display = f' [{zone}]' if zone else ''
                    
                    # STOP LOSS CHECK
                    if self.dynamic_config['USE_STOP_LOSS'] and self.current_position:
                        entry_price = self.current_position['entry_price']
                        stop_loss_usdt = self.dynamic_config['STOP_LOSS_USDT']
                        
                        price_diff = entry_price - current_price
                        
                        if price_diff >= stop_loss_usdt:
                            unrealized_pnl, unrealized_pnl_pct = self.get_unrealized_pnl(current_price)
                            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            print(f'\n{timestamp} | ⚠️  STOP LOSS TRIGGERED!')
                            print(f'   Entry: {entry_price:.4f} | Current: {current_price:.4f}')
                            print(f'   Price Drop: {price_diff:.4f} USDT (Stop Loss: {stop_loss_usdt:.4f} USDT)')
                            print(f'   Current PNL: {unrealized_pnl:+.2f} USDT ({unrealized_pnl_pct:+.2f}%)')
                            
                            self.sell_coin(current_price, f'Stop Loss: -{price_diff:.4f} USDT')
                            time.sleep(self.config['CHECK_INTERVAL_SECONDS'])
                            continue
                    
                    # TAKE PROFIT CHECK
                    if self.dynamic_config['USE_TAKE_PROFIT'] and self.current_position:
                        entry_price = self.current_position['entry_price']
                        take_profit_usdt = self.dynamic_config['TAKE_PROFIT_USDT']
                        
                        price_gain = current_price - entry_price
                        
                        if price_gain >= take_profit_usdt:
                            unrealized_pnl, unrealized_pnl_pct = self.get_unrealized_pnl(current_price)
                            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            print(f'\n{timestamp} | 🎯 TAKE PROFIT TRIGGERED!')
                            print(f'   Entry: {entry_price:.4f} | Current: {current_price:.4f}')
                            print(f'   Price Gain: {price_gain:.4f} USDT (Take Profit: {take_profit_usdt:.4f} USDT)')
                            print(f'   Current PNL: {unrealized_pnl:+.2f} USDT ({unrealized_pnl_pct:+.2f}%)')
                            
                            self.sell_coin(current_price, f'Take Profit: +{price_gain:.4f} USDT')
                            time.sleep(self.config['CHECK_INTERVAL_SECONDS'])
                            continue
                    
                    # TRAILING STOP CHECK
                    if self.dynamic_config['USE_TRAILING_STOP'] and self.current_position:
                        # Update highest price if current price is higher
                        if current_price > self.highest_price_since_entry:
                            self.highest_price_since_entry = current_price
                        
                        # Calculate trailing stop based on type
                        trailing_type = self.dynamic_config.get('TRAILING_STOP_TYPE', 'percent')
                        
                        if trailing_type == 'usdt':
                            trailing_distance = self.dynamic_config['TRAILING_STOP_USDT']
                            trailing_stop_price = self.highest_price_since_entry - trailing_distance
                        else:  # percent
                            trailing_percent = self.dynamic_config['TRAILING_STOP_PERCENT']
                            trailing_distance = self.highest_price_since_entry * (trailing_percent / 100)
                            trailing_stop_price = self.highest_price_since_entry - trailing_distance
                        
                        # Check if trailing stop hit
                        if current_price <= trailing_stop_price:
                            unrealized_pnl, unrealized_pnl_pct = self.get_unrealized_pnl(current_price)
                            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            print(f'\n{timestamp} | 📉 TRAILING STOP TRIGGERED!')
                            print(f'   Entry: {self.current_position["entry_price"]:.4f}')
                            print(f'   Highest: {self.highest_price_since_entry:.4f}')
                            print(f'   Current: {current_price:.4f}')
                            print(f'   Stop Price: {trailing_stop_price:.4f}')
                            print(f'   Trailing Distance: {trailing_distance:.4f} ({trailing_type})')
                            print(f'   Locked PNL: {unrealized_pnl:+.2f} USDT ({unrealized_pnl_pct:+.2f}%)')
                            
                            if trailing_type == 'usdt':
                                reason = f'Trailing Stop: -{trailing_distance:.4f} USDT from peak'
                            else:
                                reason = f'Trailing Stop: -{trailing_percent:.1f}% from peak'
                            
                            self.sell_coin(current_price, reason)
                            time.sleep(self.config['CHECK_INTERVAL_SECONDS'])
                            continue
                    
                    # Position status
                    if self.current_position:
                        unrealized_pnl, unrealized_pnl_pct = self.get_unrealized_pnl(current_price)
                        position_amount = self.current_position.get('amount', self.balance_coin)
                        position_entries = self.current_position.get('entries', 1)
                        invested_usdt = self.current_position.get(
                            'invested_usdt',
                            self.current_position.get('entry_price', current_price) * position_amount
                        )
                        pos_status = (
                            f'Position: HOLDING {position_amount:.2f} {self.base_currency} '
                            f'@ {self.current_position["entry_price"]:.4f} | Entries: {position_entries} '
                            f'| Invested: {invested_usdt:.2f} USDT | PNL: {unrealized_pnl:+.2f} USDT ({unrealized_pnl_pct:+.2f}%)'
                        )
                        
                        # Trailing Stop status
                        trailing_stop_status = None
                        if self.dynamic_config['USE_TRAILING_STOP']:
                            trailing_type = self.dynamic_config.get('TRAILING_STOP_TYPE', 'percent')
                            
                            if trailing_type == 'usdt':
                                trailing_distance = self.dynamic_config['TRAILING_STOP_USDT']
                                trailing_stop_price = self.highest_price_since_entry - trailing_distance
                            else:  # percent
                                trailing_percent = self.dynamic_config['TRAILING_STOP_PERCENT']
                                trailing_distance = self.highest_price_since_entry * (trailing_percent / 100)
                                trailing_stop_price = self.highest_price_since_entry - trailing_distance
                            
                            # Calculate distance to stop
                            distance_to_stop = current_price - trailing_stop_price
                            distance_pct = (distance_to_stop / current_price) * 100
                            
                            if trailing_type == 'usdt':
                                trailing_stop_status = f'Trailing Stop: {trailing_stop_price:.4f} | Distance: {distance_to_stop:.4f} USDT ({distance_pct:.2f}%) | Highest: {self.highest_price_since_entry:.4f}'
                            else:
                                trailing_stop_status = f'Trailing Stop: {trailing_stop_price:.4f} | Distance: {distance_to_stop:.4f} ({distance_pct:.2f}%) | Highest: {self.highest_price_since_entry:.4f}'
                    else:
                        pos_status = f'Position: USDT {self.balance_usdt:.2f}'
                        trailing_stop_status = None
                    
                    # Volume status
                    volume_status = None
                    if self.dynamic_config['USE_VOLUME_FILTER'] and df is not None:
                        volume_ok, current_volume, avg_volume = self.check_volume_filter(df)
                        
                        if avg_volume is not None and avg_volume > 0:
                            volume_threshold = avg_volume * self.dynamic_config['VOLUME_MULTIPLIER']
                            volume_ratio = current_volume / avg_volume
                            
                            if volume_ok:
                                volume_status = f'Volume: {current_volume:,.0f} | Avg: {avg_volume:,.0f} | Ratio: {volume_ratio:.2f}x ✅'
                            else:
                                volume_status = f'Volume: {current_volume:,.0f} | Avg: {avg_volume:,.0f} | Ratio: {volume_ratio:.2f}x ❌ (Need: {self.dynamic_config["VOLUME_MULTIPLIER"]:.1f}x)'
                    
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    rsi_display = f'{rsi:.2f}' if rsi is not None else 'N/A'
                    print(f'{timestamp} | Price: {current_price:.4f} | RSI: {rsi_display} {momentum_display}{zone_display}')
                    print(f'   {pos_status}')
                    if trailing_stop_status:
                        print(f'   {trailing_stop_status}')
                    if volume_status:
                        print(f'   {volume_status}')
                    
                    # MANUAL CONTROL
                    if self.dynamic_config.get('ENABLE_MANUAL_CONTROL', False):
                        manual_cmd = self.dynamic_config.get('MANUAL_COMMAND')
                        if manual_cmd:
                            # Convert to uppercase for case-insensitive comparison
                            manual_cmd = manual_cmd.upper()
                            print(f'\n⚠️  MANUAL COMMAND DETECTED: {manual_cmd}')
                            
                            if manual_cmd == 'BUY':
                                self.buy_coin(current_price, 'Manual command')
                            elif manual_cmd == 'SELL':
                                self.sell_coin(current_price, 'Manual command')
                            else:
                                print(f'   ❌ Unknown command: {manual_cmd} (use BUY or SELL)')
                            
                            # Clear command
                            self.dynamic_config['MANUAL_COMMAND'] = None
                            with open(self.config_file, 'w', encoding='utf-8') as f:
                                json.dump(self.dynamic_config, f, indent=4, ensure_ascii=False)
                            print(f'   ✅ Command executed')
                            
                            time.sleep(self.config['CHECK_INTERVAL_SECONDS'])
                            continue
                    
                    # TRADING LOGIC
                    if signal:
                        price_ok, price_diff = self.check_price_filter(current_price)
                        
                        if not price_ok:
                            print(f'   Signal: {signal}, but price diff {price_diff:.4f} < {self.dynamic_config["MIN_PRICE_DIFF"]} (NOISE)')
                        else:
                            # Check volume filter (skip if df not available)
                            volume_ok = True
                            if df is not None:
                                volume_ok, current_volume, avg_volume = self.check_volume_filter(df)
                            
                            if not volume_ok:
                                print(f'   Signal: {signal}, but volume {current_volume:.0f} < {avg_volume * self.dynamic_config["VOLUME_MULTIPLIER"]:.0f} (LOW VOLUME)')
                            else:
                                # With zones: BUY only in OVERSOLD, SELL only in OVERBOUGHT
                                if self.dynamic_config['USE_RSI_ZONES']:
                                    expected_zone = 'OVERSOLD' if signal == 'BUY' else 'OVERBOUGHT'
                                    if zone == expected_zone:
                                        print(f'   RSI {zone} ZONE - {signal} signal at {rsi:.2f}')

                                        if signal == 'BUY':
                                            self.buy_coin(current_price, reason)
                                        elif signal == 'SELL':
                                            self.sell_coin(current_price, reason)
                                    else:
                                        zone_name = zone if zone else 'NONE'
                                        print(f'   Signal {signal} ignored (zone: {zone_name}, expected: {expected_zone})')

                                # Without zones - trade anywhere
                                else:
                                    print(f'   {signal} signal at {rsi:.2f}')
                                    
                                    if signal == 'BUY':
                                        self.buy_coin(current_price, reason)
                                    elif signal == 'SELL':
                                        self.sell_coin(current_price, reason)
                    
                    # Save trades
                    if self.trades:
                        self.save_trade_history()
                    
                    time.sleep(self.config['CHECK_INTERVAL_SECONDS'])
                
                except KeyboardInterrupt:
                    print('\nBot stopped by user')
                    self.save_trade_history()
                    break
                except Exception as e:
                    self._log_error(f'Error in main loop: {e}')
                    time.sleep(self.config['CHECK_INTERVAL_SECONDS'])
        
        finally:
            print('\n' + '=' * 80)
            print('⚠️  BOT SHUTDOWN')
            print(f'Final Balance: {self.balance_usdt:.2f} USDT + {self.balance_coin:.2f} {self.base_currency}')
            print(f'Total PNL: {self.total_pnl:+.2f} USDT')
            print('=' * 80)

if __name__ == '__main__':
    bot = RSISpotBot()
    
    try:
        bot.run()
    except KeyboardInterrupt:
        print('\n⚠️  SHUTDOWN SIGNAL RECEIVED (Ctrl+C)')
    except Exception as e:
        print(f'\n❌ FATAL ERROR: {e}')
        traceback.print_exc()
