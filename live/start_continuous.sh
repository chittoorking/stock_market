#!/bin/bash
cd /home/ai18developer/news-trading
source venv/bin/activate
python live/indian_news_bot.py --continuous >> live/logs/continuous_cron.log 2>&1
