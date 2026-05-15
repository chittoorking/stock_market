"""
Graph-backed LLM agents — LLMs that query graphs for knowledge.

The LLM prompt is MINIMAL. Just the job description.
All knowledge comes from graph queries injected into the user message.

Flow:
1. System queries the graphs for relevant data
2. System builds a focused user message with ONLY the data this LLM needs
3. LLM reasons from the data
4. System writes the decision back to the graphs
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Any, List, Optional

from app.core.trade_dev_graph import TradeDevGraph, TradeNode
from app.core.market_memory import MarketMemory

logger = logging.getLogger(__name__)


def _call_llm(system: str, user: str, api_key: str, model: str = "gpt-4o-mini") -> str:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=600,
        )
        return resp.choices[0].message.content
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return "{}"


# ═══════════════════════════════════════════════════════════════
# SCANNER: ranks signals. Queries market memory for context.
# ═══════════════════════════════════════════════════════════════

SCANNER_SYSTEM = """You rank trading signals 1-10 for NSE 5-minute intraday. Output JSON only.
{"ranked": [{"symbol": "X", "direction": "LONG", "strategy": "lnz3", "score": 8, "reason": "why"}]}
Only include score >= 6."""

def scanner_with_graph(
    signals_text: str,
    sector_context: str,
    memory: MarketMemory,
    api_key: str,
) -> List[Dict]:
    """Scanner queries market memory for each signal's track record."""
    # Build memory context from graph
    memory_text = memory.get_full_memory_snapshot()

    user = f"SIGNALS:\n{signals_text}\n\nSECTOR FLOWS:\n{sector_context}\n\nHISTORY:\n{memory_text}"

    raw = _call_llm(SCANNER_SYSTEM, user, api_key)
    try:
        return json.loads(raw).get("ranked", [])
    except:
        return []


# ═══════════════════════════════════════════════════════════════
# RISK: approves/vetoes. Queries trade graph for exposure.
# ═══════════════════════════════════════════════════════════════

RISK_SYSTEM = """You approve or veto trades based on risk. Output JSON only.
Max 3 positions. One per sector. Veto if similar setup lost before.
{"approved": [{"symbol": "X", "direction": "LONG", "strategy": "lnz3"}], "vetoed": [{"symbol": "Y", "reason": "why"}]}"""

def risk_with_graph(
    ranked: List[Dict],
    trade_graph: TradeDevGraph,
    memory: MarketMemory,
    api_key: str,
) -> Dict:
    """Risk checks exposure from trade graph and history from memory."""
    portfolio = trade_graph.query_active_summary()

    # For each ranked signal, get its specific history
    signal_histories = []
    for r in ranked:
        sym = r.get("symbol", "")
        strat = r.get("strategy", "")
        sector = r.get("sector", "unknown")
        hist = memory.query_similar_conditions(sector, r.get("direction", ""), strat)
        stock_hist = memory.query_stock_history(sym)
        signal_histories.append(f"{sym} [{strat}]: {hist} | {stock_hist}")

    user = (
        f"RANKED SIGNALS:\n{json.dumps(ranked, indent=2)}\n\n"
        f"CURRENT POSITIONS:\n{portfolio}\n\n"
        f"SIGNAL HISTORY:\n" + "\n".join(signal_histories)
    )

    raw = _call_llm(RISK_SYSTEM, user, api_key)
    try:
        return json.loads(raw)
    except:
        return {"approved": [], "vetoed": []}


# ═══════════════════════════════════════════════════════════════
# EXECUTOR: sets trade params. Uses ATR data from system.
# ═══════════════════════════════════════════════════════════════

EXECUTOR_SYSTEM = """You set stop, target, and size for approved trades. Output JSON only.
Stop = 1.5-2.5x ATR from entry. Target = 2.5x risk. Size = 5% (3% if ATR > 0.4%).
{"trades": [{"symbol": "X", "direction": "LONG", "stop": 100.0, "target": 105.0, "size_pct": 0.05}]}"""

