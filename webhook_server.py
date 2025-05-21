# webhook_server.py
from flask import Flask, request, jsonify
import logging
import json
import time
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                   filename='tradingview_signals.log', filemode='a')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# For initial testing, we'll just log the signals
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == 'POST':
        try:
            # Receive data from TradingView
            data = json.loads(request.data)
           
            # Log the signal
            logger.info(f"Received signal: {data}")
            print(f"Received signal: {data}")
           
            # Extract information from the message
            symbol = data.get('symbol', 'XRP/USDT')
            signal = data.get('signal')
            price = data.get('price', 0)
           
            # At this stage, we just display the signal
            print(f"Symbol: {symbol}, Signal: {signal}, Price: {price}")
            logger.info(f"Symbol: {symbol}, Signal: {signal}, Price: {price}")
           
            # Successful response
            return jsonify({'success': True, 'message': f'Signal {signal} for {symbol} at {price} processed'})
               
        except Exception as e:
            logger.error(f"Error processing webhook: {str(e)}")
            return jsonify({'success': False, 'message': str(e)})
   
    return jsonify({'success': False, 'message': 'Invalid request method'})

# For server testing
@app.route('/', methods=['GET'])
def index():
    return "TradingView Webhook Server is running!"

if __name__ == '__main__':
    print("TradingView Webhook Server running on http://localhost:5000")
    print("Use ngrok to make it accessible from the internet")
    print("Waiting for signals...")
    app.run(host='0.0.0.0', port=5000, debug=True)