buy and hodl strategy script for coinex



command to build 

docker build -t btc-buyer .


command to run 

docker run --env-file {your_local_env_file} btc-buyer -f Dockerfile-{exchange}

if you want to run it without building container:

export $(grep -v '^#' .env | xargs)

pip requirements.txt

python3 strategy_{exchange}_fng_ma_buyer.py