def executor_with_graph(
    approved: List[Dict],
    price_data: str,
    api_key: str,
) -> List[Dict]:
    user = f"APPROVED:\n{json.dumps(approved, indent=2)}\n\nPRICE DATA:\n{price_data}"
    raw = _call_llm(EXECUTOR_SYSTEM, user, api_key)
    try:
        return json.loads(raw).get("trades", [])
    except:
        return []


# ═══════════════════════════════════════════════════════════════
# MONITOR: manages ONE position. Queries trade dev graph.
# ═══════════════════════════════════════════════════════════════

MONITOR_SYSTEM = """You monitor ONE open position. Output JSON only.

You see P&L data, fade analysis, AND technical chart data (candle patterns, EMAs, RSI, support/resistance).

KEY RULE: If P&L is negative BUT technicals are bullish (hammer at support, RSI oversold, EMA still aligned, three green soldiers forming) → HOLD. The dip is a shakeout before continuation.

If P&L is negative AND technicals confirm weakness (bearish engulfing, death cross, RSI dropping from 50) → TIGHTEN or CLOSE.

If P&L is positive AND technicals show momentum (three soldiers, golden cross, RSI rising) → HOLD and let it run to target.

{"action": "hold|tighten|partial|close", "reason": "one sentence referencing the technical signal"}"""

def monitor_with_graph(
    trade_node: TradeNode,
    memory: MarketMemory,
    api_key: str,
    technical_data: str = "",
) -> Dict:
    """Monitor queries trade dev graph + technical data for THIS position."""
    current = trade_node.query_current_state()
    pnl_curve = trade_node.query_pnl_curve()
    fade = trade_node.query_fade_analysis()
    observations = trade_node.query_observations()
    entry_ctx = trade_node.query_entry_context()

    similar = memory.query_similar_conditions(
        trade_node.sector, trade_node.direction, trade_node.strategy,
    )
    exit_patterns = memory.query_exit_pattern_history()

    user = (
        f"POSITION: {trade_node.direction} {trade_node.symbol} [{trade_node.strategy}]\n\n"
        f"ENTRY CONTEXT:\n{entry_ctx}\n\n"
        f"CURRENT STATE:\n{current}\n\n"
        f"P&L CURVE (last 10 bars):\n{pnl_curve}\n\n"
        f"FADE ANALYSIS:\n{fade}\n\n"
        f"TECHNICAL ANALYSIS (current bar):\n{technical_data}\n\n"
        f"OBSERVATIONS:\n{observations}\n\n"
        f"SIMILAR PAST TRADES:\n{similar}\n\n"
        f"EXIT PATTERNS:\n{exit_patterns}"
    )

    raw = _call_llm(MONITOR_SYSTEM, user, api_key)
    try:
        return json.loads(raw)
    except:
        return {"action": "hold", "reason": "parse error"}


# ═══════════════════════════════════════════════════════════════
# JUDGE: reviews closed trade. Writes to memory, never fed back.
# ═══════════════════════════════════════════════════════════════

JUDGE_SYSTEM = """You review a completed trade. Output JSON only.
{"went_right": "...", "went_wrong": "...", "lesson": "one specific takeaway"}"""

def judge_with_graph(
    trade_node: TradeNode,
    api_key: str,
) -> Dict:
    user = (
        f"TRADE: {trade_node.direction} {trade_node.symbol} [{trade_node.strategy}]\n"
        f"Entry: {trade_node.entry_price:.2f} -> Exit: {trade_node.exit_price:.2f} ({trade_node.exit_reason})\n"
        f"P&L: {trade_node.final_pnl_pct:+.3f}% | MFE: {trade_node.max_mfe_pct:.3f}% | Bars: {trade_node.bars_held}\n\n"
        f"ENTRY CONTEXT:\n{trade_node.query_entry_context()}\n\n"
        f"FADE ANALYSIS:\n{trade_node.query_fade_analysis()}\n\n"
        f"OBSERVATIONS:\n{trade_node.query_observations()}"
    )
    raw = _call_llm(JUDGE_SYSTEM, user, api_key)
    try:
        return json.loads(raw)
    except:
        return {}
