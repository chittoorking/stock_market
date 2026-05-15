"""
Context builder — assembles the system prompt based on current state.
Assembles state-aware system prompts with risk limits and active positions.
"""
from __future__ import annotations

import logging
from typing import Optional, Dict, Any

from app.core.conversation_state import (
    get_state, get_state_context, TradingState,
)
from app.core.session_context import (
    get_active_positions, get_risk_state, get_watchlist,
    get_trader_preferences, get_pending_signals,
)

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = """You are Arjun, an expert AI trading agent. You are sharp, data-driven, and always protect your trader's capital.

## Core Personality
- You are confident but never reckless
- You present data clearly with actionable recommendations
- You ALWAYS use tools — never guess prices, P&L, or market data
- You explain your reasoning concisely
- You respect risk limits as non-negotiable

## Rules
1. NEVER place a trade without explicit trader confirmation (unless auto-trade is enabled)
2. ALWAYS run risk checks before placing any order
3. ALWAYS show stop loss and take profit with every signal
4. If risk limits are breached, alert immediately — this overrides all other tasks
5. Present numbers accurately — use tools to get real data, never estimate
6. For compound requests (e.g., "buy RELIANCE and check my portfolio"), handle them in sequence

## Tool Usage Guide
- Market scanning: Use `scan_markets` to find opportunities
- Analysis: Use `run_technical_analysis` for full analysis, `get_market_data` for quick data
- Trading: Use `place_order` to execute, `update_position` to modify, `close_position` to exit
- Portfolio: Use `view_portfolio` for overview, `monitor_position` for live tracking
- Risk: Use `check_risk_limits` before any trade, `set_risk_params` to adjust limits
- Review: Use `review_trade` for post-trade analysis
- Backtest: Use `run_backtest` to test strategies on historical data
- Text response: Use `respond` for pure text replies

## Response Style
- Use clear formatting with bold headers and emojis for key data
- Include quick action suggestions after every response
- Be concise — traders need speed, not essays
"""


async def build_system_prompt(session_id: str) -> str:
    """Build the full system prompt with state context and live data."""
    parts = [BASE_SYSTEM_PROMPT]

    # 1. Current state context
    state_ctx = await get_state_context(session_id)
    if state_ctx:
        parts.append(f"\n## Current State\n{state_ctx}")

    # 2. Active positions summary
    positions = await get_active_positions(session_id)
    if positions:
        parts.append(f"\n## Active Positions\n{_format_positions(positions)}")

    # 3. Risk state
    risk = await get_risk_state(session_id)
    if risk.get("daily_pnl", 0) != 0 or risk.get("total_exposure", 0) != 0:
        parts.append(
            f"\n## Risk State\n"
            f"Daily P&L: ₹{risk.get('daily_pnl', 0):+,.2f} | "
            f"Exposure: ₹{risk.get('total_exposure', 0):,.2f} | "
            f"Open: {risk.get('open_positions_count', 0)}"
        )

    # 4. Watchlist
    watchlist = await get_watchlist(session_id)
    if watchlist:
        parts.append(f"\n## Watchlist\n{', '.join(watchlist)}")

    # 5. Pending signals
    signals = await get_pending_signals(session_id)
    if signals:
        signal_lines = []
        for s in signals[:3]:
            signal_lines.append(
                f"- {s.get('symbol')}: {s.get('strength')} "
                f"(conf: {s.get('confidence', 0):.0%})"
            )
        parts.append(f"\n## Pending Signals\n" + "\n".join(signal_lines))

    # 6. Trader preferences
    prefs = await get_trader_preferences(session_id)
    parts.append(
        f"\n## Trader Preferences\n"
        f"Risk Tolerance: {prefs.get('risk_tolerance', 'moderate')} | "
        f"Timeframe: {prefs.get('preferred_timeframe', '1h')} | "
        f"Auto SL: {'Yes' if prefs.get('auto_stop_loss') else 'No'}"
    )

    return "\n".join(parts)


def _format_positions(positions: Dict) -> str:
    """Format positions dict for the system prompt."""
    if isinstance(positions, dict):
        if not positions:
            return "No open positions"
        lines = []
        for symbol, pos in positions.items():
            lines.append(
                f"- {symbol}: {pos.get('side', '?')} {pos.get('quantity', 0)} "
                f"@ ₹{pos.get('avg_entry_price', 0):.2f}"
            )
        return "\n".join(lines)
    return "No open positions"
