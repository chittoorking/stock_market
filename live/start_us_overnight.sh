#!/bin/bash
# US Overnight bot — runs at 6:00 PM IST (8:30 AM ET), scans news before US market open
cd /home/ai18developer/news-trading
source venv/bin/activate
python live/us_overnight_bot.py --paper >> live/logs/us_overnight_cron.log 2>&1
