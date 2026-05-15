"""
Multi-channel output — routes events to Web/Telegram/Email/SMS.
Routes responses to the correct transport (WebSocket, Telegram, Email, SMS).
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional, Dict, Any, List
from threading import Lock

logger = logging.getLogger(__name__)


class ChannelType(str, Enum):
    WEB = "web"
    TELEGRAM = "telegram"
    EMAIL = "email"
    SMS = "sms"
    SLACK = "slack"
    DISCORD = "discord"


# Thread-safe channel registry (session_id -> channel)
_channel_registry: Dict[str, ChannelType] = {}
_registry_lock = Lock()


def register_channel(session_id: str, channel: ChannelType):
    with _registry_lock:
        _channel_registry[session_id] = channel


def get_channel(session_id: str) -> ChannelType:
    with _registry_lock:
        return _channel_registry.get(session_id, ChannelType.WEB)


def unregister_channel(session_id: str):
    with _registry_lock:
        _channel_registry.pop(session_id, None)


def is_web_session(session_id: str) -> bool:
    return get_channel(session_id) == ChannelType.WEB


def is_telegram_session(session_id: str) -> bool:
    return get_channel(session_id) == ChannelType.TELEGRAM


# ─── Message Formatters ───

def format_signal_message(signal: Dict[str, Any], channel: ChannelType) -> str:
    """Format a signal for the target channel."""
    symbol = signal.get("symbol", "???")
    strength = signal.get("strength", "NEUTRAL")
    confidence = signal.get("confidence", 0.5)
    entry = signal.get("entry_suggested")
    sl = signal.get("sl_suggested")
    tp = signal.get("tp_suggested")
    reason = signal.get("reason", "")

    if channel == ChannelType.TELEGRAM:
        lines = [
            f"{'🟢' if 'BUY' in strength else '🔴'} *SIGNAL: {symbol}*",
            f"Direction: *{strength}*",
            f"Confidence: {confidence:.0%}",
        ]
        if entry:
            lines.append(f"Entry: `{entry:.2f}`")
        if sl:
            lines.append(f"Stop Loss: `{sl:.2f}`")
        if tp:
            lines.append(f"Take Profit: `{tp:.2f}`")
        if reason:
            lines.append(f"\n_{reason}_")
        return "\n".join(lines)

    elif channel == ChannelType.SLACK:
        return (
            f"{'🟢' if 'BUY' in strength else '🔴'} *{symbol}* — {strength} "
            f"(Confidence: {confidence:.0%})\n"
            f"Entry: {entry or 'Market'} | SL: {sl or 'None'} | TP: {tp or 'None'}\n"
            f">{reason}"
        )

    else:  # WEB, EMAIL, SMS
        lines = [
            f"SIGNAL: {symbol} — {strength} (Confidence: {confidence:.0%})",
        ]
        if entry:
            lines.append(f"Entry: {entry:.2f}")
        if sl:
            lines.append(f"Stop Loss: {sl:.2f}")
        if tp:
            lines.append(f"Take Profit: {tp:.2f}")
        if reason:
            lines.append(f"Reason: {reason}")
        return "\n".join(lines)


def format_trade_message(trade: Dict[str, Any], channel: ChannelType) -> str:
    """Format a trade execution for the target channel."""
    symbol = trade.get("symbol", "???")
    side = trade.get("side", "???")
    qty = trade.get("quantity", 0)
    price = trade.get("entry_price", 0)
    status = trade.get("status", "UNKNOWN")

    if channel == ChannelType.TELEGRAM:
        emoji = "📈" if side == "BUY" else "📉"
        return (
            f"{emoji} *TRADE EXECUTED*\n"
            f"Symbol: *{symbol}*\n"
            f"Side: {side} | Qty: {qty}\n"
            f"Price: `{price:.2f}`\n"
            f"Status: {status}"
        )

    return f"TRADE: {side} {qty} {symbol} @ {price:.2f} — {status}"


def format_pnl_message(pnl_data: Dict[str, Any], channel: ChannelType) -> str:
    """Format P&L report for the target channel."""
    total_pnl = pnl_data.get("total_pnl", 0)
    daily_pnl = pnl_data.get("daily_pnl", 0)
    win_rate = pnl_data.get("win_rate", 0)
    open_positions = pnl_data.get("open_positions", 0)

    pnl_emoji = "✅" if total_pnl >= 0 else "❌"

    if channel == ChannelType.TELEGRAM:
        return (
            f"{pnl_emoji} *PORTFOLIO SUMMARY*\n"
            f"Total P&L: `{total_pnl:+,.2f}`\n"
            f"Daily P&L: `{daily_pnl:+,.2f}`\n"
            f"Win Rate: {win_rate:.0%}\n"
            f"Open Positions: {open_positions}"
        )

    return (
        f"Portfolio: P&L {total_pnl:+,.2f} | Today {daily_pnl:+,.2f} | "
        f"Win Rate {win_rate:.0%} | {open_positions} open"
    )


def format_risk_alert(alert: Dict[str, Any], channel: ChannelType) -> str:
    """Format a risk alert for the target channel."""
    event_type = alert.get("event_type", "UNKNOWN")
    symbol = alert.get("symbol", "")
    details = alert.get("details", "")

    if channel == ChannelType.TELEGRAM:
        return (
            f"🚨 *RISK ALERT: {event_type}*\n"
            f"{'Symbol: *' + symbol + '*' + chr(10) if symbol else ''}"
            f"{details}"
        )

    return f"RISK ALERT: {event_type} {symbol} — {details}"
