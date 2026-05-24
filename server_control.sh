#!/bin/bash
# ═══ GCP VM Control — Start/Stop the trading server ═══
# Usage:
#   ./server_control.sh start   — Turn ON (before market)
#   ./server_control.sh stop    — Turn OFF (after market)
#   ./server_control.sh status  — Check if running
#   ./server_control.sh ssh     — Login to server
#   ./server_control.sh log     — View today's bot log

VM_IP="136.111.68.229"
SSH_KEY="$HOME/.ssh/id_ed25519_gcp"
VM_USER="ai18developer"

case "$1" in
  on)
    echo "Enabling bot (will auto-run at 10:10 AM on weekdays)..."
    ssh -i "$SSH_KEY" "$VM_USER@$VM_IP" "touch ~/trading-bot/ENABLED && echo 'BOT ENABLED'"
    ;;

  off)
    echo "Disabling bot + killing if running..."
    ssh -i "$SSH_KEY" "$VM_USER@$VM_IP" \
      "rm -f ~/trading-bot/ENABLED; pkill -f run_live.py 2>/dev/null; echo 'BOT DISABLED'"
    ;;

  start)
    echo "Starting bot NOW..."
    ssh -i "$SSH_KEY" "$VM_USER@$VM_IP" \
      "touch ~/trading-bot/ENABLED && cd ~/trading-bot && nohup ./venv/bin/python run_live.py >> ~/bot.log 2>&1 &"
    echo "Bot started on $VM_IP"
    ;;

  stop)
    echo "Stopping bot (keeps enabled for tomorrow)..."
    ssh -i "$SSH_KEY" "$VM_USER@$VM_IP" \
      "pkill -f run_live.py 2>/dev/null; echo 'Bot stopped'"
    ;;

  status)
    ssh -i "$SSH_KEY" "$VM_USER@$VM_IP" "
      echo '=== KILL SWITCH ==='
      [ -f ~/trading-bot/ENABLED ] && echo 'ENABLED (will trade)' || echo 'DISABLED (will NOT trade)'
      echo ''
      echo '=== BOT PROCESS ==='
      ps aux | grep run_live | grep -v grep && echo 'RUNNING' || echo 'NOT RUNNING'
      echo ''
      echo '=== LAST LOG ==='
      tail -5 ~/bot.log 2>/dev/null || echo 'No log yet'
    "
    ;;

  ssh)
    ssh -i "$SSH_KEY" "$VM_USER@$VM_IP"
    ;;

  log)
    ssh -i "$SSH_KEY" "$VM_USER@$VM_IP" "tail -50 ~/bot.log"
    ;;

  today)
    DATE=$(date +%Y-%m-%d)
    ssh -i "$SSH_KEY" "$VM_USER@$VM_IP" "cat ~/trading-bot/journal/$DATE.json 2>/dev/null || echo 'No journal for today'"
    ;;

  *)
    echo "CAM BOT v3 — Server Control"
    echo ""
    echo "Usage: $0 {on|off|start|stop|status|ssh|log|today}"
    echo ""
    echo "  on     — Enable bot (auto-runs at 10:10 AM weekdays)"
    echo "  off    — Disable bot (won't trade until you turn ON)"
    echo "  start  — Start bot NOW (manual run)"
    echo "  stop   — Kill running bot (keeps enabled for tomorrow)"
    echo "  status — Check if enabled + running"
    echo "  ssh    — Login to server"
    echo "  log    — View last 50 lines of bot log"
    echo "  today  — View today's trade journal"
    ;;
esac
