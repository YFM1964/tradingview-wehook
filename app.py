from integrated_tradingview_bot import TradingViewBot
import os

# Configuration from environment variables or defaults
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
API_PASSPHRASE = os.getenv("API_PASSPHRASE", "")
SYMBOL = os.getenv("SYMBOL", "XRP/USDT")
DEMO_MODE = os.getenv("DEMO_MODE", "True").lower() == "true"
FIXED_AMOUNT = float(os.getenv("FIXED_AMOUNT", "1000"))
WEBHOOK_PORT = int(os.getenv("PORT", "5000"))

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

# Flask app for gunicorn
app = bot.app

if __name__ == "__main__":
    bot.run()

