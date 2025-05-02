FROM python:3.9-slim

WORKDIR /app

RUN pip install --upgrade pip

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY strategy_coinex_fng_ma_buyer.py .

CMD ["python", "strategy_coinex_fng_ma_buyer.py"]