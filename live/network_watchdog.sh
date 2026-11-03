#!/bin/bash
# Network watchdog — runs every 2 minutes via cron
# If DNS fails 3 times, restart networking and log it

LOG="/home/ai18developer/news-trading/logs/network_watchdog.log"
FAILS=0

for i in 1 2 3; do
    if ! ping -c1 -W3 8.8.8.8 &>/dev/null; then
        FAILS=$((FAILS + 1))
    fi
    sleep 1
done

if [ $FAILS -ge 2 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') NETWORK DOWN ($FAILS/3 pings failed) — restarting" >> "$LOG"

    # Try multiple recovery methods
    sudo ip link set ens4 down && sudo ip link set ens4 up
    sleep 2

    if ! ping -c1 -W3 8.8.8.8 &>/dev/null; then
        sudo systemctl restart systemd-networkd
        sleep 3
    fi

    if ! ping -c1 -W3 8.8.8.8 &>/dev/null; then
        sudo dhclient -r ens4 && sudo dhclient ens4
        sleep 3
    fi

    # Check if recovery worked
    if ping -c1 -W3 8.8.8.8 &>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') RECOVERED — network is back" >> "$LOG"

        # Check if trading bot is still healthy after network recovery
        HOUR=$(date +%H)
        if [ "$HOUR" -ge 9 ] && [ "$HOUR" -le 15 ]; then
            sleep 5  # give network a moment to stabilize
            HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://localhost:8905/health 2>/dev/null)
            if [ "$HTTP_CODE" != "200" ]; then
                echo "$(date '+%Y-%m-%d %H:%M:%S') BOT UNHEALTHY after network recovery (HTTP=$HTTP_CODE) — triggering supervisor" >> "$LOG"
                /home/ai18developer/news-trading/live/trading_supervisor.sh
            else
                echo "$(date '+%Y-%m-%d %H:%M:%S') Bot healthy after network recovery" >> "$LOG"
            fi
        fi

        # Send Telegram alert
        TG_TOKEN='8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
        TG_CHAT='866752968'
        MSG="⚠️ GCP network dropped and auto-recovered at $(date '+%H:%M'). Bot status: HTTP=$HTTP_CODE"
        curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d chat_id="$TG_CHAT" -d text="$MSG" &>/dev/null
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') FAILED TO RECOVER — needs manual reboot" >> "$LOG"
        TG_TOKEN='8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
        TG_CHAT='866752968'
        curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d chat_id="$TG_CHAT" -d text="🔴 GCP network DEAD — auto-recovery FAILED. Manual reboot needed!" &>/dev/null
    fi
fi
