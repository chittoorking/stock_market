#!/bin/bash
pgrep -f entry_timing_logger.py > /dev/null && exit 0
cd /home/ai18developer/news-trading
source venv/bin/activate
python live/entry_timing_logger.py >> live/logs/entry_timing.log 2>&1
