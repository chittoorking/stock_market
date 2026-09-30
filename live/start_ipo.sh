#!/bin/bash
# IPO listing day trader — runs at 9:55 AM, waits for 10 AM listing
cd /home/ai18developer/news-trading
source venv/bin/activate
python live/ipo_trader.py >> live/logs/ipo_cron.log 2>&1
