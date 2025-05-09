import requests
import pandas as pd
import os
import time
import hashlib
import hmac
import schedule
import sentry_sdk
from datetime import datetime, timedelta
from requests.exceptions import RequestException
from sentry_sdk import capture_message, capture_exception
import uuid
import json

# Initialize Sentry
sentry_dsn = os.environ.get('SENTRY_DSN')
if not sentry_dsn:
    print("Warning: SENTRY_DSN not set. Logging to console only.")
else:
    sentry_sdk.init(dsn=sentry_dsn, traces_sample_rate=1.0)

# Configuration
API_VERSION = 'v2'  # Use API v2
ma_period = int(os.environ.get('MA_PERIOD_DAYS', 730))  # Default to 730 days

def log_message(message, level="info"):
    """Log message to console and Sentry if configured."""
    print(f"[{level.upper()}] {message}")
    if sentry_dsn:
        capture_message(message, level=level)

def validate_env_vars():
    """Validate required environment variables."""
    required_vars = ['COINEX_API_KEY', 'COINEX_API_SECRET', 'TRIGGER_TIME', 'FNG_THRESHOLD_PERCENT', 'MA_THRESHOLD_PERCENT']
    missing = [var for var in required_vars if not os.environ.get(var)]
    if missing:
        log_message(f"Error: Missing environment variables: {', '.join(missing)}", level="error")
        return False
    return True

def get_coinex_price(symbol='BTCUSDT', retries=3, delay=5):
    """Fetch current price from CoinEx API v2."""
    url = f"https://api.coinex.com/v2/spot/ticker?market={symbol}"
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            data = response.json()
            if data['code'] != 0:
                raise Exception(f"Coinex API error: {data['message']}")
            # Access the 'last' price from the first ticker
            return float(data['data'][0]['last'])
        except Exception as e:
            log_message(f"Attempt {attempt+1}/{retries} to fetch price failed: {str(e)}", level="warning")
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                log_message(f"Failed to fetch Coinex price: {str(e)}", level="error")
                raise

def get_coinex_historical_data(days=ma_period, symbol='BTCUSDT', retries=3, delay=5):
    """Fetch historical K-line data from CoinEx API v2."""
    cache_file = './btc_usdt_historical.csv'
    
    # Load cache if recent and sufficient
    try:
        if os.path.exists(cache_file):
            df = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
            df.index = pd.to_datetime(df.index, unit='s')
            latest_date = df.index.max()
            if len(df) >= days and (pd.Timestamp.now() - latest_date).days < 1:
                log_message(f"Using cached historical data from {cache_file} with {len(df)} points")
                return df
            else:
                log_message(f"Cache outdated or insufficient ({len(df)} points, needed {days}), fetching new data", level="info")
                os.remove(cache_file)
    except Exception as e:
        log_message(f"Failed to load cache: {str(e)}", level="warning")

    # Fetch historical data
    url = f"https://api.coinex.com/v2/spot/kline?market={symbol}&period=1day&limit={days}"
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            data = response.json()
            if data['code'] != 0:
                raise Exception(f"Coinex API error: {data['message']}")
            if not data['data']:
                raise Exception("No historical data returned")
            
            records = [
                {'timestamp': int(kline['created_at'] / 1000), 'close': float(kline['close'])}
                for kline in data['data']
            ]
            df = pd.DataFrame(records)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
            df.set_index('timestamp', inplace=True)
            df = df.sort_index()
            
            log_message(f"Fetched {len(df)} historical data points")
            if len(df) < days:
                log_message(f"Warning: Only {len(df)} data points available, needed {days}", level="warning")

            # Save to cache
            try:
                os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                df.to_csv(cache_file)
                log_message(f"Saved historical data to {cache_file}")
            except Exception as e:
                log_message(f"Failed to save cache: {str(e)}", level="warning")

            return df
        except Exception as e:
            log_message(f"Attempt {attempt+1}/{retries} to fetch historical data failed: {str(e)}", level="warning")
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                log_message(f"Failed to fetch historical data: {str(e)}", level="error")
                raise

