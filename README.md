# BTC Buyer Strategy

Buy and HODL strategy scripts for multiple exchanges:
- CoinEx
- Bitfinex
- Hyperliquid (via Katoshi)

## Build Command

```bash
docker build -f Dockerfile-[exchange] -t btc-buyer-[exchange] .
```

Example for Katoshi:
```bash
docker build -f Dockerfile-katoshi -t btc-buyer-katoshi .
```

## Run Command

```bash
docker run --env-file .env btc-buyer-[exchange]
```

## Running Locally

1. Export environment variables:
   ```bash
   export $(grep -v '^#' .env | xargs)
   ```
2. Install requirements:
   ```bash
   pip install -r requirements-[exchange].txt
   ```
3. Run script:
   ```bash
   python3 strategy_[exchange]_fng_ma_buyer.py
   ```

## Configuration

Refer to `envstemplate` for required environment variables.

### Katoshi Specifics
To use the Hyperliquid strategy via Katoshi, you need to set:
- `KATOSHI_API_KEY`: Your Katoshi API key
- `KATOSHI_BOT_ID`: Your Katoshi Bot ID
- `KATOSHI_WEBHOOK_ID`: The ID from your webhook URL (e.g. 1451 from `.../signal?id=1451`)