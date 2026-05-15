"""
Trading conversation state machine.
Valid transitions enforced, dual storage (Redis + PostgreSQL), context per state.
Supports Level 1 (assisted), Level 2 (semi-auto), and Level 3 (full autonomy).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, List, Any

from app.core.redis import redis_get, redis_set, redis_delete

logger = logging.getLogger(__name__)


class TradingState(str, Enum):
    """All possible states in a trading conversation."""
    IDLE = "IDLE"
    MARKET_WATCH = "MARKET_WATCH"           # Browsing / scanning markets
    ANALYZING = "ANALYZING"                  # Running technical/sentiment analysis
    SIGNAL_DETECTED = "SIGNAL_DETECTED"      # Signal found, awaiting confirmation
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"  # User must confirm trade
    ORDER_PLACING = "ORDER_PLACING"          # Submitting order to broker
    ORDER_PLACED = "ORDER_PLACED"            # Order submitted, awaiting fill
    POSITION_OPEN = "POSITION_OPEN"          # Position active, monitoring
    POSITION_MODIFYING = "POSITION_MODIFYING"  # Updating SL/TP/quantity
    CLOSING_POSITION = "CLOSING_POSITION"    # Closing a position
    POSITION_CLOSED = "POSITION_CLOSED"      # Position closed, P&L calculated
    REVIEWING = "REVIEWING"                  # Post-trade review
    RISK_ALERT = "RISK_ALERT"               # Risk limit hit, action required
    BACKTESTING = "BACKTESTING"             # Running backtest
    PORTFOLIO_VIEW = "PORTFOLIO_VIEW"        # Viewing portfolio summary
    ERROR = "ERROR"                          # Something went wrong


# ─── Valid Transitions Graph ───

VALID_TRANSITIONS: Dict[TradingState, List[TradingState]] = {
    TradingState.IDLE: [
        TradingState.MARKET_WATCH,
        TradingState.ANALYZING,
        TradingState.PORTFOLIO_VIEW,
        TradingState.BACKTESTING,
        TradingState.POSITION_OPEN,  # Resume monitoring
    ],
    TradingState.MARKET_WATCH: [
        TradingState.ANALYZING,
        TradingState.IDLE,
        TradingState.PORTFOLIO_VIEW,
        TradingState.SIGNAL_DETECTED,
    ],
    TradingState.ANALYZING: [
        TradingState.SIGNAL_DETECTED,
        TradingState.MARKET_WATCH,
        TradingState.IDLE,
    ],
    TradingState.SIGNAL_DETECTED: [
        TradingState.AWAITING_CONFIRMATION,
        TradingState.ANALYZING,      # Re-analyze
        TradingState.IDLE,            # Dismiss signal
    ],
    TradingState.AWAITING_CONFIRMATION: [
        TradingState.ORDER_PLACING,
        TradingState.IDLE,            # User rejects
        TradingState.SIGNAL_DETECTED, # Back to signal
    ],
    TradingState.ORDER_PLACING: [
        TradingState.ORDER_PLACED,
        TradingState.ERROR,
        TradingState.IDLE,            # Order cancelled before submission
    ],
    TradingState.ORDER_PLACED: [
        TradingState.POSITION_OPEN,   # Order filled
        TradingState.IDLE,            # Order cancelled/expired
        TradingState.ERROR,
    ],
    TradingState.POSITION_OPEN: [
        TradingState.POSITION_MODIFYING,
        TradingState.CLOSING_POSITION,
        TradingState.RISK_ALERT,
        TradingState.PORTFOLIO_VIEW,
        TradingState.MARKET_WATCH,    # Continue scanning while position open
        TradingState.ANALYZING,       # Analyze same or other symbols
    ],
    TradingState.POSITION_MODIFYING: [
        TradingState.POSITION_OPEN,
        TradingState.ERROR,
    ],
    TradingState.CLOSING_POSITION: [
        TradingState.POSITION_CLOSED,
        TradingState.POSITION_OPEN,   # Partial close, still open
        TradingState.ERROR,
    ],
    TradingState.POSITION_CLOSED: [
        TradingState.REVIEWING,
        TradingState.IDLE,
        TradingState.MARKET_WATCH,    # Continue trading
    ],
    TradingState.REVIEWING: [
        TradingState.IDLE,
        TradingState.MARKET_WATCH,
    ],
    TradingState.RISK_ALERT: [
        TradingState.CLOSING_POSITION,
        TradingState.POSITION_MODIFYING,
        TradingState.POSITION_OPEN,   # Alert acknowledged
        TradingState.IDLE,
    ],
    TradingState.BACKTESTING: [
        TradingState.IDLE,
        TradingState.ANALYZING,
    ],
    TradingState.PORTFOLIO_VIEW: [
        TradingState.IDLE,
        TradingState.POSITION_OPEN,
        TradingState.ANALYZING,
        TradingState.MARKET_WATCH,
    ],
    TradingState.ERROR: [
        TradingState.IDLE,
        TradingState.POSITION_OPEN,   # Recover if position still open
    ],
}


# ─── State Context (system prompt instructions per state) ───

STATE_CONTEXT: Dict[TradingState, str] = {
    TradingState.IDLE: (
        "You are in IDLE state. The trader is not actively engaged. "
        "Offer to: scan markets, analyze a symbol, view portfolio, or run a backtest. "
        "Use the `scan_markets` or `analyze_symbol` tool to begin."
    ),
    TradingState.MARKET_WATCH: (
        "You are scanning markets. Show the trader interesting movers, volume spikes, "
        "and potential setups. Use `scan_markets` and `get_market_data` tools. "
        "If you detect a strong setup, transition to ANALYZING."
    ),
    TradingState.ANALYZING: (
        "You are analyzing a specific symbol. Run technical indicators (RSI, MACD, "
        "Bollinger Bands) and sentiment analysis. Use `run_technical_analysis` and "
        "`analyze_sentiment` tools. Present a clear BUY/SELL/HOLD recommendation. "
        "If the signal is strong enough, generate a Signal."
    ),
    TradingState.SIGNAL_DETECTED: (
        "A trading signal has been detected. Present the signal clearly: "
        "symbol, direction, entry price, stop loss, take profit, confidence score, "
        "and reasoning. Ask the trader to confirm or dismiss."
    ),
    TradingState.AWAITING_CONFIRMATION: (
        "Waiting for the trader to confirm or reject the proposed trade. "
        "Do NOT place any orders until explicit confirmation. "
        "You may answer questions about the trade setup."
    ),
    TradingState.ORDER_PLACING: (
        "Placing an order with the broker. Use `place_order` tool. "
        "ALWAYS run `check_risk_limits` BEFORE placing. "
        "If risk check fails, inform the trader and go back to IDLE."
    ),
    TradingState.ORDER_PLACED: (
        "Order has been submitted to the broker. Monitor for fill confirmation. "
        "Use `check_order_status` to track. Inform the trader of partial fills."
    ),
    TradingState.POSITION_OPEN: (
        "Position is open and being monitored. Track P&L, check stop loss and "
        "take profit levels. Use `monitor_position` and `get_market_data`. "
        "Alert the trader on significant price moves. You can also continue "
        "scanning other markets."
    ),
    TradingState.POSITION_MODIFYING: (
        "Modifying an open position. Use `update_position` to change stop loss, "
        "take profit, or add to position. Confirm changes with the trader."
    ),
    TradingState.CLOSING_POSITION: (
        "Closing a position. Use `close_position` tool. Calculate final P&L. "
        "Record the trade outcome."
    ),
    TradingState.POSITION_CLOSED: (
        "Position has been closed. Show final P&L, fees, and trade duration. "
        "Offer to review the trade or continue to the next opportunity."
    ),
    TradingState.REVIEWING: (
        "Post-trade review. Use `review_trade` to analyze what went right/wrong. "
        "Record lessons learned. Update strategy confidence scores."
    ),
    TradingState.RISK_ALERT: (
        "RISK ALERT: A risk limit has been triggered. This is URGENT. "
        "Show the trader which limit was hit and recommend action. "
        "If auto-close is enabled, close the position immediately."
    ),
    TradingState.BACKTESTING: (
        "Running a backtest. Use `run_backtest` tool with the specified strategy "
        "and timeframe. Present results: total return, win rate, max drawdown, "
        "Sharpe ratio."
    ),
    TradingState.PORTFOLIO_VIEW: (
        "Viewing portfolio. Use `view_portfolio` to show all positions, "
        "total P&L, exposure, and risk metrics. Highlight any positions "
        "near stop loss or take profit."
    ),
    TradingState.ERROR: (
        "An error occurred. Diagnose the issue, inform the trader, and "
        "offer to retry or return to IDLE. Check broker connectivity."
    ),
}


# ─── State Management Functions ───

def _state_key(session_id: str) -> str:
    return f"trading_state:{session_id}"


def _history_key(session_id: str) -> str:
    return f"trading_state_history:{session_id}"


async def get_state(session_id: str) -> TradingState:
    """Get current state from Redis, default to IDLE."""
    raw = await redis_get(_state_key(session_id))
    if raw:
        try:
            data = json.loads(raw)
            return TradingState(data["state"])
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
    return TradingState.IDLE


async def get_state_data(session_id: str) -> Dict[str, Any]:
    """Get full state data including metadata."""
    raw = await redis_get(_state_key(session_id))
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {"state": TradingState.IDLE.value, "metadata": {}}


async def transition_state(
    session_id: str,
    to_state: TradingState,
    reason: str = "",
    metadata: Optional[Dict] = None,
) -> bool:
    """
    Transition to a new state with validation.
    Returns True if transition was valid and applied, False otherwise.
    """
    current = await get_state(session_id)

    # Validate transition
    valid_next = VALID_TRANSITIONS.get(current, [])
    if to_state not in valid_next:
        logger.warning(
            "Invalid state transition: %s -> %s (session=%s, reason=%s)",
            current.value, to_state.value, session_id, reason,
        )
        return False

    now = datetime.now(timezone.utc).isoformat()

    # Build state data
    state_data = {
        "state": to_state.value,
        "previous_state": current.value,
        "transitioned_at": now,
        "reason": reason,
        "metadata": metadata or {},
    }

    # Write to Redis
    from app.core.config import settings
    await redis_set(
        _state_key(session_id),
        json.dumps(state_data),
        ttl=settings.SESSION_TTL_SECONDS,
    )

    # Append to history
    history_entry = {
        "from": current.value,
        "to": to_state.value,
        "reason": reason,
        "at": now,
    }
    from app.core.redis import get_redis_client
    client = await get_redis_client()
    await client.rpush(_history_key(session_id), json.dumps(history_entry))
    await client.expire(_history_key(session_id), settings.SESSION_TTL_SECONDS)

    logger.info(
        "State transition: %s -> %s (session=%s, reason=%s)",
        current.value, to_state.value, session_id, reason,
    )
    return True


async def get_state_history(session_id: str) -> List[Dict]:
    """Get full transition history for a session."""
    from app.core.redis import get_redis_client
    client = await get_redis_client()
    raw_list = await client.lrange(_history_key(session_id), 0, -1)
    return [json.loads(item) for item in raw_list]


async def get_state_context(session_id: str) -> str:
    """Get the system prompt context for the current state."""
    state = await get_state(session_id)
    return STATE_CONTEXT.get(state, "")


async def reset_state(session_id: str):
    """Reset session to IDLE and clear history."""
    await redis_delete(_state_key(session_id))
    await redis_delete(_history_key(session_id))
    logger.info("State reset to IDLE: session=%s", session_id)


# ─── Tool filtering per state ───

STATE_ALLOWED_TOOLS: Dict[TradingState, List[str]] = {
    TradingState.IDLE: [
        "scan_markets", "analyze_symbol", "view_portfolio",
        "run_backtest", "respond",
    ],
    TradingState.MARKET_WATCH: [
        "scan_markets", "get_market_data", "analyze_symbol",
        "view_portfolio", "respond",
    ],
    TradingState.ANALYZING: [
        "run_technical_analysis", "analyze_sentiment", "get_market_data",
        "generate_signal", "respond",
    ],
    TradingState.SIGNAL_DETECTED: [
        "respond", "view_portfolio", "check_risk_limits",
    ],
    TradingState.AWAITING_CONFIRMATION: [
        "place_order", "respond", "check_risk_limits",
    ],
    TradingState.ORDER_PLACING: [
        "place_order", "check_risk_limits", "respond",
    ],
    TradingState.ORDER_PLACED: [
        "check_order_status", "cancel_order", "respond",
    ],
    TradingState.POSITION_OPEN: [
        "monitor_position", "update_position", "close_position",
        "get_market_data", "scan_markets", "analyze_symbol",
        "view_portfolio", "respond",
    ],
    TradingState.POSITION_MODIFYING: [
        "update_position", "respond",
    ],
    TradingState.CLOSING_POSITION: [
        "close_position", "respond",
    ],
    TradingState.POSITION_CLOSED: [
        "review_trade", "scan_markets", "view_portfolio", "respond",
    ],
    TradingState.REVIEWING: [
        "review_trade", "respond",
    ],
    TradingState.RISK_ALERT: [
        "close_position", "update_position", "respond",
    ],
    TradingState.BACKTESTING: [
        "run_backtest", "respond",
    ],
    TradingState.PORTFOLIO_VIEW: [
        "view_portfolio", "analyze_symbol", "close_position",
        "monitor_position", "respond",
    ],
    TradingState.ERROR: [
        "respond",
    ],
}


def get_allowed_tools(state: TradingState) -> List[str]:
    """Get the list of tool names allowed in the current state."""
    return STATE_ALLOWED_TOOLS.get(state, ["respond"])
