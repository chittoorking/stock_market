"""
Agent Reasoning Layer — where the LLM actually THINKS about trades.

This is the missing piece. Without this, the system is just a rule executor.
With this, the agent:
1. Sees ALL signals + market context + sector flows
2. Filters crowd signals (everything gaps up = don't take all 24)
3. Picks the BEST 1-3 trades based on confluence + sector strength
4. Adjusts stop/target based on the SPECIFIC stock's behavior
5. Explains its reasoning (auditable)

The agent doesn't override strategy signals — it CURATES them.
Strategies say WHAT. The agent decides WHETHER and HOW MUCH.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

from app.signals.base import (
    StrategySignal, SECTOR_MAP, get_sector, is_sector_leader,
)

logger = logging.getLogger(__name__)


@dataclass
class MarketSnapshot:
    """Everything the agent sees at decision time."""
    # Sector flows
    sector_flows: Dict[str, Dict] = field(default_factory=dict)
    # {sector: {leader, leader_change, laggards_following, total_laggards}}

    # All signals
    signals: List[StrategySignal] = field(default_factory=list)

    # Signal aggregates
    total_long: int = 0
    total_short: int = 0
    unique_symbols: int = 0
    is_broad_gap_day: bool = False  # >70% of signals same direction

    # Contradictions
    contradictions: List[str] = field(default_factory=list)
    # Confluence
    confluence_symbols: List[str] = field(default_factory=list)


@dataclass
class TradeDecision:
    """The agent's decision on a specific signal."""
    signal: StrategySignal
    take: bool = False
    reason: str = ""

    # Agent-adjusted parameters (can override signal defaults)
    adjusted_stop: Optional[float] = None
    adjusted_target: Optional[float] = None
    adjusted_size_pct: float = 0.05  # Position size as % of portfolio
    stop_style: str = "trail"  # "trail", "fixed", "delayed_trail"
    trail_delay_bars: int = 0  # Bars before trailing activates
    trail_levels: Tuple = (0.15, 0.30, 0.50)

    # Scoring
    sector_score: float = 0.0
    crowd_penalty: float = 0.0
    confluence_bonus: float = 0.0
    final_conviction: float = 0.0


def build_snapshot(
    signals: List[StrategySignal],
    all_data: Dict[str, List[Dict]],
    date: str,
    bar_idx: int,
) -> MarketSnapshot:
    """Build the full market snapshot at a specific moment."""
    snap = MarketSnapshot(signals=signals)

    # Count directions
    for s in signals:
        if s.direction == "LONG":
            snap.total_long += 1
        else:
            snap.total_short += 1
    snap.unique_symbols = len(set(s.symbol for s in signals))

    # Broad gap day detection
    total = snap.total_long + snap.total_short
    if total > 0:
        majority_pct = max(snap.total_long, snap.total_short) / total
        snap.is_broad_gap_day = majority_pct > 0.70 and total >= 10

    # Sector flows
    for sector_name, sector_info in SECTOR_MAP.items():
        leader = sector_info["leader"]
        leader_bars = [b for b in all_data.get(leader, []) if b["timestamp"][:10] == date]
        if not leader_bars or len(leader_bars) <= bar_idx:
            continue

        l_open = leader_bars[0]["open"]
        l_now = leader_bars[bar_idx]["close"]
        l_chg = (l_now - l_open) / l_open * 100

        following = 0
        for lag in sector_info["laggards"]:
            lag_bars = [b for b in all_data.get(lag, []) if b["timestamp"][:10] == date]
            if lag_bars and len(lag_bars) > bar_idx:
                lag_chg = (lag_bars[bar_idx]["close"] - lag_bars[0]["open"]) / lag_bars[0]["open"] * 100
                if (l_chg > 0 and lag_chg > 0) or (l_chg < 0 and lag_chg < 0):
                    following += 1

        snap.sector_flows[sector_name] = {
            "leader": leader,
            "leader_change": round(l_chg, 3),
            "laggards_following": following,
            "total_laggards": len(sector_info["laggards"]),
            "strength": following / len(sector_info["laggards"]) if sector_info["laggards"] else 0,
        }

    # Detect contradictions (same symbol, different directions)
    by_symbol: Dict[str, List[str]] = {}
    for s in signals:
        by_symbol.setdefault(s.symbol, []).append(s.direction)
    for sym, dirs in by_symbol.items():
        if len(set(dirs)) > 1:
            snap.contradictions.append(sym)

    # Detect confluence (same symbol, same direction, multiple strategies)
    by_sym_strat: Dict[str, List[str]] = {}
    for s in signals:
        by_sym_strat.setdefault(s.symbol, []).append(s.strategy_name)
    for sym, strats in by_sym_strat.items():
        if len(strats) > 1 and sym not in snap.contradictions:
            snap.confluence_symbols.append(sym)

    return snap


