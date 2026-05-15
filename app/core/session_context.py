"""
Session context manager — tracks trader context, risk limits, active symbols.
ContextVar-based per-request state for async handlers.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, Dict, Any, List
from contextvars import ContextVar

from app.core.redis import redis_get, redis_set, redis_hgetall, redis_hset
from app.core.config import settings

logger = logging.getLogger(__name__)

# Context variables for current request
current_session_id: ContextVar[Optional[str]] = ContextVar("current_session_id", default=None)
current_trader_id: ContextVar[Optional[str]] = ContextVar("current_trader_id", default=None)


def _session_meta_key(session_id: str) -> str:
    return f"session_meta:{session_id}"


def _portfolio_key(session_id: str) -> str:
    return f"portfolio:{session_id}"


def _risk_key(session_id: str) -> str:
    return f"risk_limits:{session_id}"


def _signal_queue_key(session_id: str) -> str:
    return f"signal_queue:{session_id}"


def _watchlist_key(session_id: str) -> str:
    return f"watchlist:{session_id}"


# ─── Session Metadata ───

async def get_session_metadata(session_id: str) -> Dict[str, Any]:
    """Get all session metadata."""
    return await redis_hgetall(_session_meta_key(session_id))


async def set_session_field(session_id: str, field: str, value: str):
    """Set a single metadata field."""
    await redis_hset(
        _session_meta_key(session_id),
        {field: value},
        ttl=settings.SESSION_TTL_SECONDS,
    )


async def get_session_field(session_id: str, field: str) -> Optional[str]:
    """Get a single metadata field."""
    meta = await redis_hgetall(_session_meta_key(session_id))
    return meta.get(field)


# ─── Trader Preferences ───

async def get_trader_preferences(session_id: str) -> Dict[str, Any]:
    """Get trader-specific preferences (risk tolerance, preferred timeframes, etc.)."""
    raw = await redis_get(f"trader_prefs:{session_id}")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {
        "risk_tolerance": "moderate",
        "preferred_timeframe": "1h",
        "auto_stop_loss": True,
        "auto_take_profit": True,
        "max_position_size": settings.MAX_POSITION_SIZE,
        "max_leverage": settings.MAX_LEVERAGE,
    }


async def set_trader_preferences(session_id: str, prefs: Dict[str, Any]):
    """Update trader preferences."""
    await redis_set(
        f"trader_prefs:{session_id}",
        json.dumps(prefs),
        ttl=settings.SESSION_TTL_SECONDS,
    )


# ─── Watchlist ───

async def get_watchlist(session_id: str) -> List[str]:
    """Get the trader's watchlist symbols."""
    raw = await redis_get(_watchlist_key(session_id))
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return []


async def add_to_watchlist(session_id: str, symbol: str):
    """Add a symbol to the watchlist."""
    watchlist = await get_watchlist(session_id)
    symbol = symbol.upper()
    if symbol not in watchlist:
        watchlist.append(symbol)
        await redis_set(
            _watchlist_key(session_id),
            json.dumps(watchlist),
            ttl=settings.SESSION_TTL_SECONDS,
        )


async def remove_from_watchlist(session_id: str, symbol: str):
    """Remove a symbol from the watchlist."""
    watchlist = await get_watchlist(session_id)
    symbol = symbol.upper()
    if symbol in watchlist:
        watchlist.remove(symbol)
        await redis_set(
            _watchlist_key(session_id),
            json.dumps(watchlist),
            ttl=settings.SESSION_TTL_SECONDS,
        )


# ─── Signal Queue ───

async def push_signal(session_id: str, signal: Dict[str, Any]):
    """Push a new signal to the queue."""
    from app.core.redis import get_redis_client
    client = await get_redis_client()
    await client.rpush(_signal_queue_key(session_id), json.dumps(signal))
    await client.expire(_signal_queue_key(session_id), settings.SIGNAL_TTL_SECONDS)


async def pop_signal(session_id: str) -> Optional[Dict[str, Any]]:
    """Pop the next signal from the queue."""
    from app.core.redis import get_redis_client
    client = await get_redis_client()
    raw = await client.lpop(_signal_queue_key(session_id))
    if raw:
        return json.loads(raw)
    return None


async def get_pending_signals(session_id: str) -> List[Dict[str, Any]]:
    """Get all pending signals without consuming them."""
    from app.core.redis import get_redis_client
    client = await get_redis_client()
    raw_list = await client.lrange(_signal_queue_key(session_id), 0, -1)
    return [json.loads(item) for item in raw_list]


# ─── Active Position Tracking (fast Redis layer) ───

async def get_active_positions(session_id: str) -> Dict[str, Any]:
    """Get all active positions from Redis cache."""
    raw = await redis_get(_portfolio_key(session_id))
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {}


async def update_position_cache(session_id: str, positions: Dict[str, Any]):
    """Update the Redis position cache."""
    await redis_set(
        _portfolio_key(session_id),
        json.dumps(positions),
        ttl=settings.SESSION_TTL_SECONDS,
    )


# ─── Risk State ───

async def get_risk_state(session_id: str) -> Dict[str, Any]:
    """Get current risk state (daily P&L, exposure, etc.)."""
    raw = await redis_get(_risk_key(session_id))
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {
        "daily_pnl": 0.0,
        "total_exposure": 0.0,
        "open_positions_count": 0,
        "daily_trades_count": 0,
        "max_drawdown_today": 0.0,
    }


async def update_risk_state(session_id: str, risk_data: Dict[str, Any]):
    """Update the risk state."""
    await redis_set(
        _risk_key(session_id),
        json.dumps(risk_data),
        ttl=settings.SESSION_TTL_SECONDS,
    )
