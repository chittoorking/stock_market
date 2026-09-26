#!/bin/bash
cd /home/ai18developer/news-trading/trading_v2
source /home/ai18developer/news-trading/venv/bin/activate
export $(grep -v '^#' /home/ai18developer/news-trading/live/.env | xargs)

# Check if already running
PROC=$(pgrep -f "trading.main")
if [ -n "$PROC" ]; then
    echo "$(date): Already running (PID $PROC), skipping start" >> logs/system.log
    exit 0
fi

# Start fresh
nohup python3 -m trading.main --port 8905 --pool 100000 >> logs/system.log 2>&1 &
echo "$(date): Started trading system (PID $!)" >> logs/system.log
