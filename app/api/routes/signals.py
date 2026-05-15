"""
Signals webhook — receive external trading signals (TradingView, custom scanners).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.session_context import push_signal
from app.core.conversation_state import transition_state, TradingState
from app.core.agui_events.emit import emit_signal

logger = logging.getLogger(__name__)
router = APIRouter()


class ExternalSignal(BaseModel):
    """External signal payload (e.g., from TradingView webhook)."""
    symbol: str
    side: str  # "BUY" or "SELL"
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    confidence: float = 0.7
    source: str = "external"
    timeframe: Optional[str] = "1h"
    reason: Optional[str] = ""


@router.post("/webhook/{session_id}")
async def receive_signal(session_id: str, signal: ExternalSignal):
    """
    Receive an external trading signal.
    TradingView alerts, custom scanners, etc. can push signals here.
    """
    import uuid

    signal_data = {
        "id": str(uuid.uuid4())[:12],
        "symbol": signal.symbol.upper(),
        "strength": f"{'STRONG_' if signal.confidence > 0.8 else ''}{signal.side.upper()}",
        "confidence": signal.confidence,
        "source": signal.source,
        "timeframe": signal.timeframe,
        "entry_suggested": signal.entry,
        "sl_suggested": signal.stop_loss,
        "tp_suggested": signal.take_profit,
        "reason": signal.reason or f"External signal from {signal.source}",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Queue the signal
    await push_signal(session_id, signal_data)

    # Emit to all channels
    await emit_signal(session_id, signal_data)

    # Transition state
    await transition_state(
        session_id,
        TradingState.SIGNAL_DETECTED,
        reason=f"external_signal_{signal.source}",
        metadata={"signal_id": signal_data["id"]},
    )

    logger.info(
        "External signal received: %s %s from %s (session=%s)",
        signal.side, signal.symbol, signal.source, session_id,
    )

    return {"status": "queued", "signal_id": signal_data["id"]}


@router.get("/pending/{session_id}")
async def get_pending_signals(session_id: str):
    """Get all pending signals for a session."""
    from app.core.session_context import get_pending_signals
    signals = await get_pending_signals(session_id)
    return {"signals": signals, "count": len(signals)}
