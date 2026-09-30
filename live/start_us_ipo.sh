#!/bin/bash
# US IPO listing day bot — runs same time as US overnight
cd /home/ai18developer/news-trading
source venv/bin/activate
python live/us_ipo_bot.py --paper >> live/logs/us_ipo_cron.log 2>&1
