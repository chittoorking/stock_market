#!/bin/bash
# Only restart if process is truly dead, not just slow
PROC=$(pgrep -f "trading.main")
if [ -z "$PROC" ]; then
    echo "$(date): Process dead, restarting" >> /home/ai18developer/news-trading/trading_v2/logs/system.log
    /home/ai18developer/news-trading/trading_v2/start.sh
else
    HEALTH=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://localhost:8905/health 2>/dev/null)
    if [ "$HEALTH" != "200" ]; then
        echo "$(date): Health check failed (code=$HEALTH) but process $PROC alive — NOT restarting" >> /home/ai18developer/news-trading/trading_v2/logs/system.log
    fi
fi