def agent_decide(
    snapshot: MarketSnapshot,
    max_trades: int = 3,
    portfolio_positions: Optional[Dict] = None,
) -> List[TradeDecision]:
    """
    The agent's decision-making logic. This is where intelligence lives.

    Rules the agent follows:
    1. On broad gap days, pick TOP 1-3 by sector strength, skip the rest
    2. Avoid contradictions
    3. Boost confluence
    4. Don't double up on same sector
    5. Adjust stops based on stock volatility
    6. Use delayed trailing for slow grinders
    """
    decisions = []
    taken_sectors = set()
    open_sectors = set()

    if portfolio_positions:
        for sym in portfolio_positions:
            open_sectors.add(get_sector(sym))

    # ─── Rule 1: Crowd filtering ───
    if snapshot.is_broad_gap_day:
        logger.info("Broad gap day detected (%d long, %d short). Selecting best by sector.",
                     snapshot.total_long, snapshot.total_short)

    # ─── Score each signal ───
    scored_signals: List[Tuple[float, StrategySignal, str]] = []

    for signal in snapshot.signals:
        score = 0.0
        reasons = []
        sector = get_sector(signal.symbol)
        sector_data = snapshot.sector_flows.get(sector, {})

        # Skip contradictions
        if signal.symbol in snapshot.contradictions:
            reasons.append("SKIP: contradiction on this symbol")
            decisions.append(TradeDecision(signal=signal, take=False,
                                          reason="Contradiction: multiple strategies disagree"))
            continue

        # Sector alignment score (0-3 points)
        if sector_data:
            leader_change = sector_data.get("leader_change", 0)
            sector_strength = sector_data.get("strength", 0)

            # Signal direction matches sector flow?
            if (signal.direction == "LONG" and leader_change > 0) or \
               (signal.direction == "SHORT" and leader_change < 0):
                score += abs(leader_change) * 0.5  # Bigger move = more conviction
                score += sector_strength * 1.0      # More laggards = stronger flow
                reasons.append(f"Sector {sector} aligned ({leader_change:+.2f}%, {sector_strength:.0%} following)")
            else:
                score -= 1.0
                reasons.append(f"Sector {sector} AGAINST signal ({leader_change:+.2f}%)")

        # Confluence bonus
        if signal.symbol in snapshot.confluence_symbols:
            score += 1.5
            reasons.append("Confluence: multiple strategies agree")

        # Crowd penalty
        if snapshot.is_broad_gap_day:
            score -= 0.5
            reasons.append("Crowd penalty: broad gap day")

        # Sector concentration penalty
        if sector in taken_sectors or sector in open_sectors:
            score -= 1.0
            reasons.append(f"Already exposed to {sector}")

        # Signal's own confidence
        score += signal.raw_confidence * 2.0

        scored_signals.append((score, signal, " | ".join(reasons)))

    # ─── Sort by score, take top N ───
    scored_signals.sort(key=lambda x: x[0], reverse=True)

    for score, signal, reasoning in scored_signals[:max_trades * 2]:
        if len([d for d in decisions if d.take]) >= max_trades:
            break

        sector = get_sector(signal.symbol)
        sector_data = snapshot.sector_flows.get(sector, {})

        if score < 1.0:
            decisions.append(TradeDecision(
                signal=signal, take=False,
                reason=f"Score too low ({score:.2f}): {reasoning}",
                final_conviction=score,
            ))
            continue

        # Already took a trade in this sector?
        if sector in taken_sectors:
            decisions.append(TradeDecision(
                signal=signal, take=False,
                reason=f"Sector {sector} already covered: {reasoning}",
                final_conviction=score,
            ))
            continue

        # ─── Determine stop style based on stock characteristics ───
        # Fast movers (sector leader, high gap) → standard trail
        # Slow grinders (laggards, small gap) → delayed trail (give it room)
        is_leader = is_sector_leader(signal.symbol)
        gap_size = abs(float(signal.signal_reason.split('%')[0].split()[-1])) if '%' in signal.signal_reason else 0.5

        if is_leader and gap_size > 1.0:
            stop_style = "trail"
            trail_delay = 0
            stop_pct = 0.50
            reasons_list = [f"Leader + large gap: standard trail @ {stop_pct}%"]
        elif sector_data.get("strength", 0) >= 0.75:
            stop_style = "delayed_trail"
            trail_delay = 6  # 30 min before trailing kicks in
            stop_pct = 0.75
            reasons_list = [f"Strong sector flow: delayed trail (6 bars) @ {stop_pct}%"]
        else:
            stop_style = "trail"
            trail_delay = 3
            stop_pct = 0.50
            reasons_list = [f"Moderate conviction: trail after 3 bars @ {stop_pct}%"]

        # Adjust position size based on conviction
        if score > 3.0:
            size_pct = 0.07  # 7% — high conviction
        elif score > 2.0:
            size_pct = 0.05  # 5% — standard
        else:
            size_pct = 0.03  # 3% — low conviction

        # Calculate adjusted stop/target
        entry = signal.suggested_entry
        adjusted_stop = entry * (1 - stop_pct / 100) if signal.direction == "LONG" else entry * (1 + stop_pct / 100)
        risk = abs(entry - adjusted_stop)
        adjusted_target = entry + risk * 2.5 if signal.direction == "LONG" else entry - risk * 2.5

        decisions.append(TradeDecision(
            signal=signal,
            take=True,
            reason=f"Score={score:.2f}: {reasoning} | {' | '.join(reasons_list)}",
            adjusted_stop=round(adjusted_stop, 2),
            adjusted_target=round(adjusted_target, 2),
            adjusted_size_pct=size_pct,
            stop_style=stop_style,
            trail_delay_bars=trail_delay,
            sector_score=abs(sector_data.get("leader_change", 0)),
            crowd_penalty=-0.5 if snapshot.is_broad_gap_day else 0,
            confluence_bonus=1.5 if signal.symbol in snapshot.confluence_symbols else 0,
            final_conviction=score,
        ))

        taken_sectors.add(sector)

    return decisions


def format_decisions_for_log(decisions: List[TradeDecision]) -> str:
    """Format decisions for human-readable logging."""
    lines = []
    taken = [d for d in decisions if d.take]
    skipped = [d for d in decisions if not d.take]

    if taken:
        lines.append(f"TAKING {len(taken)} trades:")
        for d in taken:
            lines.append(
                f"  + {d.signal.direction} {d.signal.symbol} [{d.signal.strategy_name}] "
                f"conviction={d.final_conviction:.2f} size={d.adjusted_size_pct:.0%} "
                f"stop_style={d.stop_style}"
            )
            lines.append(f"    {d.reason}")
    else:
        lines.append("NO TRADES — all signals below threshold.")

    if skipped:
        lines.append(f"\nSKIPPING {len(skipped)} signals:")
        for d in skipped[:5]:
            lines.append(f"  - {d.signal.direction} {d.signal.symbol} [{d.signal.strategy_name}]: {d.reason}")
        if len(skipped) > 5:
            lines.append(f"  ... and {len(skipped) - 5} more")

    return "\n".join(lines)
