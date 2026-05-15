"""
AutoGen Debate Agents — Bull vs Bear debate for signal scoring.

Replaces the one-shot Scanner LLM with a structured debate:
1. Bull Agent argues FOR taking signals (highlights strengths)
2. Bear Agent argues AGAINST (highlights risks, red flags)
3. Moderator reads both sides, outputs final ranked JSON

The debate forces explicit reasoning about BOTH sides of every signal.
A loser that a one-shot scanner scores 8/10 gets demolished by the Bear
pointing out volume divergence + sector against + similar setup lost before.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Any, List, Optional

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.conditions import MaxMessageTermination
from autogen_ext.models.openai import OpenAIChatCompletionClient

from app.core.market_memory import MarketMemory

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# AGENT PROMPTS — each agent has a clear, narrow job
# ═══════════════════════════════════════════════════════════════

BULL_SYSTEM = """You are the BULL analyst for NSE intraday trading signals.
Your job: argue WHY each signal should be taken. Be a strong advocate.

For each signal, build the BEST case:
- What GREEN flags support this trade?
- Is the sector aligned? Is the leader moving with this direction?
- Does volume confirm? Is RVOL strong?
- Has this strategy/sector/stock won before?
- Is the morning move aligned with the direction?
- Any bullish candlestick patterns?
- Is EMA trend supporting? RSI in favorable zone?

Be specific. Reference exact numbers from the data. Don't just say "looks good" —
say "RVOL 2.8x with sector leader +1.2% and 4/5 laggards following."

Output your bull case for EACH signal as a structured argument.
Format:
BULL CASE FOR [symbol] [direction] [strategy]:
- Strength 1: ...
- Strength 2: ...
- Historical edge: ...
- Conviction: HIGH/MEDIUM/LOW
"""

BEAR_SYSTEM = """You are the BEAR analyst for NSE intraday trading signals.
Your job: argue WHY each signal should be SKIPPED. Be a ruthless critic.

For each signal, build the STRONGEST case against:
- What RED flags exist? Volume divergence? Sector against? Choppy price?
- Is this trading against the gap? Against the morning move?
- Entry bar volume spike? (Single institution dump, not organic)
- Volume collapsing? Breadth weak?
- Has this strategy/sector/stock LOST before?
- Any bearish candlestick patterns contradicting?
- Is EMA trend against? RSI in danger zone?
- Is the stock lagging its sector leader?

Be specific and ruthless. Every red flag is a reason to skip.
Your job is to PROTECT capital. The cost of missing a winner is low.
The cost of taking a loser is real money gone.

Output your bear case for EACH signal.
Format:
BEAR CASE AGAINST [symbol] [direction] [strategy]:
- Risk 1: ...
- Risk 2: ...
- Historical warning: ...
- Danger level: HIGH/MEDIUM/LOW
"""

MODERATOR_SYSTEM = """You are the MODERATOR who decides which signals to take after hearing Bull and Bear arguments.

You've just read the Bull's case FOR each signal and the Bear's case AGAINST.
Now you must decide: which signals are worth taking?

IMPORTANT: You are a TRADER, not a risk manager. Your job is to FIND winners, not avoid all risk.
Every trade has risks — the question is whether the EDGE outweighs the risk.
If you skip everything, you make 0% returns. That's WORSE than taking calculated risks.

DECISION FRAMEWORK:
1. Morning alignment (price moving WITH direction) is the #1 winner predictor — 100% of past winners had it. If a signal has morning alignment + at least 2 GREEN flags, it deserves score 7+.
2. RED flags are warnings, NOT automatic vetoes. Many winners had 1-2 red flags. Only SKIP if:
   - Trading against BOTH the gap AND the morning move (double-against)
   - Entry bar body < 20% AND volume collapsed (no participation at all)
   - 0% historical WR on this exact setup
3. Bear raising "unknown sector" or "YELLOW caution" are WEAK objections — don't let them veto.
4. You MUST take at least 1 signal if ANY has morning alignment + GREEN sector/volume support.
5. Max 3 signals. Prefer signals where Bull's case is backed by hard data (RVOL, sector %, breadth).

Output ONLY valid JSON:
{"ranked": [{"symbol": "X", "direction": "LONG", "strategy": "lnz3", "score": 8, "reason": "Bull: [key strength]. Bear raised [key concern] but [why it's overruled]. Debate conclusion: [final reasoning]"}]}