def coinex_buy_order(btc_amount, api_key, api_secret, retries=3, delay=5):
    """Place a market buy order on CoinEx using API v2."""
    url = "https://api.coinex.com/v2/spot/order"
    for attempt in range(retries):
        try:
            timestamp = str(int(time.time() * 1000))  # Milliseconds since epoch
            params = {
                'market': 'BTCUSDT',
                'side': 'buy',
                'type': 'market',
                'market_type': 'SPOT',
                'amount': str(round(btc_amount, 8)),
                'client_id': f"strategy6_{uuid.uuid4().hex[:8]}",  # Unique client ID
                'tonce': timestamp
            }

            # 1. Sort parameters alphabetically (CRITICAL for signature)
            query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
            print(f"Query string for signature: {query_string}")
            # 2. Generate signature (HMAC-SHA256, uppercase)
            signature = hmac.new(
                api_secret.encode('utf-8'),
                query_string.encode('utf-8'),
                hashlib.sha256
            ).hexdigest().upper()
            print(f"Generated signature: {signature}")
            # 3. Set headers (EXACT names and Content-Type)
            headers = {
                'X-COINEX-ACCESS-ID': api_key,
                'X-COINEX-TONCE': timestamp,
                'X-COINEX-SIGN': signature,
                'Content-Type': 'application/json'  # Important!
            }
            print(f"Headers: {headers}")
            # 4. Send POST request with params as JSON body
            response = requests.post(url, headers=headers, data=json.dumps(params), timeout=20)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            data = response.json()

            if data['code'] == 0:
                return data['data']  # Order details
            raise Exception(f"CoinEx buy order error: {data['message']}")

        except Exception as e:
            log_message(f"Attempt {attempt+1}/{retries} to place buy order failed: {str(e)}", level="warning")
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                log_message(f"Failed to place CoinEx buy order: {str(e)}", level="error")
                raise

def compute_buy_decision():
    """Compute buy decision based on FNG and MA conditions."""
    current_date = datetime.now().strftime('%Y-%m-%d')
    log_message(f"{current_date}: Starting buy decision computation", level="info")

    # Fetch Fear and Greed Index
    fng_value = None
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()
        fng_value = int(data['data'][0]['value'])
        log_message(f"{current_date}: FNG value: {fng_value}")
    except Exception as e:
        log_message(f"{current_date}: Failed to fetch Fear and Greed Index - {str(e)}", level="warning")

    # Fetch historical data for MA
    try:
        btc_df = get_coinex_historical_data()
        if btc_df.empty:
            raise Exception("Historical data is empty")
        
        # Adjust ma_period if insufficient data
        effective_ma_period = min(ma_period, len(btc_df))
        if effective_ma_period < ma_period:
            log_message(f"{current_date}: Adjusted MA period to {effective_ma_period} due to insufficient data ({len(btc_df)} points)", level="warning")
        
        btc_df['MA'] = btc_df['close'].rolling(window=effective_ma_period).mean()
        latest_ma = btc_df['MA'].iloc[-1]
        if pd.isna(latest_ma):
            raise Exception(f"Moving average calculation failed: {len(btc_df)} points available, needed {effective_ma_period}")
        log_message(f"{current_date}: Latest MA ({effective_ma_period} days): {latest_ma:.2f}")
    except Exception as e:
        log_message(f"{current_date}: No purchase - Failed to fetch or process historical data: {str(e)}", level="error")
        return f"{current_date}: No purchase - Failed to fetch or process historical data"

    # Get current BTC/USDT price
    try:
        current_price = get_coinex_price()
        log_message(f"{current_date}: Current BTC/USDT price: {current_price:.2f}")
    except Exception as e:
        log_message(f"{current_date}: No purchase - Failed to fetch price: {str(e)}", level="error")
        return f"{current_date}: No purchase - Failed to fetch price"

    # Get thresholds
    try:
        fng_threshold = float(os.environ.get('FNG_THRESHOLD_PERCENT', 25))
        ma_threshold = float(os.environ.get('MA_THRESHOLD_PERCENT', 0.1))  # 10% below MA
    except ValueError as e:
        log_message(f"{current_date}: No purchase - Invalid threshold values: {str(e)}", level="error")
        return f"{current_date}: No purchase - Invalid threshold values"

    # Check conditions
    buy_fng = fng_value is not None and fng_value <= fng_threshold
    buy_ma = current_price <= (1 - ma_threshold) * latest_ma
    overlap = buy_fng and buy_ma
    log_message(f"{current_date}: Buy conditions - FNG: {buy_fng}, MA: {buy_ma}, Overlap: {overlap}")

    # Get API credentials and buy amounts
    api_key = '40EC2CF286374A0AB0E2096230AF300C'  #os.environ.get('COINEX_API_KEY')
    api_secret = 'EC7251EE5C287BFCF642DC3597542084E9176EAC2903D97B' #os.environ.get('COINEX_API_SECRET')
    buy_overlap_amount = os.environ.get('BUY_OVERLAP_AMOUNT')
    buy_fng_amount = os.environ.get('BUY_FNG_AMOUNT')
    buy_ma_amount = os.environ.get('BUY_MA_AMOUNT')

    if not api_key or not api_secret:
        log_message(f"{current_date}: No purchase - Missing COINEX_API_KEY or COINEX_API_SECRET", level="error")
        return f"{current_date}: No purchase - Missing API credentials"

    # Determine purchase
    try:
        if overlap:
            btc_amount = float(buy_overlap_amount) if buy_overlap_amount else 0.0002
            reason = f"Overlap (Fear and Greed ≤ {fng_threshold}, Price ≥ {ma_threshold*100}% below MA)"
        elif buy_fng:
            btc_amount = float(buy_fng_amount) if buy_fng_amount else 0.0001
            reason = f"Fear and Greed ≤ {fng_threshold}"
        elif buy_ma:
            btc_amount = float(buy_ma_amount) if buy_ma_amount else 0.0005
            reason = f"Price ≥ {ma_threshold*100}% below MA"
        else:
            log_message(f"{current_date}: No purchase - Conditions not met")
            return f"{current_date}: No purchase - Conditions not met"

        if btc_amount <= 0:
            raise ValueError("Buy amount must be positive")
    except ValueError as e:
        log_message(f"{current_date}: No purchase - Invalid buy amount: {str(e)}", level="error")
        return f"{current_date}: No purchase - Invalid buy amount"

    # Calculate USDT amount (for logging only, not used in order)
    usdt_amount = btc_amount * current_price
    log_message(f"{current_date}: Planning to buy {btc_amount} BTC (~{usdt_amount:.2f} USDT) - {reason}")

    # Place buy order
    try:
        order = coinex_buy_order(btc_amount, api_key, api_secret)
        log_message(f"{current_date}: Bought {btc_amount} BTC (~{usdt_amount:.2f} USDT) - {reason} (Order ID: {order['id']})")
        return f"{current_date}: Bought {btc_amount} BTC (~{usdt_amount:.2f} USDT) - {reason}"
    except Exception as e:
        log_message(f"{current_date}: Failed to buy {btc_amount} BTC (~{usdt_amount:.2f} USDT) - {reason} (Error: {str(e)})", level="error")
        return f"{current_date}: Failed to buy {btc_amount} BTC (~{usdt_amount:.2f} USDT) - {reason}"

