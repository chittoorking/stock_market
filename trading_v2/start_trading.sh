#!/bin/bash
# Kill any existing trading.main process (but not ourselves)
MYPID=$$
for pid in $(pgrep -f "python.*trading.main"); do
    if [ "$pid" != "$MYPID" ] && [ "$pid" != "$PPID" ]; then
        kill "$pid" 2>/dev/null
        echo "Killed old trading process: $pid"
    fi
done
sleep 2

cd /home/ai18developer/news-trading/trading_v2
exec /home/ai18developer/news-trading/venv/bin/python3 -u -m trading.main
