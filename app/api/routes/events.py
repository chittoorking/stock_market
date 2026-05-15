"""
Events & Audit API — inspect the event bus, DLQ, audit trail, context graph.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Event Stream ───

@router.get("/recent")
async def get_recent_events(event_type: Optional[str] = None, count: int = 50):
    """Get recent events from the event bus."""
    from app.core.events.event_bus import event_bus
    events = await event_bus.get_recent_events(event_type=event_type, count=count)
    return {"events": [e.to_stream_data() for e in events], "count": len(events)}


@router.get("/chain/{correlation_id}")
async def get_event_chain(correlation_id: str):
    """Get full event chain for a correlation ID (signal → trade → close)."""
    from app.core.events.event_bus import event_bus
    events = await event_bus.get_event_chain(correlation_id)
    return {"events": [e.to_stream_data() for e in events], "count": len(events)}


# ─── DLQ ───

@router.get("/dlq")
async def get_dlq(count: int = 50):
    """Get dead-letter queue entries."""
    from app.core.events.audit import AuditService
    audit = AuditService()
    return await audit.get_dlq_stats()


@router.post("/dlq/{stream_id}/retry")
async def retry_dlq_entry(stream_id: str):
    """Retry a failed event from the DLQ."""
    from app.core.events.audit import AuditService
    audit = AuditService()
    success = await audit.retry_failed(stream_id)
    return {"retried": success}


@router.post("/dlq/retry-all")
async def retry_all_dlq():
    """Retry all failed events."""
    from app.core.events.audit import AuditService
    audit = AuditService()
    count = await audit.retry_all_failed()
    return {"retried": count}


@router.delete("/dlq")
async def flush_dlq():
    """Clear the dead-letter queue."""
    from app.core.events.audit import AuditService
    audit = AuditService()
    await audit.flush_dlq()
    return {"status": "flushed"}


# ─── Audit Trail ───

@router.get("/audit")
async def get_audit(count: int = 100, event_type: Optional[str] = None, session_id: str = "auto"):
    """Get audit trail entries."""
    from app.core.events.audit import AuditService
    audit = AuditService(session_id)
    return {"entries": await audit.get_recent_audit(count, event_type)}


@router.get("/audit/stats")
async def get_audit_stats(session_id: str = "auto"):
    """Get daily event statistics."""
    from app.core.events.audit import AuditService
    audit = AuditService(session_id)
    return await audit.get_daily_stats()


@router.get("/audit/latency")
async def get_latency(session_id: str = "auto"):
    """Get signal-to-trade latency analysis."""
    from app.core.events.audit import AuditService
    audit = AuditService(session_id)
    return await audit.get_signal_to_trade_latency()


# ─── Context Graph ───

@router.get("/context/market")
async def get_market_context(session_id: str = "auto"):
    """Get global market context."""
    from app.core.context_graph import create_context_graph
    graph = create_context_graph(session_id)
    market = await graph.get_market()
    from dataclasses import asdict
    return asdict(market)


@router.get("/context/symbol/{symbol}")
async def get_symbol_context(symbol: str, session_id: str = "auto"):
    """Get full context for a symbol."""
    from app.core.context_graph import create_context_graph
    graph = create_context_graph(session_id)
    ctx = await graph.get_symbol(symbol.upper())
    confluence = await graph.find_confluence(symbol.upper())
    from dataclasses import asdict
    return {"context": asdict(ctx), "confluence": confluence}


@router.get("/context/sector/{sector}")
async def get_sector_context(sector: str, session_id: str = "auto"):
    """Get sector context."""
    from app.core.context_graph import create_context_graph
    graph = create_context_graph(session_id)
    ctx = await graph.get_sector(sector.lower())
    from dataclasses import asdict
    return asdict(ctx)


@router.get("/context/exposure")
async def get_exposure(session_id: str = "auto"):
    """Get full exposure analysis."""
    from app.core.context_graph import create_context_graph
    graph = create_context_graph(session_id)
    return await graph.get_exposure_map()


@router.get("/context/agent-snapshot")
async def get_agent_snapshot(session_id: str = "auto"):
    """Get the full context snapshot the agent sees."""
    from app.core.context_graph import create_context_graph
    graph = create_context_graph(session_id)
    snapshot = await graph.build_agent_context()
    return {"snapshot": snapshot}
