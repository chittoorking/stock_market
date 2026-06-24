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

  token)
    if [ -z "$2" ]; then
      echo ""
      echo "Step 1: Open this URL in browser/phone:"
      echo ""
      echo "  https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id=a44bd36b-ca11-444a-bc27-87ba47b3295a&redirect_uri=https://127.0.0.1:443/callback"
      echo ""
      echo "Step 2: Login to Upstox"
      echo "Step 3: Browser shows error — copy the CODE from URL bar"
      echo "        URL looks like: https://127.0.0.1/callback?code=XXXXXX"
      echo ""
      echo "Step 4: Run: ./server_control.sh token XXXXXX"
      echo ""
    else
      echo "Exchanging code for token..."
      ssh -i "$SSH_KEY" "$VM_USER@$VM_IP" \
        "cd ~/trading-bot && ./venv/bin/python auto_token.py $2"
    fi
    ;;

  check-token)
    ssh -i "$SSH_KEY" "$VM_USER@$VM_IP" \
      "cd ~/trading-bot && ./venv/bin/python auto_token.py"
    ;;

  *)
    echo "CAM BOT v3 — Server Control"
    echo ""
    echo "Usage: $0 {on|off|start|stop|status|token|check-token|ssh|log|today}"
    echo ""
    echo "  on          — Enable bot (auto-runs at 9:15 AM weekdays)"
    echo "  off         — Disable bot (won't trade until you turn ON)"
    echo "  start       — Start bot NOW (manual run)"
    echo "  stop        — Kill running bot"
    echo "  status      — Check if enabled + running"
    echo "  token       — Get daily Upstox token (run every morning)"
    echo "  token CODE  — Save token using auth code"
    echo "  check-token — Verify if current token works"
    echo "  ssh         — Login to server"
    echo "  log         — View last 50 lines of bot log"
    echo "  today       — View today's trade journal"
    ;;
esac
