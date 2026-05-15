"""
Audit Trail & DFQ (Dead-Letter / Failed Queue) management.

Every event that flows through the system is audited.
Every failure is captured, categorized, and available for retry.

The audit trail answers:
- "Show me every event for trade XYZ from signal to close"
- "What failed in the last hour and why?"
- "How many signals were generated vs taken vs skipped?"
- "What's the latency from signal to execution?"

The DFQ answers:
- "Which events failed to process?"
- "Can we retry them?"
- "Is there a pattern in failures?" (same handler? same symbol? same time?)
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from app.core.redis import get_redis_client
from app.core.events.event_bus import DLQ_KEY, Event

logger = logging.getLogger(__name__)


class AuditService:
    """Query and manage the audit trail and DFQ."""

    def __init__(self, session_id: str = "auto"):
        self.session_id = session_id

    # ─── Audit Trail Queries ───

    async def get_event_chain(self, correlation_id: str) -> List[Dict]:
        """
        Get the full event chain for a correlation ID.
        E.g., signal_id → all events from signal generation to trade close.
        """
        client = await get_redis_client()
        results = await client.xrange(f"audit:{self.session_id}")

        chain = []
        for msg_id, data in results:
            decoded = {
                k if isinstance(k, str) else k.decode():
                v if isinstance(v, str) else v.decode()
                for k, v in data.items()
            }
            if decoded.get("correlation") == correlation_id:
                chain.append(decoded)

        return chain

    async def get_recent_audit(self, count: int = 100, event_type: Optional[str] = None) -> List[Dict]:
        """Get recent audit entries, optionally filtered by type."""
        client = await get_redis_client()
        results = await client.xrevrange(f"audit:{self.session_id}", count=count)

        entries = []
        for msg_id, data in results:
            decoded = {
                k if isinstance(k, str) else k.decode():
                v if isinstance(v, str) else v.decode()
                for k, v in data.items()
            }
            if event_type and decoded.get("type") != event_type:
                continue
            entries.append(decoded)

        return entries

    async def get_daily_stats(self) -> Dict[str, Any]:
        """Get summary statistics for today's events."""
        entries = await self.get_recent_audit(count=10000)

        stats = {
            "total_events": len(entries),
            "by_type": {},
            "signals_generated": 0,
            "trades_executed": 0,
            "positions_closed": 0,
            "risk_alerts": 0,
            "errors": 0,
        }

        for entry in entries:
            event_type = entry.get("type", "UNKNOWN")
            stats["by_type"][event_type] = stats["by_type"].get(event_type, 0) + 1

            if event_type == "SIGNAL_GENERATED":
                stats["signals_generated"] += 1
            elif event_type == "TRADE_EXECUTED":
                stats["trades_executed"] += 1
            elif event_type == "POSITION_CLOSED":
                stats["positions_closed"] += 1
            elif event_type == "RISK_LIMIT_HIT":
                stats["risk_alerts"] += 1
            elif event_type == "ERROR":
                stats["errors"] += 1

        # Signal-to-trade conversion rate
        if stats["signals_generated"] > 0:
            stats["conversion_rate"] = stats["trades_executed"] / stats["signals_generated"]
        else:
            stats["conversion_rate"] = 0

        return stats

    # ─── DFQ Management ───

    async def get_dlq_entries(self, count: int = 50) -> List[Dict]:
        """Get entries from the dead-letter queue."""
        client = await get_redis_client()
        results = await client.xrange(DLQ_KEY, count=count)

        entries = []
        for msg_id, data in results:
            decoded = {
                k if isinstance(k, str) else k.decode():
                v if isinstance(v, str) else v.decode()
                for k, v in data.items()
            }
            decoded["stream_id"] = msg_id if isinstance(msg_id, str) else msg_id.decode()
            entries.append(decoded)

        return entries

    async def get_dlq_stats(self) -> Dict[str, Any]:
        """Get DLQ statistics — failure patterns."""
        entries = await self.get_dlq_entries(count=500)

        stats = {
            "total_failures": len(entries),
            "by_handler": {},
            "by_error": {},
            "recent_failures": entries[:5],
        }

        for entry in entries:
            handler = entry.get("handler", "unknown")
            error = entry.get("error", "unknown")[:100]

            stats["by_handler"][handler] = stats["by_handler"].get(handler, 0) + 1
            stats["by_error"][error] = stats["by_error"].get(error, 0) + 1

        return stats

    async def retry_failed(self, stream_id: str) -> bool:
        """Retry a specific failed event."""
        from app.core.events.event_bus import event_bus
        return await event_bus.retry_dlq_entry(stream_id)

    async def retry_all_failed(self) -> int:
        """Retry all failed events."""
        entries = await self.get_dlq_entries(count=100)
        retried = 0
        for entry in entries:
            sid = entry.get("stream_id", "")
            if sid and await self.retry_failed(sid):
                retried += 1
        return retried

    async def flush_dlq(self):
        """Clear the DLQ (after investigation)."""
        from app.core.events.event_bus import event_bus
        await event_bus.flush_dlq()

    # ─── Latency Analysis ───

    async def get_signal_to_trade_latency(self, count: int = 50) -> Dict[str, Any]:
        """
        Measure latency from signal generation to trade execution.
        Important for understanding if the bot is fast enough.
        """
        # Get recent trade events
        trades = await self.get_recent_audit(count=count * 2, event_type="TRADE_EXECUTED")
        signals = await self.get_recent_audit(count=count * 5, event_type="SIGNAL_GENERATED")

        # Build signal lookup
        signal_times = {}
        for s in signals:
            sig_id = s.get("correlation", "")
            if sig_id:
                signal_times[sig_id] = s.get("timestamp", "")

        latencies = []
        for t in trades:
            corr_id = t.get("correlation", "")
            if corr_id in signal_times:
                try:
                    sig_time = datetime.fromisoformat(signal_times[corr_id])
                    trade_time = datetime.fromisoformat(t.get("timestamp", ""))
                    latency_ms = (trade_time - sig_time).total_seconds() * 1000
                    latencies.append(latency_ms)
                except Exception:
                    pass

        if not latencies:
            return {"avg_ms": 0, "min_ms": 0, "max_ms": 0, "p95_ms": 0, "count": 0}

        latencies.sort()
        p95_idx = int(len(latencies) * 0.95)

        return {
            "avg_ms": sum(latencies) / len(latencies),
            "min_ms": latencies[0],
            "max_ms": latencies[-1],
            "p95_ms": latencies[p95_idx] if p95_idx < len(latencies) else latencies[-1],
            "count": len(latencies),
        }