Only include signals with score >= 6. The "reason" MUST reference both Bull and Bear arguments."""


def _build_model_client(api_key: str, model: str = "gpt-4o-mini") -> OpenAIChatCompletionClient:
    """Create an OpenAI model client for AutoGen agents."""
    return OpenAIChatCompletionClient(
        model=model,
        api_key=api_key,
        temperature=0,
        max_tokens=800,
    )


async def _run_debate(
    signals_text: str,
    sector_text: str,
    memory_text: str,
    api_key: str,
) -> tuple[List[Dict], str]:
    """
    Run the Bull vs Bear debate asynchronously.
    Returns (ranked_signals, full_debate_transcript).
    """
    model_client = _build_model_client(api_key)

    bull = AssistantAgent(
        name="Bull",
        system_message=BULL_SYSTEM,
        model_client=model_client,
    )

    bear = AssistantAgent(
        name="Bear",
        system_message=BEAR_SYSTEM,
        model_client=model_client,
    )

    moderator = AssistantAgent(
        name="Moderator",
        system_message=MODERATOR_SYSTEM,
        model_client=model_client,
    )

    # RoundRobin: Bull -> Bear -> Moderator -> STOP
    # max_messages=4: task(1) + Bull(2) + Bear(3) + Moderator(4)
    team = RoundRobinGroupChat(
        participants=[bull, bear, moderator],
        termination_condition=MaxMessageTermination(max_messages=4),
    )

    # The task message contains ALL the data
    task = (
        f"TRADING SIGNALS FOR DEBATE:\n\n"
        f"SIGNALS (with enrichment flags):\n{signals_text}\n\n"
        f"SECTOR FLOWS:\n{sector_text}\n\n"
        f"HISTORICAL MEMORY:\n{memory_text}\n\n"
        f"Bull: argue FOR the best signals. "
        f"Bear: argue AGAINST and expose weaknesses. "
        f"Moderator: read both sides and output final ranked JSON."
    )

    transcript = []
    ranked = []

    result = await team.run(task=task)

    for msg in result.messages:
        transcript.append(f"[{msg.source}]: {msg.content}")

        # The last message (Moderator) should contain JSON
        if msg.source == "Moderator":
            try:
                # Try to parse JSON from moderator's response
                content = msg.content
                # Find JSON in the response
                start = content.find('{')
                end = content.rfind('}') + 1
                if start >= 0 and end > start:
                    parsed = json.loads(content[start:end])
                    ranked = parsed.get("ranked", [])
            except (json.JSONDecodeError, Exception) as e:
                logger.error("Failed to parse Moderator JSON: %s", e)
                ranked = []

    await model_client.close()
    debate_text = "\n\n".join(transcript)
    return ranked, debate_text


def debate_scanner(
    signals_text: str,
    sector_context: str,
    memory: MarketMemory,
    api_key: str,
) -> tuple[List[Dict], str]:
    """
    Drop-in replacement for scanner_with_graph().
    Same interface: (signals_text, sector_context, memory, api_key) → ranked signals.
    Also returns the debate transcript for analysis.
    """
    memory_text = memory.get_full_memory_snapshot()

    try:
        ranked, transcript = asyncio.run(
            _run_debate(signals_text, sector_context, memory_text, api_key)
        )
    except Exception as e:
        logger.error("Debate failed: %s", e)
        return [], ""

    # Clean up symbol names — Moderator sometimes puts direction in symbol
    for r in ranked:
        sym = r.get("symbol", "")
        # Remove direction prefix if present (e.g., "SHORT HCLTECH" -> "HCLTECH")
        for prefix in ("LONG ", "SHORT "):
            if sym.startswith(prefix):
                sym = sym[len(prefix):]
        r["symbol"] = sym.strip()

    return ranked, transcript


# ═══════════════════════════════════════════════════════════════
# MONITOR DEBATE — Bull vs Bear for position management
# ═══════════════════════════════════════════════════════════════

MONITOR_BULL_SYSTEM = """You are the BULL monitor for an OPEN trading position.
Your job: argue WHY this position should be HELD or let run to target.

CRITICAL FACT: In our backtesting, 100% of target hits were profitable. The system's stops
protect against real losses. Premature exits are the #1 profit killer — most "scary" dips
are shakeouts before continuation.

Look at:
- Is the original thesis still intact? (sector, volume, direction)
- ANY bullish pattern (hammer, doji at support, engulfing) = hold signal
- EMA still aligned with direction = thesis intact
- RSI oversold during a dip = shakeout, not reversal
- MFE shows the trade CAN reach target — just needs patience
- Stop loss exists for a reason — let it do its job, don't pre-empt it

Be AGGRESSIVE in defending the position. The stop loss protects us.
Every premature exit is a potential winner thrown away."""

MONITOR_BEAR_SYSTEM = """You are the BEAR monitor for an OPEN trading position.
Your job: argue WHY this position should be CLOSED or TIGHTENED.

Look at:
- Fade analysis: how much profit has been given back?
- Bearish patterns forming (engulfing, shooting star, evening star)
- Volume divergence (price up but volume dropping)
- Sector turning against
- RSI overbought/oversold at bad levels
- P&L curve trending down — momentum is dying

