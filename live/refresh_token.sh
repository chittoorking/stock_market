#!/bin/bash
cd /home/ai18developer/news-trading
source /home/ai18developer/news-trading/venv/bin/activate
python3 /home/ai18developer/news-trading/live/auto_token.py

# Refresh scrip codes daily
python3 /home/ai18developer/news-trading/live/refresh_scrips.py >> /home/ai18developer/news-trading/live/logs/scrip_refresh.log 2>&1
