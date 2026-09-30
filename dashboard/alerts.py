"""Telegram + Email alert service."""
import json
import smtplib
import logging
from email.mime.text import MIMEText
from urllib.request import urlopen, Request
from . import db

log = logging.getLogger("dashboard")


def send_telegram(message):
    """Send Telegram message."""
    cfg = db.get_alert_config()
    token = cfg.get("telegram_token", "")
    chat_id = cfg.get("telegram_chat_id", "")
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        urlopen(req, timeout=10)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


def send_email(subject, body):
    """Send email alert."""
    cfg = db.get_alert_config()
    smtp = cfg.get("email_smtp", "")
    user = cfg.get("email_user", "")
    passwd = cfg.get("email_pass", "")
    to = cfg.get("email_to", "")
    if not all([smtp, user, passwd, to]):
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = f"[Trading Bot] {subject}"
        msg["From"] = user
        msg["To"] = to
        host, port = smtp.split(":") if ":" in smtp else (smtp, "587")
        server = smtplib.SMTP(host, int(port))
        server.starttls()
        server.login(user, passwd)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        log.error(f"Email send failed: {e}")


def alert_trade(bot, symbol, side, price, pnl=None, reason=None):
    """Alert on trade entry/exit."""
    if pnl is not None:
        emoji = "+" if pnl >= 0 else ""
        msg = f"{'EXIT' if reason else 'CLOSE'} {bot.upper()} | {symbol} {side} @ ${price:.2f} | P&L: {emoji}${pnl:.2f} | {reason or ''}"
    else:
        msg = f"ENTRY {bot.upper()} | {symbol} {side} @ ${price:.2f}"
    send_telegram(msg)


def alert_kill_switch():
    """Alert on kill switch activation."""
    send_telegram("KILL SWITCH ACTIVATED — All bots stopped!")


def alert_bot_crash(bot_name, error=""):
    """Alert on bot crash."""
    send_telegram(f"BOT CRASH: {bot_name} — {error}")


def alert_daily_summary(summaries):
    """Send daily P&L summary."""
    lines = ["<b>Daily Trading Summary</b>\n"]
    for s in summaries:
        emoji = "+" if s.get("total_pnl", 0) >= 0 else ""
        lines.append(
            f"<b>{s['bot']}</b>: {s.get('total_trades', 0)} trades, "
            f"WR {s.get('wins', 0)}/{s.get('total_trades', 0)}, "
            f"P&L: {emoji}${s.get('total_pnl', 0):.2f}"
        )
    send_telegram("\n".join(lines))
