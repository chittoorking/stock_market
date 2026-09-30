#!/bin/bash
cd /home/ai18developer/trading-bot
source /home/ai18developer/news-trading/venv/bin/activate
python auto_token.py
cp /home/ai18developer/trading-bot/data/indmoney_token.txt /home/ai18developer/news-trading/data/indmoney_token.txt

# Check if token is valid
TOKEN=$(cat /home/ai18developer/news-trading/data/indmoney_token.txt 2>/dev/null)
STATUS=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: $TOKEN" https://api.indstocks.com/user/profile)

TG_TOKEN='8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
TG_CHAT='866752968'

if [ "$STATUS" = "200" ]; then
    curl -s -X POST "https://api.telegram.org/bot$TG_TOKEN/sendMessage"         -H 'Content-Type: application/json'         -d "{\"chat_id\":\"$TG_CHAT\",\"text\":\"✅ Token refreshed. All bots active.\"}" > /dev/null
else
    curl -s -X POST "https://api.telegram.org/bot$TG_TOKEN/sendMessage"         -H 'Content-Type: application/json'         -d "{\"chat_id\":\"$TG_CHAT\",\"text\":\"❌ TOKEN REFRESH FAILED! All bots DEAD. Go to https://www.indstocks.com/app/api-trading/access-tokens and paste token at http://35.238.32.244:8899\"}" > /dev/null
fi

# Refresh scrip codes daily
python3 /home/ai18developer/news-trading/live/refresh_scrips.py >> /home/ai18developer/news-trading/live/logs/scrip_refresh.log 2>&1