def run_strategy():
    """Run the trading strategy."""
    try:
        result = compute_buy_decision()
        log_message(result)
    except Exception as e:
        log_message(f"Unexpected error in strategy execution: {str(e)}", level="error")
        capture_exception(e)

def main():
    """Main function to start the strategy."""
    if not validate_env_vars():
        return

    start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
    trigger_time = os.environ.get('TRIGGER_TIME', 'Not set')
    start_message = (
        f"Container started at {start_time}\n"
        f"TRIGGER_TIME: {trigger_time}\n"
        f"COINEX_API_KEY: {'Set' if os.environ.get('COINEX_API_KEY') else 'Not set'}\n"
        f"COINEX_API_SECRET: {'Set' if os.environ.get('COINEX_API_SECRET') else 'Not set'}\n"
        f"SENTRY_DSN: {'Set' if sentry_dsn else 'Not set'}\n"
        f"Moving average period: {ma_period} days\n"
        f"Buy amount for overlap: {os.environ.get('BUY_OVERLAP_AMOUNT', 'Not set')}\n"
        f"Buy amount for FNG: {os.environ.get('BUY_FNG_AMOUNT', 'Not set')}\n"
        f"Buy amount for MA: {os.environ.get('BUY_MA_AMOUNT', 'Not set')}\n"
        f"FNG threshold: {os.environ.get('FNG_THRESHOLD_PERCENT', 'Not set')}\n"
        f"MA threshold: {os.environ.get('MA_THRESHOLD_PERCENT', 'Not set')}"
    )
    log_message(start_message)

    if not trigger_time or len(trigger_time.split(':')) != 2:
        log_message("Error: TRIGGER_TIME not set or invalid (use HH:MM, e.g., 08:00)", level="error")
        return

    try:
        schedule.every().day.at(trigger_time).do(run_strategy)
        log_message(f"Started strategy with daily trigger at {trigger_time} UTC")
    except schedule.ScheduleValueError as e:
        log_message(f"Error: Invalid TRIGGER_TIME format ({trigger_time}): {str(e)}", level="error")
        return

    run_strategy()  # Run immediately on startup
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()