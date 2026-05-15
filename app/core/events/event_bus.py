"""
Event Bus — Redis Streams-based event-driven architecture.

Replaces polling with real-time event propagation:

    MARKET_TICK → runs strategy scanners → SIGNAL_GENERATED
    SIGNAL_GENERATED → aggregator scores → SIGNAL_SCORED
    SIGNAL_SCORED → agent decides → TRADE_DECISION (taken/skipped)
    TRADE_DECISION (taken) → broker executes → TRADE_EXECUTED
    TRADE_EXECUTED → risk updates → RISK_UPDATED
    TRADE_EXECUTED → context graph updates → CONTEXT_UPDATED
    POSITION_MONITOR → SL/TP hit → POSITION_CLOSED
    POSITION_CLOSED → RL learns → WEIGHTS_UPDATED
    POSITION_CLOSED → context graph records outcome → CONTEXT_UPDATED
    REGIME_CHANGED → all components adapt → cascading updates

Every event is:
1. Published to a Redis Stream (durable, ordered)
2. Consumed by all registered handlers (fan-out)
3. Stored in audit trail (DFQ for failures)
4. Available for replay (debugging, backtesting)
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Dict, Any, List, Callable, Awaitable, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum

from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)

STREAM_KEY = "events:trading"
CONSUMER_GROUP = "trading_bot"
DLQ_KEY = "events:dlq"        # Dead letter queue
AUDIT_KEY = "events:audit"
MAX_STREAM_LEN = 10000        # Keep last 10K events in stream
EVENT_TTL = 86400 * 7         # 7 days for audit trail


class EventType(str, Enum):
    # Market data events
    MARKET_TICK = "MARKET_TICK"
    MARKET_DATA_UPDATED = "MARKET_DATA_UPDATED"
    REGIME_CHANGED = "REGIME_CHANGED"

    # Signal events
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    SIGNAL_SCORED = "SIGNAL_SCORED"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"

    # Trade decision events
    TRADE_DECISION = "TRADE_DECISION"       # Agent decided (taken, skipped, partial)
    TRADE_EXECUTING = "TRADE_EXECUTING"     # Sending to broker
    TRADE_EXECUTED = "TRADE_EXECUTED"        # Broker confirmed fill
    TRADE_FAILED = "TRADE_FAILED"           # Broker rejected / error

    # Position events
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_UPDATED = "POSITION_UPDATED"   # SL/TP modified, price updated
    POSITION_CLOSING = "POSITION_CLOSING"
    POSITION_CLOSED = "POSITION_CLOSED"

    # Risk events
    RISK_UPDATED = "RISK_UPDATED"
    RISK_LIMIT_HIT = "RISK_LIMIT_HIT"
    RISK_LIMIT_APPROACHING = "RISK_LIMIT_APPROACHING"

    # RL / Learning events
    OUTCOME_RECORDED = "OUTCOME_RECORDED"
    WEIGHTS_UPDATED = "WEIGHTS_UPDATED"
    META_REVIEW_COMPLETED = "META_REVIEW_COMPLETED"

    # Context graph events
    CONTEXT_UPDATED = "CONTEXT_UPDATED"
    CONFLUENCE_DETECTED = "CONFLUENCE_DETECTED"
    SECTOR_CASCADE = "SECTOR_CASCADE"

    # System events
    PIPELINE_STARTED = "PIPELINE_STARTED"
    PIPELINE_STOPPED = "PIPELINE_STOPPED"
    DAILY_RESET = "DAILY_RESET"
    ERROR = "ERROR"


@dataclass
class Event:
    """A single event in the system."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    type: str = ""
    session_id: str = "auto"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data: Dict[str, Any] = field(default_factory=dict)
    source: str = ""          # Which component emitted this
    correlation_id: str = ""  # Links related events (e.g., signal → trade → close)

    def to_stream_data(self) -> Dict[str, str]:
        """Convert to Redis Stream format (all values must be strings)."""
        return {
            "id": self.id,
            "type": self.type,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "data": json.dumps(self.data),
            "source": self.source,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_stream_data(cls, stream_data: Dict[str, str]) -> "Event":
        return cls(
            id=stream_data.get("id", ""),
            type=stream_data.get("type", ""),
            session_id=stream_data.get("session_id", ""),
            timestamp=stream_data.get("timestamp", ""),
            data=json.loads(stream_data.get("data", "{}")),
            source=stream_data.get("source", ""),
            correlation_id=stream_data.get("correlation_id", ""),
        )


# Type alias for event handlers
EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    """
    Central event bus. Publish-subscribe with Redis Streams.
    Handlers register for specific event types and get called when events arrive.
    Failed handlers → DLQ with retry.
    All events → audit trail.
    """

    def __init__(self):
        self._handlers: Dict[str, List[EventHandler]] = {}
        self._running = False
        self._consumer_task: Optional[asyncio.Task] = None

    def subscribe(self, event_type: EventType, handler: EventHandler):
        """Register a handler for an event type."""
        key = event_type.value
        if key not in self._handlers:
            self._handlers[key] = []
        self._handlers[key].append(handler)
        logger.debug("Handler registered for %s: %s", key, handler.__name__)

    def subscribe_all(self, handler: EventHandler):
        """Register a handler for ALL events (e.g., audit logger)."""
        for et in EventType:
            self.subscribe(et, handler)

    async def publish(self, event: Event):
        """Publish an event to the stream."""
        client = await get_redis_client()

        # Add to Redis Stream (durable, ordered)
        await client.xadd(
            STREAM_KEY,
            event.to_stream_data(),
            maxlen=MAX_STREAM_LEN,
        )

        # Also dispatch to in-process handlers immediately (low-latency path)
        await self._dispatch(event)

    async def _dispatch(self, event: Event):
        """Dispatch event to all registered handlers."""
        handlers = self._handlers.get(event.type, [])

        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(
                    "Event handler failed: %s for %s — %s",
                    handler.__name__, event.type, e,
                )
                # Send to DLQ
                await self._send_to_dlq(event, handler.__name__, str(e))

    async def _send_to_dlq(self, event: Event, handler_name: str, error: str):
        """Send failed event to dead-letter queue for retry/investigation."""
        client = await get_redis_client()
        dlq_entry = {
            "event": json.dumps(event.to_stream_data()),
            "handler": handler_name,
            "error": error,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "retry_count": "0",
        }
        await client.xadd(DLQ_KEY, dlq_entry, maxlen=1000)
        logger.warning("Event sent to DLQ: %s (handler=%s, error=%s)", event.type, handler_name, error)

    async def start_consumer(self):
        """
        Start consuming events from Redis Stream.
        This is for multi-process scenarios where events are published
        by one process and consumed by another.
        """
        self._running = True
        client = await get_redis_client()

        # Create consumer group if it doesn't exist
        try:
            await client.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
        except Exception:
            pass  # Group already exists

        self._consumer_task = asyncio.create_task(self._consume_loop(client))
        logger.info("Event consumer started")

    async def _consume_loop(self, client):
        """Background loop that reads from Redis Stream."""
        consumer_name = f"bot-{uuid.uuid4().hex[:8]}"

        while self._running:
            try:
                results = await client.xreadgroup(
                    CONSUMER_GROUP, consumer_name,
                    {STREAM_KEY: ">"},
                    count=10,
                    block=1000,  # 1 second block
                )

                for stream_name, messages in results:
                    for msg_id, msg_data in messages:
                        try:
                            # Decode bytes to strings
                            decoded = {
                                k if isinstance(k, str) else k.decode():
                                v if isinstance(v, str) else v.decode()
                                for k, v in msg_data.items()
                            }
                            event = Event.from_stream_data(decoded)
                            await self._dispatch(event)
                            # Acknowledge
                            await client.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)
                        except Exception as e:
                            logger.error("Failed to process stream message: %s", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Consumer loop error: %s", e)
                await asyncio.sleep(1)

    async def stop_consumer(self):
        """Stop the consumer loop."""
        self._running = False
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        logger.info("Event consumer stopped")

    # ─── DLQ Management ───

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

    async def retry_dlq_entry(self, stream_id: str) -> bool:
        """Retry a failed event from the DLQ."""
        client = await get_redis_client()
        results = await client.xrange(DLQ_KEY, min=stream_id, max=stream_id)
        if not results:
            return False

        _, data = results[0]
        decoded = {
            k if isinstance(k, str) else k.decode():
            v if isinstance(v, str) else v.decode()
            for k, v in data.items()
        }

        event_data = json.loads(decoded.get("event", "{}"))
        event = Event.from_stream_data(event_data)

        # Re-dispatch
        await self._dispatch(event)

        # Remove from DLQ
        await client.xdel(DLQ_KEY, stream_id)
        return True

    async def flush_dlq(self):
        """Clear the dead-letter queue."""
        client = await get_redis_client()
        await client.delete(DLQ_KEY)

    # ─── Query / Replay ───

    async def get_recent_events(
        self,
        event_type: Optional[str] = None,
        count: int = 50,
    ) -> List[Event]:
        """Get recent events from the stream, optionally filtered by type."""
        client = await get_redis_client()
        results = await client.xrevrange(STREAM_KEY, count=count)

        events = []
        for msg_id, data in results:
            decoded = {
                k if isinstance(k, str) else k.decode():
                v if isinstance(v, str) else v.decode()
                for k, v in data.items()
            }
            event = Event.from_stream_data(decoded)
            if event_type and event.type != event_type:
                continue
            events.append(event)

        return events

    async def get_event_chain(self, correlation_id: str) -> List[Event]:
        """Get all events linked by a correlation ID (e.g., signal → trade → close)."""
        # Scan recent events for matching correlation_id
        all_events = await self.get_recent_events(count=500)
        return [e for e in all_events if e.correlation_id == correlation_id]


# ─── Convenience publish functions ───

# Singleton
event_bus = EventBus()


async def emit_event(
    event_type: EventType,
    data: Dict[str, Any],
    source: str = "",
    session_id: str = "auto",
    correlation_id: str = "",
):
    """Convenience: publish an event."""
    event = Event(
        type=event_type.value,
        session_id=session_id,
        data=data,
        source=source,
        correlation_id=correlation_id,
    )
    await event_bus.publish(event)
    return event.id


async def emit_signal_generated(signal_data: Dict, source: str = "scanner") -> str:
    return await emit_event(EventType.SIGNAL_GENERATED, signal_data, source)


async def emit_trade_executed(trade_data: Dict, source: str = "broker") -> str:
    return await emit_event(
        EventType.TRADE_EXECUTED, trade_data, source,
        correlation_id=trade_data.get("signal_id", ""),
    )


async def emit_position_closed(close_data: Dict, source: str = "monitor") -> str:
    return await emit_event(
        EventType.POSITION_CLOSED, close_data, source,
        correlation_id=close_data.get("trade_id", ""),
    )


async def emit_regime_changed(old: str, new: str, symbol: str = "MARKET") -> str:
    return await emit_event(
        EventType.REGIME_CHANGED,
        {"old_regime": old, "new_regime": new, "symbol": symbol},
        source="regime_detector",
    )


async def emit_risk_event(event_type: str, details: Dict) -> str:
    return await emit_event(EventType.RISK_LIMIT_HIT, {"event_type": event_type, **details}, source="risk_service")
