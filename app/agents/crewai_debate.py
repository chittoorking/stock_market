"""
CrewAI Debate Agents - Bull vs Bear with full reasoning trace.

Each signal goes through a structured debate:
1. Bull Analyst: argues FOR, builds the strongest case
2. Bear Analyst: argues AGAINST, exposes every weakness
3. Moderator: reads both, scores each signal, outputs JSON

Every reasoning step is captured in the trace for post-analysis.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from crewai import Agent, Task, Crew, Process
from crewai.llm import LLM

from app.core.market_memory import MarketMemory

logger = logging.getLogger(__name__)


@dataclass
class DebateTrace:
    """Captures every reasoning step from the debate."""
    date: str = ""
    bar: int = 0
    signals_count: int = 0
    bull_reasoning: str = ""
    bear_reasoning: str = ""
    moderator_reasoning: str = ""
    ranked: List[Dict] = field(default_factory=list)
    # Monitor debate
    monitor_bull: str = ""
    monitor_bear: str = ""
    monitor_decision: str = ""


def _get_llm(api_key: str) -> LLM:
    """Create a CrewAI-compatible LLM."""
    return LLM(
        model="openai/gpt-4o-mini",
        api_key=api_key,
        temperature=0,
        max_tokens=800,
    )


# ======================================================================
# SCANNER DEBATE: Should we take these signals?
# ======================================================================

def debate_scanner(
    signals_text: str,
    sector_context: str,
    memory: MarketMemory,
    api_key: str,
) -> tuple[List[Dict], DebateTrace]:
    """
    CrewAI debate replaces one-shot scanner.
    Returns (ranked_signals, full_trace).
    """
    os.environ["OPENAI_API_KEY"] = api_key
    memory_text = memory.get_full_memory_snapshot()
    llm = _get_llm(api_key)
    trace = DebateTrace(signals_count=len(signals_text.split('\n')))

    # --- Agents ---
    bull = Agent(
        role="Bull Trading Analyst",
        goal="Build the strongest possible case FOR the best trading signals",
        backstory=(
            "You are an aggressive bull analyst for NSE intraday trading. "
            "You find reasons to take trades. You look for GREEN flags, sector alignment, "
            "volume confirmation, morning alignment, and historical edge. "
            "Be specific with numbers. Don't just say 'looks good' - cite RVOL values, "
            "sector percentages, breadth counts. Morning alignment (price moving WITH direction) "
            "is the #1 winner predictor - 100% of past winners had it."
        ),
        verbose=True,
        llm=llm,
    )

    bear = Agent(
        role="Bear Risk Analyst",
        goal="Find every reason to SKIP weak signals and protect capital",
        backstory=(
            "You are a ruthless bear analyst. You find RED flags: volume divergence, "
            "sector against direction, counter-gap trades, entry bar spikes, choppy price. "
            "Be specific. 'Unknown sector' alone is NOT a strong objection - focus on "
            "hard data like volume collapse, trading against gap AND morning move, "
            "or 0% historical WR. Minor cautions (YELLOW flags) are NOT deal-breakers."
        ),
        verbose=True,
        llm=llm,
    )

    moderator = Agent(
        role="Trading Moderator",
        goal="Read Bull and Bear arguments, score each signal 1-10, output ranked JSON",
        backstory=(
            "You are the final decision maker. You've read Bull's case and Bear's objections. "
            "You are a TRADER, not a risk manager - your job is to find winners.\n"
            "RULES:\n"
            "1. Morning alignment + 2 GREEN flags = score 7+ minimum\n"
            "2. RED flags are warnings, not vetoes. Only skip if trading against BOTH gap AND morning move\n"
            "3. You MUST take at least 1 signal if any has morning alignment + sector support\n"
            "4. Max 3 signals. Quality over quantity\n"
            "5. Output ONLY valid JSON: {\"ranked\": [{\"symbol\": \"X\", \"direction\": \"LONG\", "
            "\"strategy\": \"lnz3\", \"score\": 8, \"reason\": \"Bull: [strength]. Bear: [concern]. "
            "Decision: [why]\"}]}\n"
            "Only include score >= 6."
        ),
        verbose=True,
        llm=llm,
    )

    # --- Tasks (sequential: Bull -> Bear -> Moderator) ---
    market_data = (
        f"SIGNALS:\n{signals_text}\n\n"
        f"SECTOR FLOWS:\n{sector_context}\n\n"
        f"HISTORY:\n{memory_text}"
    )

    bull_task = Task(
        description=(
            f"Analyze these trading signals and build the BULL case for each.\n\n"
            f"{market_data}\n\n"
            f"For each signal, argue:\n"
            f"- Key GREEN flags and strengths\n"
            f"- Sector/volume/momentum support\n"
            f"- Historical edge if any\n"
            f"- Your conviction: HIGH/MEDIUM/LOW\n"
            f"Focus on the top 3-5 best signals."
        ),
        expected_output="Bull case for each signal with specific data points and conviction levels",
        agent=bull,
    )

    bear_task = Task(
        description=(
            f"Now tear apart these signals. Build the BEAR case against each.\n\n"
            f"{market_data}\n\n"
            f"For each signal the Bull liked, find:\n"
            f"- RED flags and specific risks\n"
            f"- Counter-evidence (volume divergence, sector against, etc.)\n"
            f"- Historical failures if any\n"
            f"- Your danger level: HIGH/MEDIUM/LOW\n"
            f"Be ruthless but fair - 'unknown sector' alone is weak. Focus on hard data."
        ),
        expected_output="Bear case against each signal with specific risk factors and danger levels",
        agent=bear,
    )

    moderator_task = Task(
        description=(
            f"Read the Bull and Bear arguments above. Now decide which signals to take.\n\n"
            f"Score each signal 1-10 based on the debate.\n"
            f"Output ONLY valid JSON (no markdown, no explanation outside JSON):\n"
            f"{{\"ranked\": [{{\"symbol\": \"X\", \"direction\": \"LONG\", \"strategy\": \"lnz3\", "
            f"\"score\": 8, \"reason\": \"Bull: [key]. Bear: [key]. Decision: [why]\"}}]}}\n\n"
            f"Only include score >= 6. Max 3 signals."
        ),
        expected_output='Valid JSON with ranked signals, scores, and reasoning referencing both Bull and Bear',
        agent=moderator,
    )

    # --- Run Crew ---
    crew = Crew(
        agents=[bull, bear, moderator],
        tasks=[bull_task, bear_task, moderator_task],
        process=Process.sequential,
        verbose=True,
    )

    try:
        result = crew.kickoff()
        raw_output = str(result)

        # Capture reasoning from each task
        trace.bull_reasoning = str(bull_task.output) if bull_task.output else ""
        trace.bear_reasoning = str(bear_task.output) if bear_task.output else ""
        trace.moderator_reasoning = raw_output

        # Parse moderator JSON
        start = raw_output.find('{')
        end = raw_output.rfind('}') + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw_output[start:end])
            ranked = parsed.get("ranked", [])
        else:
            ranked = []

        # Clean symbol names (moderator sometimes puts direction in symbol)
        for r in ranked:
            sym = r.get("symbol", "")
            for prefix in ("LONG ", "SHORT "):
                if sym.startswith(prefix):
                    sym = sym[len(prefix):]
            r["symbol"] = sym.strip()

        trace.ranked = ranked
        return ranked, trace

    except Exception as e:
        logger.error("CrewAI debate failed: %s", e)
        trace.moderator_reasoning = f"ERROR: {e}"
        return [], trace


# ======================================================================
# MONITOR DEBATE: Should we hold, tighten, or close this position?
# ======================================================================

def debate_monitor(
    trade_node,  # TradeNode
    memory: MarketMemory,
    api_key: str,
    technical_data: str = "",
) -> tuple[Dict, DebateTrace]:
    """
    CrewAI debate for position monitoring.
    Returns (action_dict, trace).
    """
    os.environ["OPENAI_API_KEY"] = api_key
    llm = _get_llm(api_key)
    trace = DebateTrace()

    # Build position context
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
        f"P&L CURVE:\n{pnl_curve}\n\n"
        f"FADE ANALYSIS:\n{fade}\n\n"
        f"OBSERVATIONS:\n{observations}\n\n"
        f"SIMILAR PAST:\n{similar}\n\n"
        f"EXIT PATTERNS:\n{exit_patterns}"
    )

    # --- Agents ---
    bull_monitor = Agent(
        role="Position Bull Monitor",
        goal="Argue why this position should be HELD to its target",
        backstory=(
            "You defend open positions. CRITICAL FACT: 100% of target hits are profitable. "
            "The stop loss handles risk. Premature exits are the #1 profit killer - "
            "88% of monitor exits were unprofitable in backtesting. "
            "One bearish candle is NOT a reason to close. Sector dips are NORMAL. "
            "Volume fluctuations are NORMAL. The stop exists for a reason - let it work."
        ),
        verbose=True,
        llm=llm,
    )

    bear_monitor = Agent(
        role="Position Bear Monitor",
        goal="Find overwhelming evidence that this position should be closed",
        backstory=(
            "You look for REAL danger to open positions. But you have a HIGH bar - "
            "closing early was wrong 88% of the time. Only argue CLOSE if you see "
            "3+ converging signals: bearish engulfing + sector against + volume dying. "
            "Minor dips, single bearish candles, or neutral sectors are NOT enough."
        ),
        verbose=True,
        llm=llm,
    )

    monitor_mod = Agent(
        role="Position Decision Maker",
        goal="Decide HOLD/TIGHTEN/PARTIAL/CLOSE based on Bull vs Bear debate",
        backstory=(
            "DEFAULT IS HOLD. Only override if Bear has OVERWHELMING evidence.\n"
            "CLOSE: only if fade>80% AND multiple bearish signals AND sector against (ALL required)\n"
            "TIGHTEN: only if P&L positive AND fade>70%\n"
            "PARTIAL: only if P&L>+0.5% AND momentum clearly dying\n"
            "HOLD: for everything else. The stop loss handles risk.\n"
            "Output ONLY valid JSON: {\"action\": \"hold\", \"reason\": \"Bull: [X]. Bear: [Y]. Decision: [why]\"}"
        ),
        verbose=True,
        llm=llm,
    )

    bull_task = Task(
        description=(
            f"This position is open. Argue why it should be HELD.\n\n"
            f"{position_context}\n\n"
            f"TECHNICAL DATA:\n{technical_data}\n\n"
            f"Find every reason to hold: intact thesis, supportive patterns, favorable zone."
        ),
        expected_output="Bull case for holding the position with specific evidence",
        agent=bull_monitor,
    )

    bear_task = Task(
        description=(
            f"Challenge the Bull. Find reasons this position should be CLOSED or TIGHTENED.\n\n"
            f"{position_context}\n\n"
            f"TECHNICAL DATA:\n{technical_data}\n\n"
            f"Only argue CLOSE if you see 3+ converging danger signals. Single patterns don't count."
        ),
        expected_output="Bear case for closing/tightening with converging evidence or acknowledgment that hold is correct",
        agent=bear_monitor,
    )

    mod_task = Task(
        description=(
            f"Read Bull and Bear arguments. Decide: HOLD, TIGHTEN, PARTIAL, or CLOSE.\n"
            f"Default is HOLD unless Bear has 3+ converging reasons.\n"
            f"Output ONLY valid JSON (no markdown):\n"
            f"{{\"action\": \"hold|tighten|partial|close\", \"reason\": \"Bull: [X]. Bear: [Y]. Decision: [why]\"}}"
        ),
        expected_output='Valid JSON with action and reasoning referencing both Bull and Bear',
        agent=monitor_mod,
    )

    crew = Crew(
        agents=[bull_monitor, bear_monitor, monitor_mod],
        tasks=[bull_task, bear_task, mod_task],
        process=Process.sequential,
        verbose=True,
    )

    try:
        result = crew.kickoff()
        raw = str(result)

        trace.monitor_bull = str(bull_task.output) if bull_task.output else ""
        trace.monitor_bear = str(bear_task.output) if bear_task.output else ""
        trace.monitor_decision = raw

        start = raw.find('{')
        end = raw.rfind('}') + 1
        if start >= 0 and end > start:
            decision = json.loads(raw[start:end])
        else:
            decision = {"action": "hold", "reason": "parse error - defaulting to hold"}

        return decision, trace

    except Exception as e:
        logger.error("Monitor debate failed: %s", e)
        trace.monitor_decision = f"ERROR: {e}"
        return {"action": "hold", "reason": f"debate error: {e}"}, trace
