"""
Event emission — routes events to the correct channel.
Dual-path routing: Web (Redis Stream) vs external (Telegram/Slack).
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Any, Optional, List

from app.core.channel_output import (
    get_channel, ChannelType,
    format_signal_message, format_trade_message,
    format_pnl_message, format_risk_alert,
)
from app.core.agui_events.base import (
    BaseEvent, SignalEvent, TradeExecutedEvent, PortfolioEvent,
    RiskAlertEvent, TextMessageEvent, QuickRepliesEvent,
    PositionUpdateEvent, ActivityEvent, ErrorEvent,
)

logger = logging.getLogger(__name__)

# In-memory event queue for SSE streaming (Redis Streams in production)
_event_queues: Dict[str, list] = {}


def _push_event(session_id: str, event: BaseEvent):
    """Push event to the session's event queue (SSE/WebSocket delivery)."""
    if session_id not in _event_queues:
        _event_queues[session_id] = []
    _event_queues[session_id].append(event.to_dict())


def drain_events(session_id: str) -> List[Dict[str, Any]]:
    """Drain all pending events for SSE streaming."""
    events = _event_queues.pop(session_id, [])
    return events


async def emit_text(session_id: str, text: str, quick_replies: Optional[List[Dict]] = None):
    """Emit a text message to the trader."""
    channel = get_channel(session_id)

    if channel == ChannelType.WEB:
        _push_event(session_id, TextMessageEvent(session_id=session_id, content=text))
        if quick_replies:
            _push_event(session_id, QuickRepliesEvent(session_id=session_id, replies=quick_replies))

    elif channel == ChannelType.TELEGRAM:
        from app.services.notification_service import send_telegram
        await send_telegram(text)

    elif channel == ChannelType.SLACK:
        from app.services.notification_service import send_slack
        await send_slack(text)

    elif channel == ChannelType.EMAIL:
        logger.info("Email queued: %s", text[:100])

    elif channel == ChannelType.SMS:
        logger.info("SMS queued: %s", text[:160])


async def emit_signal(session_id: str, signal_data: Dict[str, Any]):
    """Emit a trading signal to all channels."""
    channel = get_channel(session_id)

    # Always push to web dashboard
    _push_event(session_id, SignalEvent(
        session_id=session_id,
        symbol=signal_data.get("symbol", ""),
        strength=signal_data.get("strength", "NEUTRAL"),
        confidence=signal_data.get("confidence", 0.5),
        entry_suggested=signal_data.get("entry_suggested"),
        sl_suggested=signal_data.get("sl_suggested"),
        tp_suggested=signal_data.get("tp_suggested"),
        reason=signal_data.get("reason", ""),
        indicator=signal_data.get("indicator", ""),
        timeframe=signal_data.get("timeframe", ""),
    ))

    # Also send to notification channels
    formatted = format_signal_message(signal_data, ChannelType.TELEGRAM)
    try:
        from app.services.notification_service import send_telegram
        await send_telegram(formatted)
    except Exception as e:
        logger.warning("Telegram notification failed: %s", e)


async def emit_trade_executed(session_id: str, trade_data: Dict[str, Any]):
    """Emit trade execution event."""
    channel = get_channel(session_id)

    _push_event(session_id, TradeExecutedEvent(
        session_id=session_id,
        trade_id=trade_data.get("id", ""),
        symbol=trade_data.get("symbol", ""),
        side=trade_data.get("side", ""),
        quantity=trade_data.get("quantity", 0),
        price=trade_data.get("entry_price", 0),
        order_type=trade_data.get("order_type", "MARKET"),
        status=trade_data.get("status", "FILLED"),
        broker_order_id=trade_data.get("broker_order_id", ""),
    ))

    # Critical event — always notify
    formatted = format_trade_message(trade_data, ChannelType.TELEGRAM)
    try:
        from app.services.notification_service import send_telegram
        await send_telegram(formatted)
    except Exception as e:
        logger.warning("Trade notification failed: %s", e)


async def emit_portfolio(session_id: str, portfolio_data: Dict[str, Any]):
    """Emit portfolio snapshot."""
    _push_event(session_id, PortfolioEvent(
        session_id=session_id,
        positions=portfolio_data.get("positions", []),
        total_pnl=portfolio_data.get("total_pnl", 0),
        daily_pnl=portfolio_data.get("daily_pnl", 0),
        total_exposure=portfolio_data.get("total_exposure", 0),
        cash_balance=portfolio_data.get("cash_balance", 0),
    ))


async def emit_position_update(session_id: str, position_data: Dict[str, Any]):
    """Emit position update."""
    _push_event(session_id, PositionUpdateEvent(
        session_id=session_id,
        symbol=position_data.get("symbol", ""),
        side=position_data.get("side", ""),
        quantity=position_data.get("quantity", 0),
        avg_entry=position_data.get("avg_entry_price", 0),
        current_price=position_data.get("current_price", 0),
        unrealized_pnl=position_data.get("unrealized_pnl", 0),
        stop_loss=position_data.get("stop_loss"),
        take_profit=position_data.get("take_profit"),
    ))


async def emit_risk_alert(session_id: str, alert_data: Dict[str, Any]):
    """Emit risk alert — ALWAYS sends to all channels."""
    _push_event(session_id, RiskAlertEvent(
        session_id=session_id,
        alert_type=alert_data.get("event_type", "UNKNOWN"),
        symbol=alert_data.get("symbol", ""),
        details=alert_data.get("details", ""),
        severity=alert_data.get("severity", "high"),
        action_required=alert_data.get("action_required", True),
    ))

    # Risk alerts always go to all notification channels
    formatted = format_risk_alert(alert_data, ChannelType.TELEGRAM)
    try:
        from app.services.notification_service import send_telegram, send_slack
        await send_telegram(f"🚨 {formatted}")
        await send_slack(f"🚨 {formatted}")
    except Exception as e:
        logger.error("Risk alert notification failed: %s", e)


async def emit_activity(session_id: str, activity: str, is_active: bool = True):
    """Emit loading/activity indicator."""
    _push_event(session_id, ActivityEvent(
        session_id=session_id,
        activity=activity,
        is_active=is_active,
    ))


async def emit_error(session_id: str, error_code: str, message: str, recoverable: bool = True):
    """Emit error event."""
    _push_event(session_id, ErrorEvent(
        session_id=session_id,
        error_code=error_code,
        message=message,
        recoverable=recoverable,
    ))
