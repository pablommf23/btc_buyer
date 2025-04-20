import requests
import pandas as pd
import os
import time
import hashlib
import hmac
import schedule
import sentry_sdk
from datetime import datetime, timedelta
from sentry_sdk import capture_message, capture_exception

# Initialize Sentry
sentry_dsn = os.environ.get('SENTRY_DSN')
if not sentry_dsn:
    print("Warning: SENTRY_DSN not set. Logging to console only.")
else:
    sentry_sdk.init(dsn=sentry_dsn, traces_sample_rate=1.0)

ma_period = os.environ.get('MA_PERIOD', 730)  # Default to 730 days if not set

def log_message(message, level="info"):
    print(message)
    if sentry_dsn:
        if level == "error":
            capture_message(message, level="error")
        else:
            capture_message(message, level=level)

def get_coinex_price(symbol='BTCUSDT'):
    url = f"https://api.coinex.com/v1/market/ticker?market={symbol}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data['code'] == 0:
            return float(data['data']['ticker']['last'])
        else:
            raise Exception(f"Coinex API error: {data['message']}")
    except Exception as e:
        log_message(f"Failed to fetch Coinex price: {str(e)}", level="error")
        raise

def get_coinex_historical_data(days, symbol='BTCUSDT'):
    cache_file = '/app/btc_usdt_historical.csv'
    end_time = int(time.time())
    start_time = end_time - (days * 86400)  # Days to seconds
    
    # Load existing cache
    try:
        if os.path.exists(cache_file):
            df = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
            df.index = pd.to_datetime(df.index, unit='s')
            latest_date = df.index.max()
            if latest_date >= pd.Timestamp.now() - timedelta(days=1):
                return df
    except Exception as e:
        log_message(f"Failed to load cache: {str(e)}", level="warning")
    
    # Fetch historical data
    url = f"https://api.coinex.com/v1/market/kline?market={symbol}&type=1day&start_time={start_time}&end_time={end_time}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data['code'] != 0:
            raise Exception(f"Coinex API error: {data['message']}")
        
        # Process K-line data: [timestamp, open, high, low, close, volume]
        records = [
            {'timestamp': int(kline[0]), 'close': float(kline[4])}
            for kline in data['data']
        ]
        df = pd.DataFrame(records)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
        df.set_index('timestamp', inplace=True)
        df = df.sort_index()
        
        # Save to cache
        df.to_csv(cache_file)
        return df
    except Exception as e:
        log_message(f"Failed to fetch historical data: {str(e)}", level="error")
        raise

