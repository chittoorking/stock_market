"""Telegram notifications — get trade alerts on your phone."""
import os
import requests
import logging

log = logging.getLogger('notify')

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')


def send(message):
    """Send message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
        requests.post(url, json={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML',
        }, timeout=10)
    except Exception as e:
        log.error(f'Telegram error: {e}')


def signal_alert(signal):
    """Notify when a signal is found."""
    send(f"<b>SIGNAL: {signal['direction']} {signal['sym']}</b>\n"
         f"Entry: {signal['entry']}\n"
         f"Target: {signal['target']}\n"
         f"Stop: {signal['stop']}")


def trade_alert(direction, sym, entry, qty):
    """Notify when order is placed."""
    send(f"ORDER PLACED: {direction} {qty} {sym} @ {entry}")


def exit_alert(direction, sym, reason, pnl_pct, pnl_rs):
    """Notify when trade exits."""
    emoji = '++' if pnl_rs > 0 else '--'
    send(f"{emoji} EXIT {direction} {sym}\n"
         f"Reason: {reason}\n"
         f"PnL: {pnl_pct:+.2f}% = Rs {pnl_rs:+,.0f}")


def daily_summary(total_trades, wins, daily_pnl, capital):
    """End of day summary."""
    send(f"<b>DAILY SUMMARY</b>\n"
         f"Trades: {total_trades}\n"
         f"Wins: {wins}/{total_trades}\n"
         f"P&L: Rs {daily_pnl:+,.0f}\n"
         f"Capital: Rs {capital:,.0f}")


def error_alert(message):
    """Notify on errors."""
    send(f"ERROR: {message}")
