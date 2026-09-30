#!/bin/bash
# CAS expiry paper trade — runs at 2:30 PM IST on Tue/Thu
cd /home/ai18developer/news-trading
source venv/bin/activate
python live/cas_paper_trade.py --auto >> live/logs/cas_cron.log 2>&1
