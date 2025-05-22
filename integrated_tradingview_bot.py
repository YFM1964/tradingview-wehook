# integrated_tradingview_bot.py
import ccxt
import time
import json
import os
import numpy as np
import pandas as pd
from datetime import datetime
import logging
import threading
import queue
from flask import Flask, request, jsonify

# Logging setup
def setup_logger(bot_id="default"):
    # Create logs directory if it doesn't exist
    if not os.path.exists('logs'):
        os.makedirs('logs')
  
    # Error log
    error_logger = logging.getLogger(f'error_logger_{bot_id}')
    error_logger.setLevel(logging.ERROR)
    error_handler = logging.FileHandler(f'logs/error_log_{bot_id}.txt')
    error_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    if not error_logger.handlers:
        error_logger.addHandler(error_handler)

    # Trade log
    trade_logger = logging.getLogger(f'trade_logger_{bot_id}')
    trade_logger.setLevel(logging.INFO)
    trade_handler = logging.FileHandler(f'logs/trade_log_{bot_id}.txt')
    trade_formatter = logging.Formatter('%(asctime)s - %(message)s')
    trade_handler.setFormatter(trade_formatter)
    if not trade_logger.handlers:
        trade_logger.addHandler(trade_handler)
  
    # Signal log
    signal_logger = logging.getLogger(f'signal_logger_{bot_id}')
    signal_logger.setLevel(logging.INFO)
    signal_handler = logging.FileHandler(f'logs/signal_log_{bot_id}.txt')
    signal_formatter = logging.Formatter('%(asctime)s - %(message)s')
    signal_handler.setFormatter(signal_formatter)
    if not signal_logger.handlers:
        signal_logger.addHandler(signal_handler)
  
    # Webhook log
    webhook_logger = logging.getLogger(f'webhook_logger_{bot_id}')
    webhook_logger.setLevel(logging.INFO)
    webhook_handler = logging.FileHandler(f'logs/webhook_log_{bot_id}.txt')
    webhook_formatter = logging.Formatter('%(asctime)s - %(message)s')
    webhook_handler.setFormatter(webhook_formatter)
    if not webhook_logger.handlers:
        webhook_logger.addHandler(webhook_handler)
  
    # Create trade history file if it doesn't exist
    if not os.path.exists(f'logs/trade_history_{bot_id}.txt'):
        with open(f'logs/trade_history_{bot_id}.txt', 'w') as f:
            f.write("Timestamp | Type | Price | Amount | Total | Profit/Loss\n")
            f.write("-" * 70 + "\n")
  
    return error_logger, trade_logger, signal_logger, webhook_logger

