#!/bin/bash
# Trading bot supervisor — ensures Python process is ALWAYS running during market hours.
# Deploy: cron every minute: * 9-15 * * 1-5 /home/ai18developer/news-trading/live/trading_supervisor.sh
#
# Also used as the main start script:
#   ./trading_supervisor.sh        → starts bot if not running
#   ./trading_supervisor.sh stop   → kills bot gracefully

LOG="/home/ai18developer/news-trading/logs/supervisor.log"
PIDFILE="/tmp/trading_v2.pid"
BOT_DIR="/home/ai18developer/news-trading/trading_v2"
TG_TOKEN='8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
TG_CHAT='866752968'

tg() {
    curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d chat_id="$TG_CHAT" -d text="$1" &>/dev/null
}

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"
}

# Stop command
if [ "$1" = "stop" ]; then
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        kill "$PID" 2>/dev/null
        rm -f "$PIDFILE"
        log "STOPPED pid=$PID"
        tg "Trading bot stopped manually"
    fi
    exit 0
fi

# Check market hours (9:00-15:25 IST, Mon-Fri)
HOUR=$(date +%H)
MIN=$(date +%M)
DOW=$(date +%u)  # 1=Mon, 7=Sun

if [ "$DOW" -gt 5 ]; then
    exit 0  # Weekend
fi
if [ "$HOUR" -lt 9 ] || [ "$HOUR" -gt 15 ]; then
    exit 0  # Outside market hours
fi
if [ "$HOUR" -eq 15 ] && [ "$MIN" -gt 25 ]; then
    exit 0  # After 3:25 PM
fi

# Check if already running
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        # Process alive, check health endpoint
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://localhost:8905/health 2>/dev/null)
        if [ "$HTTP_CODE" = "200" ]; then
            exit 0  # All good
        else
            log "UNHEALTHY: pid=$PID alive but HTTP=$HTTP_CODE — killing"
            kill -9 "$PID" 2>/dev/null
            rm -f "$PIDFILE"
            sleep 2
        fi
    else
        log "DEAD: pid=$PID not running"
        rm -f "$PIDFILE"
    fi
fi

# Start the bot
log "STARTING trading bot..."
cd "$BOT_DIR"
source /home/ai18developer/news-trading/live/.env 2>/dev/null
nohup /home/ai18developer/news-trading/venv/bin/python3 -u -m trading.main --pool 100000 >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
log "STARTED pid=$NEW_PID"
tg "Trading bot (re)started — pid=$NEW_PID at $(date '+%H:%M')"
