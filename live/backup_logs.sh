#!/bin/bash
# Daily log backup — run via cron at 11:30 PM IST
DATE=20261024
BACKUP_DIR=/home/ai18developer/news-trading/live/backups/
mkdir -p 

# Trading logs
cp /home/ai18developer/news-trading/live/logs/trading_v2.log /trading_v2.log 2>/dev/null
cp /home/ai18developer/news-trading/live/logs/trading_.log / 2>/dev/null
cp /home/ai18developer/news-trading/live/logs/audit_.jsonl / 2>/dev/null

# ML signal data (94 fields per stock per trade)
cp /home/ai18developer/news-trading/live/vwap_logs/ml_signals_.jsonl / 2>/dev/null

# Token and scrip data
cp /home/ai18developer/news-trading/data/indmoney_token.txt / 2>/dev/null

# Position state
cp /home/ai18developer/news-trading/trading_v2/data/positions.json / 2>/dev/null

# Multyfi seen signals
cp /home/ai18developer/multyfi_seen.json / 2>/dev/null

# Compress old backups (older than 7 days)
find /home/ai18developer/news-trading/live/backups -maxdepth 1 -type d -mtime +7 -exec tar czf {}.tar.gz {} \; -exec rm -rf {} \; 2>/dev/null

echo Sat Oct 24 19:05:29 IST 2026: Backup done — 18G
