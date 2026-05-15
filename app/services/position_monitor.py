"""
Background position monitor — checks SL/TP hits and auto-closes.
Runs as a periodic background task alongside the main event loop.
"""
from __future__ import annotations

import asyncio
import logging

from app.services.risk_service import risk_service
from app.services.broker_service import broker_service
from app.core.agui_events.emit import emit_risk_alert, emit_text

logger = logging.getLogger(__name__)

MONITOR_INTERVAL = 30  # seconds


async def start_position_monitor():
    """Background task: monitor all positions for SL/TP hits."""
    logger.info("Position monitor started (interval=%ds)", MONITOR_INTERVAL)

    while True:
        try:
            # For now, check positions across a default session
            # In production, iterate over all active sessions
            actions = await risk_service.check_stop_loss_take_profit("default")

            for action in actions:
                symbol = action["symbol"]
                reason = action["reason"]

                logger.warning("Auto-action triggered: %s %s (%s)", action["action"], symbol, reason)

                if action["action"] == "close":
                    try:
                        result = await broker_service.close_position(symbol)
                        await emit_risk_alert("default", {
                            "event_type": reason.upper(),
                            "symbol": symbol,
                            "details": f"Position auto-closed at ₹{action['price']:.2f}. P&L: ₹{result.get('pnl', 0):+,.2f}",
                            "severity": "critical",
                        })
                    except Exception as e:
                        logger.error("Auto-close failed for %s: %s", symbol, e)

        except Exception as e:
            logger.error("Position monitor error: %s", e)

        await asyncio.sleep(MONITOR_INTERVAL)
