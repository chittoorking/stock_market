"""
Trading crew — the main entry point for processing trader messages.
Orchestrates input through state-based tool filtering, LLM reasoning, and post-processing hooks.
Handles: input → state-based tool filtering → LLM → tool execution → post-processing hooks → emit.
"""
from __future__ import annotations

import logging
from typing import Optional, Dict, Any, List

from app.core.conversation_state import (
    get_state, get_allowed_tools, TradingState, transition_state,
)
from app.orchestration.direct_fc import direct_function_call
from app.orchestration.context import build_system_prompt
from app.core.agui_events.emit import emit_text, emit_activity
from app.ai_services.intent_classifier import classify_intent

logger = logging.getLogger(__name__)


def _create_all_tools(session_id: str) -> List:
    """Create all tools for a session (CRUD factory pattern)."""
    from app.features.market_analysis.tools_crud import create_market_analysis_tools
    from app.features.order_execution.tools_crud import create_order_execution_tools
    from app.features.portfolio.tools_crud import create_portfolio_tools
    from app.features.risk_management.tools_crud import create_risk_management_tools

    tools = []
    tools.extend(create_market_analysis_tools(session_id))
    tools.extend(create_order_execution_tools(session_id))
    tools.extend(create_portfolio_tools(session_id))
    tools.extend(create_risk_management_tools(session_id))
    return tools


def _create_respond_tool(session_id: str):
    """Create the generic respond tool for plain-text replies."""
    from crewai_tools import tool

    @tool("respond")
    async def respond(message: str) -> str:
        """
        Send a text response to the trader.
        Use this when no specific trading tool is needed.
        """
        return message

    return respond


async def process_message(
    session_id: str,
    message: str,
    conversation_history: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    Process a trader's message through the full pipeline.

    Pipeline:
    1. Intent classification
    2. State-based tool filtering
    3. System prompt assembly
    4. LLM function calling
    5. Post-processing hooks (risk checks, state transitions)
    6. Event emission

    Returns:
        {"response": str, "tool_used": str|None, "quick_replies": list, "state": str}
    """
    current_state = await get_state(session_id)

    # ─── 1. Activity indicator ───
    await emit_activity(session_id, "thinking", True)

    # ─── 2. Intent classification (fast path) ───
    intent = await classify_intent(message)
    logger.info(
        "Intent: %s (confidence=%.2f) state=%s session=%s",
        intent["intent"], intent["confidence"], current_state.value, session_id,
    )

    # ─── 3. Auto state transition based on intent ───
    await _auto_transition(session_id, current_state, intent["intent"])
    current_state = await get_state(session_id)

    # ─── 4. Create and filter tools ───
    all_tools = _create_all_tools(session_id)
    all_tools.append(_create_respond_tool(session_id))

    allowed_names = get_allowed_tools(current_state)
    filtered_tools = [t for t in all_tools if t.name in allowed_names]

    if not filtered_tools:
        # Fallback — always allow respond
        respond_tool = _create_respond_tool(session_id)
        filtered_tools = [respond_tool]

    logger.info(
        "Tools available: %s (state=%s)",
        [t.name for t in filtered_tools], current_state.value,
    )

    # ─── 5. Build system prompt with live context ───
    system_prompt = await build_system_prompt(session_id)

    # ─── 6. Direct function calling ───
    response_text, tool_name, tool_result, quick_replies = await direct_function_call(
        message=message,
        tools=filtered_tools,
        system_prompt=system_prompt,
        conversation_history=conversation_history,
    )

    # ─── 7. Post-processing hooks ───
    await _post_process(session_id, tool_name, tool_result)

    # ─── 8. Emit response ───
    await emit_activity(session_id, "thinking", False)
    await emit_text(session_id, response_text, quick_replies)

    final_state = await get_state(session_id)

    return {
        "response": response_text,
        "tool_used": tool_name,
        "quick_replies": quick_replies,
        "state": final_state.value,
        "intent": intent["intent"],
    }


async def _auto_transition(session_id: str, current: TradingState, intent: str):
    """Auto-transition state based on intent (convenience for the trader)."""
    transitions = {
        "scan_market": TradingState.MARKET_WATCH,
        "analyze_symbol": TradingState.ANALYZING,
        "view_portfolio": TradingState.PORTFOLIO_VIEW,
        "run_backtest": TradingState.BACKTESTING,
    }

    target = transitions.get(intent)
    if target and target != current:
        # Only auto-transition from non-critical states
        safe_states = {
            TradingState.IDLE, TradingState.MARKET_WATCH,
            TradingState.PORTFOLIO_VIEW, TradingState.POSITION_CLOSED,
            TradingState.REVIEWING,
        }
        if current in safe_states:
            await transition_state(session_id, target, reason=f"auto_{intent}")


async def _post_process(session_id: str, tool_name: Optional[str], tool_result: Optional[str]):
    """
    Post-processing hooks — run after every tool call.
    State transitions, notifications, and side-effects after each tool call.
    """
    if not tool_name:
        return

    current_state = await get_state(session_id)

    # ─── Hook: Risk monitoring after any trade action ───
    trade_tools = {"place_order", "update_position", "close_position"}
    if tool_name in trade_tools:
        from app.services.risk_service import risk_service
        risk_check = await risk_service.get_risk_summary(session_id)

        from app.core.config import settings
        if abs(risk_check.get("daily_pnl", 0)) > settings.MAX_DAILY_LOSS * 0.8:
            await emit_text(
                session_id,
                "⚠️ **Warning:** You're approaching your daily loss limit. "
                "Consider reducing exposure.",
            )

    # ─── Hook: Auto-monitor after order placed ───
    if tool_name == "place_order" and current_state == TradingState.POSITION_OPEN:
        logger.info("Position opened — auto-monitoring enabled for session %s", session_id)

    # ─── Hook: Prompt review after position closed ───
    if tool_name == "close_position" and current_state == TradingState.POSITION_CLOSED:
        await emit_text(
            session_id,
            "📝 Trade closed. Want me to review this trade for lessons learned?",
            [{"label": "Review trade", "action": "review_trade"}],
        )
