#!/bin/bash
cd /home/ai18developer/news-trading
source venv/bin/activate
python live/us_news_bot_v2.py --scan-only >> live/logs/us_news_cron.log 2>&1
