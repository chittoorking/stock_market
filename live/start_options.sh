#!/bin/bash
pgrep -f indian_news_bot.py > /dev/null && exit 0
cd /home/ai18developer/news-trading
source venv/bin/activate
python live/indian_news_bot.py >> live/logs/options_cron.log 2>&1
