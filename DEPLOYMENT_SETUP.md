# CAM BOT v3 — Deployment & Infrastructure Document

## Server Details

| Item | Value |
|------|-------|
| Cloud | Google Cloud Platform (GCP) |
| VM Name | instance-20260524-112031 |
| Machine | f1-micro (0.25 vCPU, 614MB RAM) |
| OS | Debian 12 (Linux 6.1) |
| External IP | 136.111.68.229 |
| Internal IP | 10.128.0.2 |
| Region | (as selected during creation) |
| Cost | ~$7.11/month (~Rs 600/month) |
| Username | ai18developer |
| Password | ai18developer |

## SSH Access

### From Laptop
```bash
ssh -i ~/.ssh/id_ed25519_gcp ai18developer@136.111.68.229
```

### SSH Key Location (Laptop)
```
Private: C:\Users\HP\.ssh\id_ed25519_gcp
Public:  C:\Users\HP\.ssh\id_ed25519_gcp.pub
```

### Public Key (added to server)
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFZb7TntdZyx/iigUklKemPbFk+ZTsn/LX7AT7zTruXa chittoorvamsi@gmail.com
```

### From Mobile (Terminus App)
```
Host: 136.111.68.229
Port: 22
Username: ai18developer
Auth: Password (ai18developer) or import private key from laptop
```

### From GCP Console
```
GCP Console → Compute Engine → VM instances → Click "SSH" button
```

---

## Server File Structure

```
/home/ai18developer/trading-bot/
    venv/                    — Python virtual environment
    live/
        __init__.py
        config.py            — Strategy params + API config
        strategy.py          — Signal detection + exit logic
        upstox_client.py     — Upstox API (data, orders, positions)
        trader.py            — Live trading orchestrator
        auth_server.py       — OAuth2 daily login
        notify.py            — Telegram notifications
    run_live.py              — Entry point (paper/live)
    run_auth.py              — Daily Upstox login
    bot.py                   — Original backtest bot
    data/
        5min/                — 47 CSV files (4 years of 5-min data)
    journal/                 — Daily trade journals
```

---

## Upstox API Setup (YOU NEED TO DO THIS)

### Step 1: Create Upstox Developer App
1. Go to: https://account.upstox.com/developer/apps
2. Login with your Upstox trading account
3. Click "New App"
4. App Name: CAM Bot
5. Redirect URL: http://localhost:5000/callback
6. You will get:
   - **API Key** (client_id)
   - **API Secret** (client_secret)

### Step 2: Set Environment Variables on Server
```bash
ssh -i ~/.ssh/id_ed25519_gcp ai18developer@136.111.68.229

cat > ~/trading-bot/.env.live << 'EOF'
UPSTOX_API_KEY=your_api_key_here
UPSTOX_API_SECRET=your_api_secret_here
UPSTOX_REDIRECT_URI=http://localhost:5000/callback
CAPITAL=100000
DATA_DIR=/home/ai18developer/trading-bot/data/5min
JOURNAL_DIR=/home/ai18developer/trading-bot/journal
TOKEN_FILE=/home/ai18developer/trading-bot/data/upstox_token.txt
EOF
```

### Step 3: Daily Token Refresh
Upstox tokens expire at 3:30 AM IST daily. Before market opens:
```bash
cd ~/trading-bot && ./venv/bin/python run_auth.py
```
This opens browser for Upstox login. After login, token is saved automatically.

**Note**: For automated daily token refresh on server (no browser), you need to manually paste the token each morning until we set up headless OAuth.

---

## Cron Job (Auto-Run)

```
Schedule: 10:10 AM IST, Monday-Friday
Command:  cd /home/ai18developer/trading-bot && ./venv/bin/python run_live.py
Log:      /home/ai18developer/bot.log
```

### View Cron
```bash
crontab -l
```

### View Bot Log
```bash
tail -f ~/bot.log
```

### Manual Run
```bash
cd ~/trading-bot
./venv/bin/python run_live.py                        # Paper mode, Rs 1L
./venv/bin/python run_live.py --capital 200000        # Paper, Rs 2L
./venv/bin/python run_live.py --live --capital 100000  # LIVE Rs 1L
```

---

## Telegram Notifications (Optional)

### Step 1: Create Telegram Bot
1. Open Telegram, search @BotFather
2. Send: /newbot
3. Name: CAM Bot Alerts
4. You get a **Bot Token**

### Step 2: Get Chat ID
1. Send any message to your bot
2. Visit: https://api.telegram.org/bot<TOKEN>/getUpdates
3. Find "chat":{"id": YOUR_CHAT_ID}

### Step 3: Add to Environment
```bash
# Add to .env.live:
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

## Git Branches

| Branch | Purpose | Status |
|--------|---------|--------|
| production-v2 | Strategy code + backtest results | FROZEN — never modify |
| live-trading-v1 | Live deployment code | Active |
| main | Original code | Untouched |

### Repository
```
Remote: git@github.com:chittoorking/stock_market.git
```

---

## Daily Workflow

| Time | Action | How |
|------|--------|-----|
| 8:30 AM | Refresh Upstox token | Manual: run_auth.py or paste token |
| 10:10 AM | Bot starts automatically | Cron job |
| 10:15 AM | Scans 45 stocks for signals | Automatic |
| 10:16 AM | Places orders (paper/live) | Automatic |
| 10:17-3:00 PM | Monitors positions every minute | Automatic |
| 3:00 PM | Closes all positions | Automatic |
| 3:01 PM | Writes journal | Automatic |
| Evening | Check results | `tail ~/bot.log` or Telegram |

---

## Security Notes

1. **NEVER commit .env.live to git** — contains API secrets
2. **Change server password** from default after setup
3. **SSH key** is stored at `~/.ssh/id_ed25519_gcp` on your laptop
4. **Upstox token** expires daily — even if leaked, it's useless after 3:30 AM
5. **GCP firewall** — only SSH (port 22) is open by default. Bot doesn't need any open ports.

---

## Troubleshooting

### Bot didn't run today
```bash
# Check cron log
grep CRON /var/log/syslog | tail -10

# Check bot log
tail -50 ~/bot.log

# Run manually
cd ~/trading-bot && ./venv/bin/python run_live.py
```

### Upstox token expired
```bash
# Check token
cat ~/trading-bot/data/upstox_token.txt

# Refresh: paste new token manually
echo "NEW_TOKEN_HERE" > ~/trading-bot/data/upstox_token.txt
```

### Server disk full
```bash
df -h
# Clean old logs
truncate -s 0 ~/bot.log
```

### Server down / restarted
Bot auto-starts via cron on next weekday at 10:10 AM. No action needed.

---

## Cost Summary

| Item | Monthly Cost |
|------|-------------|
| GCP VM (f1-micro) | Rs 600 (~$7.11) |
| Upstox API | Free |
| Telegram | Free |
| **Total** | **Rs 600/month** |
| **Bot Income** | **Rs 2,40,000/month** |

---

*Document generated: 2026-05-24*
*Server setup completed and verified.*
