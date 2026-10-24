#!/bin/bash
echo 'KILLING ALL TRADING BOTS...'
pkill -f trading.main 2>/dev/null
pkill -f silver_scalper 2>/dev/null
pkill -f natgas_scalper 2>/dev/null
pkill -f crude_scalper 2>/dev/null
sleep 2
# Verify
if pgrep -f 'trading.main|scalper' > /dev/null; then
    echo 'FORCE KILLING...'
    pkill -9 -f trading.main 2>/dev/null
    pkill -9 -f silver_scalper 2>/dev/null
    pkill -9 -f natgas_scalper 2>/dev/null
    pkill -9 -f crude_scalper 2>/dev/null
    sleep 1
fi
# Send Telegram alert
curl -s -X POST 'https://api.telegram.org/bot8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs/sendMessage'     -H 'Content-Type: application/json'     -d '{"chat_id":"866752968","text":"KILL SWITCH: All bots stopped."}' > /dev/null
echo 'ALL BOTS STOPPED.'