Be the voice of risk management. Holding losers kills accounts.
Argue specifically why THIS position needs action NOW."""

MONITOR_MOD_SYSTEM = """You decide: HOLD, TIGHTEN, PARTIAL, or CLOSE this position.

You've heard the Bull (hold/run) and Bear (close/tighten) arguments.

CRITICAL BIAS: DEFAULT TO HOLD. The system has stop losses that will protect against real losses.
Closing early is almost always wrong — backtesting shows 88% of monitor exits are unprofitable.
Only close if the Bear presents OVERWHELMING evidence (3+ strong bearish signals converging).

DECISION FRAMEWORK:
- DEFAULT is HOLD — the stop loss handles risk management
- CLOSE only if: fade > 80% AND bearish engulfing AND sector turned against AND volume divergence (ALL of these, not just one)
- TIGHTEN only if: P&L positive AND fade > 70% (protect profit, don't abandon position)
- PARTIAL only if: P&L > +0.5% AND momentum clearly dying
- HOLD for everything else — especially if P&L is negative but stop hasn't been hit

One bearish candle is NOT a reason to close. Sector neutral is NOT a reason to close.
Volume dip is NOT a reason to close. These are NORMAL fluctuations.

Output ONLY valid JSON:
{"action": "hold|tighten|partial|close", "reason": "Bull argued [X]. Bear argued [Y]. Decision: [why]."}"""


async def _run_monitor_debate(
    position_context: str,
    technical_data: str,
    api_key: str,
) -> tuple[Dict, str]:
    """Run Bull vs Bear debate for position monitoring."""
    model_client = _build_model_client(api_key)

    bull = AssistantAgent(
        name="Bull_Monitor",
        system_message=MONITOR_BULL_SYSTEM,
        model_client=model_client,
    )

    bear = AssistantAgent(
        name="Bear_Monitor",
        system_message=MONITOR_BEAR_SYSTEM,
        model_client=model_client,
    )

    moderator = AssistantAgent(
        name="Monitor_Decision",
        system_message=MONITOR_MOD_SYSTEM,
        model_client=model_client,
    )

    team = RoundRobinGroupChat(
        participants=[bull, bear, moderator],
        termination_condition=MaxMessageTermination(max_messages=4),
    )

    task = (
        f"OPEN POSITION TO EVALUATE:\n\n"
        f"{position_context}\n\n"
        f"TECHNICAL ANALYSIS:\n{technical_data}\n\n"
        f"Bull_Monitor: argue for HOLDING. Bear_Monitor: argue for ACTION. "
        f"Monitor_Decision: decide with JSON output."
    )

    transcript = []
    decision = {"action": "hold", "reason": "debate inconclusive"}

    result = await team.run(task=task)

    for msg in result.messages:
        transcript.append(f"[{msg.source}]: {msg.content}")
        if msg.source == "Monitor_Decision":
            try:
                content = msg.content
                start = content.find('{')
                end = content.rfind('}') + 1
                if start >= 0 and end > start:
                    decision = json.loads(content[start:end])
            except (json.JSONDecodeError, Exception):
                decision = {"action": "hold", "reason": "parse error"}

    await model_client.close()
    return decision, "\n\n".join(transcript)


def debate_monitor(
    trade_node,  # TradeNode
    memory: MarketMemory,
    api_key: str,
    technical_data: str = "",
) -> tuple[Dict, str]:
    """
    Drop-in replacement for monitor_with_graph().
    Returns (action_dict, debate_transcript).
    """
    current = trade_node.query_current_state()
    pnl_curve = trade_node.query_pnl_curve()
    fade = trade_node.query_fade_analysis()
    observations = trade_node.query_observations()
    entry_ctx = trade_node.query_entry_context()

    similar = memory.query_similar_conditions(
        trade_node.sector, trade_node.direction, trade_node.strategy,
    )
    exit_patterns = memory.query_exit_pattern_history()

    position_context = (
        f"POSITION: {trade_node.direction} {trade_node.symbol} [{trade_node.strategy}]\n\n"
        f"ENTRY CONTEXT:\n{entry_ctx}\n\n"
        f"CURRENT STATE:\n{current}\n\n"
        f"P&L CURVE (last 10 bars):\n{pnl_curve}\n\n"
        f"FADE ANALYSIS:\n{fade}\n\n"
        f"OBSERVATIONS:\n{observations}\n\n"
        f"SIMILAR PAST TRADES:\n{similar}\n\n"
        f"EXIT PATTERNS:\n{exit_patterns}"
    )

    try:
        decision, transcript = asyncio.run(
            _run_monitor_debate(position_context, technical_data, api_key)
        )
    except Exception as e:
        logger.error("Monitor debate failed: %s", e)
        return {"action": "hold", "reason": f"debate error: {e}"}, ""

    return decision, transcript