def coinex_buy_order(btc_amount, usdt_amount, api_key, api_secret):
    url = "https://api.coinex.com/v1/order/market"
    timestamp = str(int(time.time() * 1000))
    params = {
        'market': 'BTCUSDT',
        'type': 'buy',
        'amount': str(round(usdt_amount, 8)),
        'client_id': f"strategy6_{timestamp}",
        'access_id': api_key,
        'tonce': timestamp
    }
    
    query = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = hmac.new(
        api_secret.encode('utf-8'),
        query.encode('utf-8'),
        hashlib.md5
    ).hexdigest().upper()
    params['sign'] = signature

    try:
        response = requests.post(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data['code'] == 0:
            return data['data']
        else:
            raise Exception(f"Coinex buy order error: {data['message']}")
    except Exception as e:
        log_message(f"Failed to place Coinex buy order: {str(e)}", level="error")
        raise

def compute_buy_decision():
    current_date = datetime.now().strftime('%Y-%m-%d')
    
    # Fetch Fear and Greed Index
    fng_value = None
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        fng_value = int(data['data'][0]['value'])
    except Exception as e:
        log_message(f"{current_date}: Failed to fetch Fear and Greed Index - {str(e)}", level="warning")
    
    # Fetch historical data for MA
    try:
        btc_df = get_coinex_historical_data(day=ma_period, symbol='BTCUSDT')
        btc_df['MA_2y'] = btc_df['close'].rolling(window=ma_period).mean()
        latest_ma = btc_df['MA_2y'].iloc[-1] if not btc_df.empty else float('nan')
    except Exception as e:
        log_message(f"{current_date}: No purchase - Failed to fetch historical data", level="error")
        return f"{current_date}: No purchase - Failed to fetch historical data"
    
    # Get current BTC/USDT price
    try:
        current_price = get_coinex_price()
    except Exception:
        return f"{current_date}: No purchase - Failed to fetch Coinex price"
    #GET THRESHOLDS

    fng_threshold= os.environ.get('FNG_THRESHOLD_PERCENT')
    ma_threshold = os.environ.get('MA_THRESHOLD_PERCENT')



    # Check conditions
    buy_fng = fng_value is not None and fng_value <= fng_threshold
    buy_ma = current_price <= (1-ma_threshold) * latest_ma if not pd.isna(latest_ma) else False
    overlap = buy_fng and buy_ma
    
    # Get API credentials
    api_key = os.environ.get('COINEX_API_KEY')
    api_secret = os.environ.get('COINEX_API_SECRET')
    buy_overlap_amount = os.environ.get('BUY_OVERLAP_AMOUNT')
    buy_fng_amount = os.environ.get('BUY_FNG_AMOUNT')
    buy_ma_amount = os.environ.get('BUY_MA_AMOUNT')
    if not api_key or not api_secret:
        log_message(f"{current_date}: No purchase - Missing COINEX_API_KEY or COINEX_API_SECRET", level="error")
        return f"{current_date}: No purchase - Missing API credentials"
    
    # Determine purchase
    if overlap:
        btc_amount = float(buy_overlap_amount) if buy_overlap_amount else 0.0002
        reason = "Overlap (Fear and Greed ≤ {fng_threshold} and Price ≥{ma_threshold}% below MA)"
    elif buy_fng:
        btc_amount = float(buy_fng_amount) if buy_fng_amount else 0.0001
        reason = "Fear and Greed {fng_threshold}"
    elif buy_ma:
        btc_amount = float(buy_ma_amount) if buy_ma_amount else 0.0001
        reason = "Price ≥{ma_threshold}% below MA"
    else:
        log_message(f"{current_date}: No purchase - Conditions not met")
        return f"{current_date}: No purchase - Conditions not met"
    
    # Calculate USDT amount
    usdt_amount = btc_amount * current_price
    
    # Place buy order
    try:
        order = coinex_buy_order(btc_amount, usdt_amount, api_key, api_secret)
        log_message(f"{current_date}: Bought {btc_amount} BTC (~{usdt_amount:.2f} USDT) - {reason} (Order ID: {order['id']})")
        return f"{current_date}: Bought {btc_amount} BTC (~{usdt_amount:.2f} USDT) - {reason}"
    except Exception as e:
        log_message(f"{current_date}: Failed to buy {btc_amount} BTC (~{usdt_amount:.2f} USDT) - {reason} (Error: {str(e)})", level="error")
        return f"{current_date}: Failed to buy {btc_amount} BTC (~{usdt_amount:.2f} USDT) - {reason}"

def run_strategy():
    try:
        compute_buy_decision()
    except Exception as e:
        log_message(f"Unexpected error in strategy execution: {str(e)}", level="error")
        capture_exception(e)

def main():
    # Log starting container message
    start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
    trigger_time = os.environ.get('TRIGGER_TIME', 'Not set')
    api_key_set = 'Set' if os.environ.get('COINEX_API_KEY') else 'Not set'
    api_secret_set = 'Set' if os.environ.get('COINEX_API_SECRET') else 'Not set'
    sentry_dsn_set = 'Set' if os.environ.get('SENTRY_DSN') else 'Not set'
    start_message = (
        f"Container started at {start_time}\n"
        f"TRIGGER_TIME: {trigger_time}\n"
        f"COINEX_API_KEY: {api_key_set}\n"
        f"COINEX_API_SECRET: {api_secret_set}\n"
        f"SENTRY_DSN: {sentry_dsn_set}\n"
        f"Moving average period: {ma_period} days\n"
        f"Buy amount for overlap: {os.environ.get('BUY_OVERLAP_AMOUNT', 'Not set')}\n"
        f"Buy amount for FNG: {os.environ.get('BUY_FNG_AMOUNT', 'Not set')}\n"
        f"Buy amount for MA: {os.environ.get('BUY_MA_AMOUNT', 'Not set')}\n"

    )
    log_message(start_message)
    
    # Validate TRIGGER_TIME
    if not trigger_time or not len(trigger_time.split(':')) == 2:
        log_message("Error: TRIGGER_TIME not set or invalid (use HH:MM, e.g., 08:00)", level="error")
        return
    
    try:
        schedule.every().day.at(trigger_time).do(run_strategy)
        log_message(f"Started strategy with daily trigger at {trigger_time} UTC")
    except schedule.ScheduleValueError as e:
        log_message(f"Error: Invalid TRIGGER_TIME format ({trigger_time}): {str(e)}", level="error")
        return
    
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()