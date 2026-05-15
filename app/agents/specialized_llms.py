"""
Specialized LLM Agents — each has ONE job, sees ONLY relevant data.

No LLM sees another LLM's output. No feedback contamination.
The SYSTEM orchestrates. LLMs provide intelligence at specific decision points.

Architecture:
    Raw Data → Scanner LLM → ranked signals
    Ranked signals + portfolio → Risk LLM → approved/vetoed
    Approved signals + ATR data → Executor LLM → stop/target/size
    Open position + live data → Monitor LLM → hold/tighten/close
    Closed trade + outcome → Judge LLM → lesson learned (stored, never fed back to other LLMs)
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _call_llm(system: str, user: str, api_key: str, model: str = "gpt-4o-mini") -> str:
    """Single LLM call. Returns raw text."""
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
            temperature=0.15,
            max_tokens=800,
        )
        return resp.choices[0].message.content
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return "{}"


# ═══════════════════════════════════════════════════════════════
# LLM 1: SCANNER — ranks signals by quality
# ═══════════════════════════════════════════════════════════════

SCANNER_SYSTEM = """You are a signal quality ranker for Indian stock markets (NSE, 5-minute intraday).

You receive raw strategy signals with market context. Your ONLY job: rank them from best to worst.

For each signal, score 1-10 based on:
- Is the sector flow supporting this direction? (leader + laggards aligned)
- Is volume confirming? (RVOL > 2x, no bearish divergence)
- Multiple strategies agree on this stock? (confluence)
- Is the stock showing exhaustion pattern AGAINST this signal? (bad)
- Is this a crowd signal? (too many stocks signaling = noise)

Output JSON: {"ranked": [{"symbol": "X", "direction": "LONG", "strategy": "lnz3", "score": 8, "reason": "why"}]}
Only include signals scoring 6+. Drop the rest silently."""

def scanner_rank(context: str, api_key: str) -> List[Dict]:
    """LLM 1: Rank signals by quality. Returns ranked list."""
    raw = _call_llm(SCANNER_SYSTEM, context, api_key)
    try:
        data = json.loads(raw)
        return data.get("ranked", [])
    except:
        return []


# ═══════════════════════════════════════════════════════════════
# LLM 2: RISK — vetoes unsafe trades
# ═══════════════════════════════════════════════════════════════

RISK_SYSTEM = """You are a risk manager for an intraday trading system.

You receive ranked signals and current portfolio. Your ONLY job: approve or veto each signal.

Veto rules:
- Already have a position in the same sector → VETO (concentration)
- All positions same direction and this adds more → VETO unless very strong
- Stock's ATR suggests stop would be too tight for the price → VETO
- More than 3 total positions → VETO
- Energy sector → approve only if score >= 8

Output JSON: {"approved": [{"symbol": "X", "direction": "LONG", "strategy": "lnz3"}], "vetoed": [{"symbol": "Y", "reason": "why"}]}"""

def risk_check(ranked_signals: str, portfolio: str, api_key: str) -> Dict:
    """LLM 2: Approve or veto ranked signals."""
    user = f"RANKED SIGNALS:\n{ranked_signals}\n\nCURRENT PORTFOLIO:\n{portfolio}"
    raw = _call_llm(RISK_SYSTEM, user, api_key)
    try:
        return json.loads(raw)
    except:
        return {"approved": [], "vetoed": []}


# ═══════════════════════════════════════════════════════════════
# LLM 3: EXECUTOR — sets trade parameters
# ═══════════════════════════════════════════════════════════════

EXECUTOR_SYSTEM = """You are a trade execution specialist for NSE intraday.

You receive approved signals with ATR and price data. Your ONLY job: set the exact stop, target, and position size for each trade.

Rules:
- Stop must be at least 1.5x ATR from entry (anything tighter = noise stop)
- Stop must not exceed 2.5x ATR (anything wider = too much risk)
- Target = stop distance x 2.5 (minimum 2:1 reward-risk)
- Size: 5% of portfolio per trade. Reduce to 3% if ATR > 0.4% of price.
- If the stock gapped, use the gap boundary as a stop reference.

Output JSON: {"trades": [{"symbol": "X", "direction": "LONG", "stop": 100.0, "target": 105.0, "size_pct": 0.05, "reason": "why these levels"}]}"""

def executor_set_params(approved: str, price_data: str, api_key: str) -> List[Dict]:
    """LLM 3: Set stop/target/size for approved trades."""
    user = f"APPROVED TRADES:\n{approved}\n\nPRICE & ATR DATA:\n{price_data}"
    raw = _call_llm(EXECUTOR_SYSTEM, user, api_key)
    try:
        data = json.loads(raw)
        return data.get("trades", [])
    except:
        return []


# ═══════════════════════════════════════════════════════════════
# LLM 4: MONITOR — manages ONE open position
# ═══════════════════════════════════════════════════════════════

MONITOR_SYSTEM = """You are monitoring ONE open trading position on NSE intraday.

You see: the position details, current P&L, MFE (peak profit), how much profit has faded,
sector status, and volume data.

Your ONLY job: decide ONE action for THIS position right now.

Decision framework:
- HOLD: position working, sector supporting, no fade. Do nothing.
- TIGHTEN: profit fading (gave back >40% of peak) OR volume diverging. Move stop closer.
- PARTIAL: profit reached +0.4% or more. Take 30% off, move stop to breakeven.
- CLOSE: sector reversed against position OR profit collapsed (gave back >70%) OR clear reversal signal.

You must pick exactly ONE action. Be specific about why.

Output JSON: {"action": "hold|tighten|partial|close", "reason": "one sentence why"}"""

def monitor_position(position_context: str, api_key: str) -> Dict:
    """LLM 4: Monitor one position. Returns action."""
    raw = _call_llm(MONITOR_SYSTEM, position_context, api_key)
    try:
        return json.loads(raw)
    except:
        return {"action": "hold", "reason": "parse error, defaulting to hold"}


# ═══════════════════════════════════════════════════════════════
# LLM 5: JUDGE — reviews closed trades (stored, never fed back)
# ═══════════════════════════════════════════════════════════════

JUDGE_SYSTEM = """You are a trade reviewer. You see a completed trade with entry, exit, P&L, and market context.

Your ONLY job: write a brief review. What went right? What went wrong? One lesson learned.

This review is for the trading journal. It will NOT be shown to any other system component.
Be honest and specific, not generic.

Output JSON: {"went_right": "...", "went_wrong": "...", "lesson": "one specific takeaway"}"""

def judge_trade(trade_summary: str, api_key: str) -> Dict:
    """LLM 5: Review a closed trade. Stored in journal."""
    raw = _call_llm(JUDGE_SYSTEM, trade_summary, api_key)
    try:
        return json.loads(raw)
    except:
        return {"went_right": "", "went_wrong": "", "lesson": ""}
