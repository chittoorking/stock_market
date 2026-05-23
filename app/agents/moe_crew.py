"""
Mixture of Experts — CrewAI agents, each a domain specialist.
10 experts analyze the trade from their angle.
Aggregator reads all 10 opinions and makes ONE decision.

Experts:
1. Volume Expert — RVOL, sustainability, block deals, delivery %
2. Sector Expert — leader flow, breadth, laggard confirmation
3. Price Structure Expert — candles, VWAP, noise, gaps
4. Momentum Expert — morning move, consecutive bars, EMA trend
5. Market Regime Expert — broad breadth, trending vs choppy day
6. Macro Expert — VIX, US overnight, global cues
7. Risk Expert — stop distance, R:R ratio, position sizing
8. Timing Expert — bar number, time of day, expiry effects
9. History Expert — past similar setups, win rate, what failed before
10. Contrarian Expert — what could go wrong, devil's advocate
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from crewai import Agent, Task, Crew, Process
from crewai.llm import LLM


def _get_llm(api_key: str) -> LLM:
    return LLM(
        model="openai/gpt-4.1-nano",
        api_key=api_key,
        temperature=0,
        max_tokens=600,
    )


def moe_scan(
    context: str,
    api_key: str,
) -> tuple[List[Dict], Dict]:
    """
    10 expert agents analyze signals. Aggregator picks 1 trade.
    context: full state dump (signals + sector + market + macro + history)
    Returns: (ranked_trades, expert_opinions)
    """
    os.environ["OPENAI_API_KEY"] = api_key
    llm = _get_llm(api_key)

    # ═══ 10 EXPERT AGENTS ═══

    volume_expert = Agent(
        role="Volume Expert",
        goal="Analyze volume patterns for each signal",
        backstory=(
            "You are a volume analysis specialist for NSE intraday trading. "
            "You evaluate: RVOL (relative volume vs 5-day avg), volume sustainability "
            "(is volume holding or dying?), entry bar volume spikes (block deals vs organic), "
            "and delivery percentage (institutional vs speculative). "
            "High RVOL with sustainability = conviction. Volume spike on single bar = block deal, no follow-through. "
            "State your assessment per signal with numbers."
        ),
        verbose=True, llm=llm,
    )

    sector_expert = Agent(
        role="Sector Expert",
        goal="Analyze sector flow and alignment for each signal",
        backstory=(
            "You are a sector rotation specialist. You evaluate: "
            "Is the sector leader moving in the same direction as the trade? "
            "How many laggards are following? What is sector breadth? "
            "A stock going LONG while its sector leader is down = fighting the flow. "
            "Strong sector alignment (leader +1%, 75%+ following) = institutional rotation into sector. "
            "State your assessment per signal with percentages."
        ),
        verbose=True, llm=llm,
    )

    price_expert = Agent(
        role="Price Structure Expert",
        goal="Analyze price action quality for each signal",
        backstory=(
            "You are a price action specialist. You evaluate: "
            "Opening bar body ratio (decisive vs indecisive), gap alignment with direction, "
            "VWAP position (above = buyers zone, below = sellers), noise ratio (clean trend vs chop), "
            "candle patterns at entry bar, Camarilla pivot levels (S3 support, R3 resistance). "
            "Clean bars with 70%+ body ratio = conviction. Noise ratio > 3.5 = whipsaw territory. "
            "State your assessment per signal with exact levels."
        ),
        verbose=True, llm=llm,
    )

    momentum_expert = Agent(
        role="Momentum Expert",
        goal="Analyze momentum and trend strength for each signal",
        backstory=(
            "You are a momentum specialist. You evaluate: "
            "Morning move alignment (is price moving WITH the trade direction since open?), "
            "consecutive direction bars (3-4 = trend, 5+ = exhaustion risk), "
            "EMA9 vs EMA21 alignment, RSI level (overbought/oversold). "
            "Morning alignment is the #1 predictor from 4 years of data. "
            "State your assessment per signal with momentum numbers."
        ),
        verbose=True, llm=llm,
    )

    market_expert = Agent(
        role="Market Regime Expert",
        goal="Analyze the broad market context for today",
        backstory=(
            "You are a market regime specialist. You evaluate: "
            "What % of all 45 stocks are moving up vs down right now? "
            "Is this a trending day (70%+ one direction) or choppy (40-60% split)? "
            "On trending days, trade WITH the trend. On choppy days, fewer trades survive. "
            "State your assessment of the overall market regime with breadth %."
        ),
        verbose=True, llm=llm,
    )

    macro_expert = Agent(
        role="Macro Expert",
        goal="Analyze macro conditions affecting today's trading",
        backstory=(
            "You are a macro analyst. You evaluate: "
            "India VIX level (>22 = high fear, <13 = calm trending), "
            "US overnight return (S&P500, Nasdaq — positive = global risk-on), "
            "Nifty previous day return (strong continuation vs exhaustion). "
            "High VIX = wider stops needed, lower conviction. "
            "State your assessment of macro environment."
        ),
        verbose=True, llm=llm,
    )

    risk_expert = Agent(
        role="Risk Expert",
        goal="Evaluate risk-reward for each signal",
        backstory=(
            "You are a risk management specialist. You evaluate: "
            "What is the ATR% (average true range as % of price)? "
            "How wide is the stop? What R:R ratio is achievable? "
            "Is the entry too extended from the opening range? "
            "You need at least 2:1 R:R to justify the trade after charges (Rs 84/trade). "
            "A trade with 0.3% avg win needs minimum 2.5:1 R:R to survive charges. "
            "State your risk assessment with exact stop/target distances."
        ),
        verbose=True, llm=llm,
    )

    timing_expert = Agent(
        role="Timing Expert",
        goal="Evaluate entry timing for each signal",
        backstory=(
            "You are a timing specialist. You evaluate: "
            "What bar number is the entry? (bar 1-3 = early, may not be confirmed. "
            "bar 5-8 = confirmed. bar 10+ = late, limited runway). "
            "What day of week? (Monday best WR historically). "
            "Is this an expiry Thursday? (higher volatility, mean reversion at bar 10). "
            "How much time is left until 3:15 PM close? "
            "State your timing assessment."
        ),
        verbose=True, llm=llm,
    )

    history_expert = Agent(
        role="History Expert",
        goal="Check if this setup has worked or failed before",
        backstory=(
            "You are a pattern recognition specialist. You check: "
            "Has this exact strategy (lnz3, gdr4, aft7, etc.) been profitable historically? "
            "Has this stock been reliable for this type of setup? "
            "From 4 years of data: some stocks are consistently predictable (NTPC 64% WR), "
            "others are traps (NESTLEIND 47% WR). "
            "What does the market memory say about similar past trades? "
            "State what history tells us about this setup."
        ),
        verbose=True, llm=llm,
    )

    contrarian_expert = Agent(
        role="Contrarian Expert",
        goal="Find what could go WRONG with each signal",
        backstory=(
            "You are the devil's advocate. Your job: "
            "What is the biggest risk nobody mentioned? "
            "Is this trade too obvious (crowded)? "
            "Is there a gap that could fill against us? "
            "Is the sector rotation ending? "
            "Is this a block deal spike that will reverse? "
            "From 4 years: 70% of losses went +0.2% favorable before reversing. "
            "What would make this trade reverse after initial move? "
            "Be specific about the failure scenario."
        ),
        verbose=True, llm=llm,
    )

    # ═══ 5 MORE EXPERTS ═══

    relative_strength_expert = Agent(
        role="Relative Strength Expert",
        goal="Rank which signal's stock is strongest/weakest today",
        backstory=(
            "You compare stocks' intraday performance. "
            "Which stock is leading the market today? Which is lagging? "
            "For LONG: pick the strongest stock. For SHORT: pick the weakest. "
            "A stock ranked top 20% in relative strength has momentum. "
            "Bottom 20% is weak for a reason. State rank and % move."
        ),
        verbose=True, llm=llm,
    )

    gap_expert = Agent(
        role="Gap Analysis Expert",
        goal="Analyze gap behavior for each signal",
        backstory=(
            "You specialize in gap analysis. "
            "Gap aligned with trade direction = tailwind. Gap against = headwind. "
            "Large gaps (>1.5%) tend to fill 70% of the time. "
            "Small gaps (<0.3%) are noise. "
            "Is the trade going WITH the gap or fading it? "
            "State gap size, direction, and alignment."
        ),
        verbose=True, llm=llm,
    )

    volatility_expert = Agent(
        role="Volatility Expert",
        goal="Assess volatility regime and position sizing",
        backstory=(
            "You evaluate current vs historical volatility. "
            "ATR% tells you how much the stock moves per bar. "
            "High ATR (>0.5%) = needs wider stops = smaller position. "
            "Low ATR (<0.2%) = tight stops possible = better R:R. "
            "Is volatility expanding (breakout) or contracting (squeeze)? "
            "State ATR%, ORB range %, and volatility assessment."
        ),
        verbose=True, llm=llm,
    )

    level_expert = Agent(
        role="Key Levels Expert",
        goal="Identify support/resistance levels near entry",
        backstory=(
            "You map key price levels from the data: "
            "Camarilla S3/R3 (institutional S/R from prev day range), "
            "Previous day high/low, VWAP, opening range high/low. "
            "Is the entry near support (good for long) or resistance (good for short)? "
            "How much room to target before hitting next level? "
            "State exact levels and distances."
        ),
        verbose=True, llm=llm,
    )

    pattern_expert = Agent(
        role="Candlestick Pattern Expert",
        goal="Read candle patterns at and before entry",
        backstory=(
            "You read candlestick patterns: "
            "Marubozu (strong body, no wicks = conviction), "
            "Doji (indecision = wait), "
            "Shooting star / Hammer (reversal signals), "
            "Engulfing (trend change). "
            "What does the entry bar look like? Body ratio? Wick ratio? "
            "What about the bar before entry? "
            "State pattern name and what it implies."
        ),
        verbose=True, llm=llm,
    )

    # ═══ AGGREGATOR ═══
    aggregator = Agent(
        role="Trade Aggregator",
        goal="Read all 10 expert opinions. Pick exactly 1 trade or SKIP.",
        backstory=(
            "You are a senior trader who has listened to 15 domain experts analyze today's signals. "
            "Your job: synthesize their insights and make ONE decision.\n\n"
            "You don't just count votes. You WEIGH the arguments:\n"
            "- If Risk Expert says R:R is bad, that matters more than 5 experts saying TAKE\n"
            "- If Contrarian found a critical structural flaw, respect it\n"
            "- If Volume + Momentum + Sector all agree, that's strong convergence\n"
            "- If experts disagree, understand WHY and use your judgment\n\n"
            "Pick exactly 1 trade or SKIP. You are putting real money on this.\n"
            "Output ONLY valid JSON:\n"
            '{"trade": {"symbol":"STOCKNAME","direction":"LONG or SHORT","strategy":"stratname",'
            '"confidence":85,"reasoning":"2-3 sentence synthesis of why"}}\n'
            "Or: {\"trade\": null, \"reasoning\": \"Why no trade today\"}"
        ),
        verbose=True, llm=llm,
    )

    # ═══ TASKS ═══
    expert_tasks = []
    experts = [
        volume_expert, sector_expert, price_expert, momentum_expert,
        market_expert, macro_expert, risk_expert, timing_expert,
        history_expert, contrarian_expert,
        relative_strength_expert, gap_expert, volatility_expert,
        level_expert, pattern_expert,
    ]

    for expert in experts:
        task = Task(
            description=(
                f"Analyze these signals from YOUR domain perspective.\n\n"
                f"{context}\n\n"
                f"For each signal, give your expert assessment. "
                f"End with: VERDICT per signal: TAKE / SKIP / WEAK"
            ),
            expected_output=f"Expert assessment from {expert.role} with TAKE/SKIP/WEAK per signal",
            agent=expert,
        )
        expert_tasks.append(task)

    agg_task = Task(
        description=(
            "You have heard 15 experts. Now synthesize and decide.\n"
            "Which signal has the strongest CONVERGENT case across experts?\n"
            "Weigh quality of arguments, not count of votes.\n"
            "Pick 1 trade or SKIP.\n"
            "Output ONLY valid JSON (no markdown):\n"
            '{"trade": {"symbol":"STOCKNAME","direction":"LONG or SHORT","strategy":"stratname",'
            '"confidence":85,"reasoning":"synthesis of expert insights"}}\n'
            'Or: {"trade": null, "reasoning": "Why skip"}'
        ),
        expected_output="Valid JSON with trade decision and expert vote summary",
        agent=aggregator,
    )

    crew = Crew(
        agents=experts + [aggregator],
        tasks=expert_tasks + [agg_task],
        process=Process.sequential,
        verbose=False,  # Individual agents are verbose, crew is not
    )

    result = crew.kickoff()
    raw = str(result.raw) if hasattr(result, 'raw') else str(result)

    # Parse result
    try:
        # Find JSON in output
        start = raw.find('{')
        end = raw.rfind('}') + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            trade = parsed.get("trade")
            if trade:
                return [trade], parsed
            else:
                return [], parsed
    except (json.JSONDecodeError, Exception) as e:
        print(f"  MoE parse error: {e}")
        print(f"  Raw: {raw[:200]}")

    return [], {"raw": raw[:500]}


def moe_monitor(
    position_context: str,
    api_key: str,
) -> Dict:
    """
    Simplified monitor with 3 key experts for open positions.
    """
    os.environ["OPENAI_API_KEY"] = api_key
    llm = _get_llm(api_key)

    hold_expert = Agent(
        role="Hold Expert",
        goal="Argue why this position should be HELD",
        backstory=(
            "You defend open positions. 100% of target hits are profitable. "
            "Premature exits killed 88% of monitor exits in backtesting. "
            "The stop loss handles risk. Let it work."
        ),
        verbose=True, llm=llm,
    )

    danger_expert = Agent(
        role="Danger Expert",
        goal="Find if this position is in REAL danger",
        backstory=(
            "You look for convergent danger: bearish engulfing + sector against + volume dying. "
            "Single signals are noise. You need 3+ converging to recommend close. "
            "CRITICAL: if P&L went +0.2% and is now fading, recommend TIGHTEN to break-even. "
            "70% of losses went +0.2% favorable before reversing (4-year data)."
        ),
        verbose=True, llm=llm,
    )

    decision_maker = Agent(
        role="Monitor Decision",
        goal="Decide HOLD or TIGHTEN or CLOSE",
        backstory=(
            "Default is HOLD. Only TIGHTEN if P&L was positive and fading. "
            "Only CLOSE if 3+ converging danger signals. "
            "Output ONLY JSON: {\"action\":\"hold|tighten|close\",\"reason\":\"...\"}"
        ),
        verbose=True, llm=llm,
    )

    tasks = [
        Task(description=f"Position:\n{position_context}\nArgue HOLD.", expected_output="Hold case", agent=hold_expert),
        Task(description=f"Position:\n{position_context}\nFind danger.", expected_output="Danger assessment", agent=danger_expert),
        Task(description="Read Hold and Danger experts. Decide.\nOutput JSON only.", expected_output="JSON decision", agent=decision_maker),
    ]

    crew = Crew(agents=[hold_expert, danger_expert, decision_maker], tasks=tasks, process=Process.sequential, verbose=False)
    result = crew.kickoff()
    raw = str(result.raw) if hasattr(result, 'raw') else str(result)

    try:
        start = raw.find('{')
        end = raw.rfind('}') + 1
        if start >= 0:
            return json.loads(raw[start:end])
    except:
        pass
    return {"action": "hold", "reason": "parse_error"}