class TradingViewBot:
    def __init__(self, api_key=None, api_secret=None, api_passphrase=None, symbol='XRP/USDT',
                 demo_mode=True, bot_id=None, fixed_amount=1000, webhook_port=5000):
        """
        Initialize the TradingView Bot that receives signals from TradingView webhooks
      
        Args:
            api_key: Kucoin API key
            api_secret: Kucoin API secret
            api_passphrase: Kucoin API passphrase
            symbol: Trading pair (e.g. 'XRP/USDT')
            demo_mode: Run in demo mode (simulation) or real mode
            bot_id: Unique identifier for this bot instance
            fixed_amount: Fixed amount of base currency to trade
            webhook_port: Port for webhook server
        """
        self.symbol = symbol
        self.demo_mode = demo_mode
        self.base_currency = symbol.split('/')[0]  # XRP in XRP/USDT
        self.quote_currency = symbol.split('/')[1]  # USDT in XRP/USDT
        self.fixed_amount = fixed_amount  # Fixed amount to trade
        self.webhook_port = webhook_port
      
        # Generate bot ID if not provided
        if bot_id is None:
            self.bot_id = self.symbol.replace('/', '_').lower()
        else:
            self.bot_id = bot_id
          
        # Setup loggers for this bot instance
        self.error_logger, self.trade_logger, self.signal_logger, self.webhook_logger = setup_logger(self.bot_id)
      
        # Queue for receiving signals from webhook
        self.signal_queue = queue.Queue()
        
        # Demo or real mode
        if demo_mode:
            self.exchange = ccxt.kucoin()  # Demo mode, but we'll fetch real market data
            print(f"[{self.bot_id}] Bot is running in DEMO mode (simulation). No real trades will be executed.")
        else:
            if not api_key or not api_secret or not api_passphrase:
                raise ValueError("API key, secret, and passphrase are required for real mode")
          
            self.exchange = ccxt.kucoin({
                'apiKey': api_key,
                'secret': api_secret,
                'password': api_passphrase,
                'enableRateLimit': True
            })
            print(f"[{self.bot_id}] Bot is running in REAL mode. REAL trades will be executed!")
      
        # Current bot state (was the last action a buy or sell)
        self.last_action = "BUY"  # Start with BUY so it can receive SELL signal first
      
        # Track original amount sold to buy back exactly the same amount
        self.last_sold_amount = None
      
        # Simulated balances for demo mode
        self.demo_balance = {
            self.base_currency: self.fixed_amount,  # Fixed amount as you mentioned
            self.quote_currency: 0                 # 0 quote currency initially
        }
      
        # Profit history
        self.total_profit = 0
        self.trades = []
        
        # Flask app for webhook
        self.app = Flask(__name__)
        self.setup_webhook_routes()
      
        print(f"[{self.bot_id}] TradingView Bot initialized")
        print(f"[{self.bot_id}] Trading pair: {symbol}")
        print(f"[{self.bot_id}] Fixed trading amount: {fixed_amount} {self.base_currency}")
        print(f"[{self.bot_id}] Webhook will run on port: {webhook_port}")
        print(f"[{self.bot_id}] Last action: {self.last_action}")

    def setup_webhook_routes(self):
        """Setup Flask routes for webhook"""
        
        @self.app.route('/webhook', methods=['POST'])
        def webhook():
            if request.method == 'POST':
                try:
                    # Log raw request data
                    raw_data = request.get_data()
                    self.webhook_logger.info(f"Raw webhook data received: {raw_data}")
                    
                    # Try to parse JSON data
                    try:
                        data = request.get_json()
                        if not data:
                            # If no JSON, try to get raw text
                            data = raw_data.decode('utf-8').strip()
                        
                        self.webhook_logger.info(f"Parsed webhook data: {data}")
                        print(f"[{self.bot_id}] Received webhook data: {data}")
                        
                        # Process the signal
                        signal_processed = self.process_tradingview_signal(data)
                        
                        if signal_processed:
                            return jsonify({'success': True, 'message': 'Signal processed successfully'})
                        else:
                            return jsonify({'success': False, 'message': 'Signal not processed'})
                            
                    except json.JSONDecodeError:
                        # Handle plain text signals
                        signal_text = raw_data.decode('utf-8').strip()
                        self.webhook_logger.info(f"Plain text signal received: {signal_text}")
                        print(f"[{self.bot_id}] Received plain text signal: {signal_text}")
                        
                        signal_processed = self.process_tradingview_signal(signal_text)
                        
                        if signal_processed:
                            return jsonify({'success': True, 'message': 'Signal processed successfully'})
                        else:
                            return jsonify({'success': False, 'message': 'Signal not processed'})
                        
                except Exception as e:
                    error_msg = f"Error processing webhook: {str(e)}"
                    self.webhook_logger.error(error_msg)
                    self.error_logger.error(error_msg)
                    print(f"[{self.bot_id}] ERROR: {error_msg}")
                    return jsonify({'success': False, 'message': str(e)})
            
            return jsonify({'success': False, 'message': 'Invalid request method'})
        
        @self.app.route('/', methods=['GET'])
        def index():
            return f"TradingView Webhook Server is running for {self.bot_id}!"
        
        @self.app.route('/status', methods=['GET'])
        def status():
            current_price = self.get_current_price()
            base_balance = self.get_balance(self.base_currency)
            quote_balance = self.get_balance(self.quote_currency)
            
            status_info = {
                'bot_id': self.bot_id,
                'symbol': self.symbol,
                'current_price': current_price,
                'last_action': self.last_action,
                'base_balance': base_balance,
                'quote_balance': quote_balance,
                'total_profit': self.total_profit,
                'demo_mode': self.demo_mode
            }
            
            return jsonify(status_info)

    def process_tradingview_signal(self, data):
        """
        Process signal received from TradingView webhook
        
        Args:
            data: Signal data from TradingView (can be JSON object or plain text)
            
        Returns:
            bool: True if signal was processed, False otherwise
        """
        try:
            signal_type = None
            
            # Handle different data formats
            if isinstance(data, dict):
                # JSON format: {"action": "BUY"} or similar
                signal_type = data.get('action', '').upper()
                if not signal_type:
                    signal_type = data.get('signal', '').upper()
                    
            elif isinstance(data, str):
                # Plain text format: "BUY", "SELL", "LONG", "SHORT"
                data_upper = data.upper().strip()
                
                if data_upper in ['BUY', 'LONG']:
                    signal_type = 'BUY'
                elif data_upper in ['SELL', 'SHORT']:
                    signal_type = 'SELL'
                else:
                    # Try to extract from JSON string
                    try:
                        parsed_data = json.loads(data)
                        if isinstance(parsed_data, dict):
                            signal_type = parsed_data.get('action', '').upper()
                            if not signal_type:
                                signal_type = parsed_data.get('signal', '').upper()
                    except:
                        pass
            
            if not signal_type:
                self.webhook_logger.warning(f"Could not extract signal type from data: {data}")
                return False
            
            # Add signal to queue for processing
            signal_info = {
                'type': signal_type,
                'timestamp': datetime.now(),
                'raw_data': data
            }
            
            self.signal_queue.put(signal_info)
            
            signal_msg = f"TradingView signal received: {signal_type}"
            print(f"[{self.bot_id}] {signal_msg}")
            self.signal_logger.info(signal_msg)
            
            return True
            
        except Exception as e:
            error_msg = f"Error processing TradingView signal: {str(e)}"
            self.error_logger.error(error_msg)
            print(f"[{self.bot_id}] ERROR: {error_msg}")
            return False

    def get_current_price(self):
        """Get current price of the trading pair"""
        try:
            ticker = self.exchange.fetch_ticker(self.symbol)
            return ticker['last']
        except Exception as e:
            error_msg = f"Error getting current price: {str(e)}"
            print(f"[{self.bot_id}] ERROR: {error_msg}")
            self.error_logger.error(error_msg)
            return None
  
    def get_balance(self, currency):
        """Get balance of a currency"""
        if self.demo_mode:
            return self.demo_balance.get(currency, 0)
      
        try:
            balance = self.exchange.fetch_balance()
            return balance[currency]['free']
        except Exception as e:
            error_msg = f"Error getting {currency} balance: {str(e)}"
            print(f"[{self.bot_id}] ERROR: {error_msg}")
            self.error_logger.error(error_msg)
            return 0
  
    def execute_trade(self, side, amount, price):
        """
        Execute a trade (buy or sell)
      
        In demo mode, it's just simulated
        In real mode, a real trade is executed
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
      
        if self.demo_mode:
            # Simulate trade in demo mode
            if side == 'buy':
                cost = amount * price
                if cost > self.demo_balance[self.quote_currency]:
                    print(f"[{self.bot_id}] WARNING: Not enough {self.quote_currency} for buy. Have: {self.demo_balance[self.quote_currency]:.6f}, Need: {cost:.6f}")
                    return None
              
                self.demo_balance[self.quote_currency] -= cost
                self.demo_balance[self.base_currency] += amount
                print(f"[{self.bot_id}] [DEMO] BUY {amount:.6f} {self.base_currency} at {price:.6f} {self.quote_currency}")
              
                # Record trade
                trade = {
                    'timestamp': timestamp,
                    'side': 'BUY',
                    'price': price,
                    'amount': amount,
                    'cost': cost,
                    'fee': 0  # Zero fee in demo mode for now
                }
              
            else:  # sell
                if amount > self.demo_balance[self.base_currency]:
                    print(f"[{self.bot_id}] WARNING: Not enough {self.base_currency} for sell. Have: {self.demo_balance[self.base_currency]:.6f}, Need: {amount:.6f}")
                    return None
              
                revenue = amount * price
                self.demo_balance[self.base_currency] -= amount
                self.demo_balance[self.quote_currency] += revenue
                print(f"[{self.bot_id}] [DEMO] SELL {amount:.6f} {self.base_currency} at {price:.6f} {self.quote_currency}")
              
                # Record trade
                trade = {
                    'timestamp': timestamp,
                    'side': 'SELL',
                    'price': price,
                    'amount': amount,
                    'cost': revenue,
                    'fee': 0  # Zero fee in demo mode for now
                }
          
            self.trades.append(trade)
          
            # Log the trade to file
            with open(f'logs/trade_history_{self.bot_id}.txt', 'a') as f:
                f.write(f"{timestamp} | {trade['side']} | {trade['price']:.6f} | {trade['amount']:.6f} | {trade['cost']:.6f} | \n")
          
            return trade
      
        else:
            # Execute real trade
            try:
                if side == 'buy':
                    order = self.exchange.create_market_order(self.symbol, side, amount)
                    print(f"[{self.bot_id}] BUY {amount:.6f} {self.base_currency} at {price:.6f} {self.quote_currency}")
                  
                    # Log the trade to file
                    with open(f'logs/trade_history_{self.bot_id}.txt', 'a') as f:
                        f.write(f"{timestamp} | BUY | {price:.6f} | {amount:.6f} | {amount * price:.6f} | \n")
                  
                    return order
                else:  # sell
                    order = self.exchange.create_market_order(self.symbol, side, amount)
                    print(f"[{self.bot_id}] SELL {amount:.6f} {self.base_currency} at {price:.6f} {self.quote_currency}")
                  
                    # Log the trade to file
                    with open(f'logs/trade_history_{self.bot_id}.txt', 'a') as f:
                        f.write(f"{timestamp} | SELL | {price:.6f} | {amount:.6f} | {amount * price:.6f} | \n")
                  
                    return order
          
            except Exception as e:
                error_msg = f"Error executing {side} trade: {str(e)}"
                print(f"[{self.bot_id}] ERROR: {error_msg}")
                self.error_logger.error(error_msg)
                return None
  
    def calculate_profit(self, sell_price, buy_price, amount):
        """
        Calculate profit from a complete sell-buy cycle
      
        Args:
            sell_price: Price at which we sold
            buy_price: Price at which we bought back
            amount: Amount of base currency traded
          
        Returns:
            profit: Profit in quote currency
        """
        # Sell revenue
        sell_revenue = amount * sell_price
      
        # Buy cost
        buy_cost = amount * buy_price
      
        # Profit in quote currency
        profit = sell_revenue - buy_cost
      
        # Log the cycle completion and profit to file
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(f'logs/trade_history_{self.bot_id}.txt', 'a') as f:
            f.write(f"{timestamp} | CYCLE | {sell_price:.6f}->{buy_price:.6f} | {amount:.6f} | {sell_revenue:.6f} | {profit:.6f}\n")
            f.write("-" * 70 + "\n")
      
        # Log to trade logger for analysis
        trade_log_msg = f"CYCLE COMPLETE: SELL@{sell_price:.6f} BUY@{buy_price:.6f} AMOUNT:{amount:.6f} PROFIT:{profit:.6f} {self.quote_currency}"
        self.trade_logger.info(trade_log_msg)
      
        self.total_profit += profit
        print(f"[{self.bot_id}] Cycle profit: {profit:.6f} {self.quote_currency}")
        print(f"[{self.bot_id}] Total profit: {self.total_profit:.6f} {self.quote_currency}")
      
        return profit

    def run_webhook_server(self):
        """Run the Flask webhook server"""
        print(f"[{self.bot_id}] Starting webhook server on port {self.webhook_port}")
        self.app.run(host='0.0.0.0', port=self.webhook_port, debug=False, threaded=True)

    def run_trading_loop(self):
        """Main trading loop that processes signals from the queue"""
        print(f"[{self.bot_id}] Starting trading loop...")
        print(f"[{self.bot_id}] Waiting for TradingView signals...")
        
        last_sell_price = None
        
        try:
            while True:
                try:
                    # Check for new signals with timeout
                    signal_info = self.signal_queue.get(timeout=1)
                    
                    signal_type = signal_info['type']
                    timestamp = signal_info['timestamp']
                    
                    print(f"[{self.bot_id}] Processing signal: {signal_type} received at {timestamp}")
                    
                    current_price = self.get_current_price()
                    if current_price is None:
                        print(f"[{self.bot_id}] Could not get current price. Skipping signal.")
                        continue
                    
                    base_balance = self.get_balance(self.base_currency)
                    quote_balance = self.get_balance(self.quote_currency)
                    
                    status_msg = (f"[{self.bot_id}] Current Price: {current_price:.6f} | "
                                 f"{self.base_currency}: {base_balance:.6f} | "
                                 f"{self.quote_currency}: {quote_balance:.6f} | "
                                 f"Last Action: {self.last_action}")
                    print(status_msg)
                    
                    # Trading logic with enforced alternating pattern
                    if signal_type == "BUY" and self.last_action == "SELL":
                        # Buy signal after Sell
                        print(f"[{self.bot_id}] BUY Signal from TradingView at price {current_price:.6f}")
                        
                        # Buy back exactly the same amount we sold earlier
                        if self.last_sold_amount:
                            buy_amount = self.last_sold_amount
                            buy_cost = buy_amount * current_price
                            
                            if quote_balance >= buy_cost:
                                buy_trade = self.execute_trade('buy', buy_amount, current_price)
                                if buy_trade:
                                    self.last_action = "BUY"
                                    
                                    # Calculate profit if we have the sell price
                                    if last_sell_price:
                                        self.calculate_profit(last_sell_price, current_price, buy_amount)
                            else:
                                print(f"[{self.bot_id}] Insufficient {self.quote_currency} balance for buying. Need {buy_cost:.6f}, have {quote_balance:.6f}")
                        else:
                            print(f"[{self.bot_id}] No previous sell amount recorded, cannot determine buy amount")
                    
                    elif signal_type == "SELL" and self.last_action == "BUY":
                        # Sell signal after Buy
                        print(f"[{self.bot_id}] SELL Signal from TradingView at price {current_price:.6f}")
                        
                        # Sell fixed amount (or less if not enough balance)
                        sell_amount = min(self.fixed_amount, base_balance)
                        
                        if sell_amount > 0:
                            sell_trade = self.execute_trade('sell', sell_amount, current_price)
                            if sell_trade:
                                self.last_action = "SELL"
                                self.last_sold_amount = sell_amount
                                last_sell_price = current_price
                        else:
                            print(f"[{self.bot_id}] Insufficient {self.base_currency} balance for selling")
                    
                    else:
                        if signal_type == "BUY" and self.last_action == "BUY":
                            print(f"[{self.bot_id}] Buy signal received but we already bought (waiting for sell signal)")
                        elif signal_type == "SELL" and self.last_action == "SELL":
                            print(f"[{self.bot_id}] Sell signal received but we already sold (waiting for buy signal)")
                        else:
                            print(f"[{self.bot_id}] Signal {signal_type} received but doesn't follow alternating pattern (last: {self.last_action})")
                    
                    # Mark signal as processed
                    self.signal_queue.task_done()
                    
                except queue.Empty:
                    # No signal received, continue waiting
                    continue
                    
        except KeyboardInterrupt:
            print(f"\n[{self.bot_id}] Trading loop stopped by user")
        except Exception as e:
            error_msg = f"Unexpected error in trading loop: {str(e)}"
            print(f"[{self.bot_id}] ERROR: {error_msg}")
            self.error_logger.error(error_msg)

    def run(self):
        """
        Run the complete bot: webhook server + trading loop
        """
        print(f"[{self.bot_id}] Starting TradingView Bot...")
        print(f"[{self.bot_id}] Log files will be saved in logs/ directory")
        print(f"[{self.bot_id}] Webhook URL: http://your-server.com:{self.webhook_port}/webhook")
        
        try:
            # Start webhook server in a separate thread
            webhook_thread = threading.Thread(target=self.run_webhook_server, daemon=True)
            webhook_thread.start()
            
            # Give the webhook server time to start
            time.sleep(2)
            
            # Run trading loop in main thread
            self.run_trading_loop()
            
        except KeyboardInterrupt:
            print(f"\n[{self.bot_id}] Bot stopped by user")
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            print(f"[{self.bot_id}] ERROR: {error_msg}")
            self.error_logger.error(error_msg)
        finally:
            print(f"\n[{self.bot_id}] Performance Summary:")
            print(f"[{self.bot_id}] Total profit: {self.total_profit:.6f} {self.quote_currency}")
            print(f"[{self.bot_id}] Trade history saved to 'logs/trade_history_{self.bot_id}.txt'")
            print(f"[{self.bot_id}] Trade log saved to 'logs/trade_log_{self.bot_id}.txt'")
            print(f"[{self.bot_id}] Signal log saved to 'logs/signal_log_{self.bot_id}.txt'")
            print(f"[{self.bot_id}] Webhook log saved to 'logs/webhook_log_{self.bot_id}.txt'")
            print(f"[{self.bot_id}] Error log saved to 'logs/error_log_{self.bot_id}.txt'")

if __name__ == "__main__":
    # Configuration - change these for your actual run
    API_KEY = ""        # Your Kucoin API key
    API_SECRET = ""     # Your Kucoin API secret
    API_PASSPHRASE = "" # Your Kucoin API passphrase
    SYMBOL = "XRP/USDT" # Trading pair
    
    DEMO_MODE = True     # True for demo mode, False for real trading
    FIXED_AMOUNT = 1000  # Fixed amount to trade (1000 XRP)
    WEBHOOK_PORT = 5000  # Port for webhook server
    
    # Initialize bot
    bot = TradingViewBot(
        api_key=API_KEY,
        api_secret=API_SECRET,
        api_passphrase=API_PASSPHRASE,
        symbol=SYMBOL,
        demo_mode=DEMO_MODE,
        fixed_amount=FIXED_AMOUNT,
        webhook_port=WEBHOOK_PORT
    )
    
    # Run bot
    bot.run()
