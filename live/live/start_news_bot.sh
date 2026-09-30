#!/bin/bash
# Start the news trading bot — runs at 8:30 AM IST via cron
cd /home/ai18developer/news-trading
source venv/bin/activate
python live/news_bot.py --paper >> live/logs/cron.log 2>&1
