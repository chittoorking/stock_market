"""
Event Handlers — the wiring between components.

Each handler reacts to specific events and triggers downstream actions.
This is where the event-driven architecture comes alive:

    SIGNAL_GENERATED → handler updates context graph, checks confluence
    TRADE_EXECUTED → handler updates risk state, context graph, sends notification
    POSITION_CLOSED → handler feeds RL engine, updates trust scores, records outcome
    REGIME_CHANGED → handler notifies all components, adjusts strategy selection
    RISK_LIMIT_HIT → handler auto-closes positions, sends critical alert

Handlers are registered at startup and run for the lifetime of the application.
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from app.core.events.event_bus import Event, EventType, event_bus

logger = logging.getLogger(__name__)


# ─── Signal Handlers ───

async def on_signal_generated(event: Event):
    """When a new signal is generated → update context graph, check confluence."""
    data = event.data
    symbol = data.get("symbol", "")
    strategy = data.get("strategy_name", "")

    if not symbol:
        return

    from app.core.context_graph import create_context_graph
    from app.signals.base import SignalRecord

    graph = create_context_graph(event.session_id)

    # Record in context graph
    record = SignalRecord(
        signal_id=data.get("id", event.id),
        strategy=strategy,
        symbol=symbol,
        direction=data.get("direction", ""),
        confidence=data.get("raw_confidence", 0),
        score=data.get("final_score", 0),
        timestamp=event.timestamp,
        regime_at_signal=data.get("market_regime", ""),
    )
    await graph.record_signal(record)

    # Check for confluence
    confluence = await graph.find_confluence(symbol)
    if confluence.get("score", 0) > 0.6:
        from app.core.events.event_bus import emit_event
        await emit_event(
            EventType.CONFLUENCE_DETECTED,
            {"symbol": symbol, "confluence": confluence},
            source="context_graph",
            session_id=event.session_id,
            correlation_id=event.id,
        )
        logger.info(
            "Confluence detected on %s: score=%.0f%%, factors=%s",
            symbol, confluence["score"] * 100, confluence.get("factors", []),
        )


async def on_signal_scored(event: Event):
    """When aggregator scores signals → log for audit."""
    data = event.data
    logger.debug(
        "Signal scored: %s %s %s → %.0f%%",
        data.get("strategy", ""), data.get("direction", ""),
        data.get("symbol", ""), data.get("final_score", 0) * 100,
    )


# ─── Trade Handlers ───

async def on_trade_executed(event: Event):
    """When a trade is executed → update everything."""
    data = event.data
    symbol = data.get("symbol", "")
    session_id = event.session_id

    if not symbol:
        return

    # 1. Update context graph with position
    from app.core.context_graph import create_context_graph
    graph = create_context_graph(session_id)
    await graph.update_position(symbol, {
        "side": data.get("side", ""),
        "avg_entry_price": data.get("entry_price", 0),
        "unrealized_pnl": 0,
    })

    # 2. Update market context trade counter
    mkt = await graph.get_market()
    mkt.trades_executed_today += 1
    from app.core.redis import redis_set
    import json
    from dataclasses import asdict
    await redis_set(
        f"ctx:market:{session_id}",
        json.dumps(asdict(mkt)), ttl=28800,
    )

    # 3. Send notification
    from app.services.notification_service import notify_all
    side = data.get("side", "?")
    qty = data.get("quantity", 0)
    price = data.get("entry_price", 0)
    strategy = data.get("strategy_name", data.get("signal_strategy", "agent"))
    await notify_all(
        f"{'📈' if side == 'BUY' else '📉'} TRADE: {side} {qty} {symbol} @ ₹{price:.2f}\n"
        f"Strategy: {strategy} | Signal: {data.get('signal_id', 'N/A')}",
    )

    logger.info("Trade executed event processed: %s %s %s", side, qty, symbol)


async def on_position_closed(event: Event):
    """When a position closes → RL learns, context updates, trust adjusts."""
    data = event.data
    symbol = data.get("symbol", "")
    session_id = event.session_id
    pnl = data.get("pnl", 0)
    pnl_pct = data.get("pnl_pct", 0)
    strategy = data.get("strategy_name", "")

    if not symbol:
        return

    # 1. Clear position in context graph
    from app.core.context_graph import create_context_graph
    graph = create_context_graph(session_id)
    await graph.update_position(symbol, None)

    # 2. Record trade outcome
    await graph.record_trade(symbol, {
        "symbol": symbol,
        "side": data.get("side", ""),
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "strategy": strategy,
        "reason": data.get("close_reason", ""),
        "closed_at": event.timestamp,
    })

    # 3. Update signal outcome in context graph
    signal_id = data.get("signal_id", "")
    if signal_id:
        await graph.record_signal_outcome(symbol, signal_id, pnl, pnl_pct)

    # 4. Feed RL engine
    from app.ai_services.reinforcement_engine import rl_engine, TradeOutcome
    signal_indicators = data.get("signal_indicators", {})
    if signal_indicators:
        outcome = TradeOutcome(
            trade_id=data.get("trade_id", event.id),
            symbol=symbol,
            side=data.get("side", "BUY"),
            entry_price=data.get("entry_price", 0),
            exit_price=data.get("exit_price", 0),
            pnl=pnl,
            pnl_pct=pnl_pct,
            duration_minutes=data.get("duration_minutes", 0),
            timeframe=data.get("timeframe", "5m"),
            signal_indicators=signal_indicators,
            signal_strength=data.get("signal_strength", ""),
            signal_confidence=data.get("signal_confidence", 0.5),
            market_regime=data.get("regime", "unknown"),
            volume_regime="normal",
        )
        await rl_engine.record_outcome(session_id, outcome)

        # Emit weight update event
        from app.core.events.event_bus import emit_event
        weights = await rl_engine.get_current_weights(session_id)
        await emit_event(
            EventType.WEIGHTS_UPDATED,
            {"weights": weights, "triggered_by": event.id},
            source="rl_engine",
            session_id=session_id,
        )

    # 5. Update trust scores in aggregator
    if strategy:
        from app.signals.aggregator import signal_aggregator
        await signal_aggregator.update_trust(session_id, strategy, pnl > 0, pnl_pct)

    # 6. Notify
    from app.services.notification_service import notify_all
    emoji = "✅" if pnl >= 0 else "❌"
    await notify_all(
        f"{emoji} CLOSED: {symbol} | P&L: ₹{pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
        f"Strategy: {strategy} | Reason: {data.get('close_reason', 'manual')}",
        level="info" if pnl >= 0 else "warning",
    )

    logger.info("Position closed event processed: %s pnl=%.2f strategy=%s", symbol, pnl, strategy)


# ─── Risk Handlers ───

async def on_risk_limit_hit(event: Event):
    """When a risk limit is hit → critical alert, possible auto-close."""
    data = event.data
    event_type = data.get("event_type", "UNKNOWN")
    symbol = data.get("symbol", "")
    details = data.get("details", "")

    from app.services.notification_service import notify_all
    await notify_all(
        f"🚨 RISK LIMIT: {event_type}\n"
        f"{'Symbol: ' + symbol + chr(10) if symbol else ''}"
        f"{details}",
        level="critical",
    )

    # If daily loss limit → halt pipeline
    if event_type == "DAILY_LOSS_LIMIT":
        logger.critical("Daily loss limit hit — halting autonomous pipeline")
        from app.services.autonomous_pipeline import stop_autonomous_pipeline
        await stop_autonomous_pipeline()


async def on_regime_changed(event: Event):
    """When market regime changes → update context, notify agent."""
    data = event.data
    old = data.get("old_regime", "")
    new = data.get("new_regime", "")
    symbol = data.get("symbol", "MARKET")

    from app.core.context_graph import create_context_graph
    graph = create_context_graph(event.session_id)

    if symbol == "MARKET":
        await graph.update_market({"regime": new})
    else:
        await graph.update_symbol(symbol, {"regime": new})

    from app.services.notification_service import notify_all
    await notify_all(
        f"🔄 Regime change: {symbol} {old} → {new}",
        level="info",
    )

    logger.info("Regime changed: %s %s → %s", symbol, old, new)


# ─── Audit Handler ───

async def on_any_event_audit(event: Event):
    """Log every event to the audit trail. Catches everything."""
    client = await get_redis_client()
    from app.core.redis import get_redis_client
    client = await get_redis_client()

    audit_entry = {
        "event_id": event.id,
        "type": event.type,
        "session": event.session_id,
        "source": event.source,
        "timestamp": event.timestamp,
        "correlation": event.correlation_id,
        "data_summary": str(event.data)[:500],  # Truncate for storage
    }

    await client.xadd(
        f"audit:{event.session_id}",
        {k: str(v) for k, v in audit_entry.items()},
        maxlen=50000,  # Keep 50K audit entries
    )


# ─── Registration ───

def register_all_handlers():
    """Register all event handlers. Called at startup."""
    # Signal events
    event_bus.subscribe(EventType.SIGNAL_GENERATED, on_signal_generated)
    event_bus.subscribe(EventType.SIGNAL_SCORED, on_signal_scored)

    # Trade events
    event_bus.subscribe(EventType.TRADE_EXECUTED, on_trade_executed)
    event_bus.subscribe(EventType.POSITION_CLOSED, on_position_closed)

    # Risk events
    event_bus.subscribe(EventType.RISK_LIMIT_HIT, on_risk_limit_hit)

    # Regime events
    event_bus.subscribe(EventType.REGIME_CHANGED, on_regime_changed)

    # Audit — logs everything
    event_bus.subscribe_all(on_any_event_audit)

    logger.info("All event handlers registered")
