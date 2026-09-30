#!/bin/bash
# restart_all.sh — kill old processes, clear locks, start fresh
# Usage: bash restart_all.sh [trading|mcx|dashboard|all]

TARGET=${1:-all}
cd /home/ai18developer/news-trading
source /home/ai18developer/news-trading/venv/bin/activate
export $(grep -v '^#' /home/ai18developer/news-trading/live/.env | xargs 2>/dev/null)

restart_trading() {
    echo "Restarting trading.main..."
    pkill -f "python3 -m trading.main" 2>/dev/null
    sleep 1
    rm -f ~/trading_main.lock
    cd /home/ai18developer/news-trading/trading_v2
    nohup python3 -m trading.main --port 8905 --pool 100000 >> logs/system.log 2>&1 &
    echo "  Started PID $!"
}

restart_mcx() {
    echo "Restarting mcx_commodity_bot..."
    pkill -f "python3 live/mcx_commodity_bot" 2>/dev/null
    sleep 1
    rm -f ~/mcx_bot.lock
    cd /home/ai18developer/news-trading
    nohup python3 live/mcx_commodity_bot.py >> live/logs/mcx_bot_nohup.log 2>&1 &
    echo "  Started PID $!"
}

restart_dashboard() {
    echo "Restarting dashboard..."
    pkill -f "python3 live/trading_dashboard" 2>/dev/null
    sleep 1
    rm -f ~/dashboard.lock
    cd /home/ai18developer/news-trading
    nohup python3 live/trading_dashboard.py >> live/logs/dashboard.log 2>&1 &
    echo "  Started PID $!"
}

case $TARGET in
    trading) restart_trading ;;
    mcx) restart_mcx ;;
    dashboard) restart_dashboard ;;
    all)
        restart_trading
        restart_mcx
        restart_dashboard
        ;;
    *) echo "Usage: $0 [trading|mcx|dashboard|all]" ;;
esac

sleep 2
echo
echo "Running processes:"
ps aux | grep python3 | grep -v grep | grep -v root | grep -v bash | awk '{print "  "$11" "$12" "$13}'
