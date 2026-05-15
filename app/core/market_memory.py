"""
Market Memory Graph — what happened before in similar conditions.

The LLM asks: "Has this happened before?" The graph answers with data.

Stores:
- Sector flow patterns and what followed
- Gap day outcomes (broad gap up → what happened by EOD?)
- Stock-level patterns (BPCL gapped down last time → stopped out)
- Time-of-day patterns (bar 6 entries vs bar 15 entries)
- Volume patterns and outcomes

The LLM doesn't carry this in its prompt. It QUERIES when it needs to.
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class DayMemory:
    """What happened on a specific trading day."""
    date: str
    market_gap_direction: str = ""    # "broad_gap_up", "broad_gap_down", "mixed"
    market_gap_pct: float = 0
    strongest_sector: str = ""
    weakest_sector: str = ""
    trades_taken: int = 0
    total_pnl: float = 0
    best_trade: str = ""
    worst_trade: str = ""
    regime: str = "unknown"


class MarketMemory:
    """
    Stores market history. LLMs query it, system writes to it.
    """

    def __init__(self):
        self._days: List[DayMemory] = []
        self._stock_outcomes: Dict[str, List[Dict]] = defaultdict(list)   # symbol -> [{pnl, strategy, sector_flow}]
        self._sector_outcomes: Dict[str, List[Dict]] = defaultdict(list)  # sector -> [{pnl, direction, strength}]
        self._strategy_outcomes: Dict[str, List[Dict]] = defaultdict(list) # strategy -> [{pnl, symbol, sector}]
        self._exit_outcomes: Dict[str, List[float]] = defaultdict(list)   # exit_reason -> [pnl]
        self._bar_outcomes: Dict[int, List[float]] = defaultdict(list)    # scan_bar -> [pnl]

    def record_day(self, day: DayMemory):
        self._days.append(day)

    def record_trade(self, trade: Dict[str, Any]):
        """Record a completed trade outcome."""
        sym = trade.get("symbol", "")
        self._stock_outcomes[sym].append({
            "pnl": trade.get("pnl", 0),
            "strategy": trade.get("strategy", ""),
            "direction": trade.get("direction", ""),
            "mfe": trade.get("mfe", 0),
            "exit_reason": trade.get("exit_reason", ""),
            "bars_held": trade.get("bars_held", 0),
            "date": trade.get("date", ""),
        })

        sector = trade.get("sector", "unknown")
        self._sector_outcomes[sector].append({
            "pnl": trade.get("pnl", 0),
            "direction": trade.get("direction", ""),
            "symbol": sym,
        })

        strategy = trade.get("strategy", "")
        self._strategy_outcomes[strategy].append({
            "pnl": trade.get("pnl", 0),
            "symbol": sym,
            "sector": sector,
        })

        self._exit_outcomes[trade.get("exit_reason", "unknown")].append(trade.get("pnl", 0))
        self._bar_outcomes[trade.get("scan_bar", 0)].append(trade.get("pnl", 0))

    # ─── Queries the LLM can ask ───

    def query_stock_history(self, symbol: str) -> str:
        """What happened when we traded this stock before?"""
        outcomes = self._stock_outcomes.get(symbol, [])
        if not outcomes:
            return f"Never traded {symbol} before."
        wins = sum(1 for o in outcomes if o["pnl"] > 0)
        total = sum(o["pnl"] for o in outcomes)
        last = outcomes[-1]
        return (
            f"{symbol}: {len(outcomes)} past trades, {wins} wins, total P&L={total:+.3f}%. "
            f"Last: {last['direction']} via {last['strategy']}, P&L={last['pnl']:+.3f}%, "
            f"exit={last['exit_reason']}, held {last['bars_held']} bars."
        )

    def query_sector_history(self, sector: str) -> str:
        """How has this sector performed for us?"""
        outcomes = self._sector_outcomes.get(sector, [])
        if not outcomes:
            return f"Never traded in {sector} sector."
        wins = sum(1 for o in outcomes if o["pnl"] > 0)
        total = sum(o["pnl"] for o in outcomes)
        symbols = list(set(o["symbol"] for o in outcomes))
        return (
            f"{sector}: {len(outcomes)} trades on {symbols}, "
            f"{wins} wins ({wins/len(outcomes):.0%}), total P&L={total:+.3f}%."
        )

    def query_strategy_history(self, strategy: str) -> str:
        """How has this strategy performed overall?"""
        outcomes = self._strategy_outcomes.get(strategy, [])
        if not outcomes:
            return f"No history for {strategy}."
        wins = sum(1 for o in outcomes if o["pnl"] > 0)
        total = sum(o["pnl"] for o in outcomes)
        sectors = list(set(o["sector"] for o in outcomes))
        return (
            f"{strategy}: {len(outcomes)} trades, {wins} wins ({wins/len(outcomes):.0%}), "
            f"total P&L={total:+.3f}%. Sectors: {sectors}."
        )

    def query_time_of_day(self, scan_bar: int) -> str:
        """How do entries at this time of day perform?"""
        outcomes = self._bar_outcomes.get(scan_bar, [])
        if not outcomes:
            return f"No history for bar {scan_bar} entries."
        wins = sum(1 for o in outcomes if o > 0)
        total = sum(outcomes)
        return (
            f"Bar {scan_bar} entries: {len(outcomes)} trades, "
            f"{wins} wins ({wins/len(outcomes):.0%}), total P&L={total:+.3f}%."
        )

    def query_similar_conditions(self, sector: str, direction: str, strategy: str) -> str:
        """Have we seen this exact setup before? (sector + direction + strategy)"""
        matches = []
        for o in self._strategy_outcomes.get(strategy, []):
            if o.get("sector") == sector:
                matches.append(o)
        if not matches:
            return f"No previous {strategy} trades in {sector} sector."
        wins = sum(1 for m in matches if m["pnl"] > 0)
        total = sum(m["pnl"] for m in matches)
        return (
            f"{strategy} in {sector}: {len(matches)} previous trades, "
            f"{wins} wins ({wins/len(matches):.0%}), P&L={total:+.3f}%."
        )

    def query_broad_gap_day_history(self) -> str:
        """What happened on previous broad gap days?"""
        gap_days = [d for d in self._days if d.market_gap_direction in ("broad_gap_up", "broad_gap_down")]
        if not gap_days:
            return "No previous broad gap days."
        lines = []
        for d in gap_days[-3:]:
            lines.append(
                f"{d.date}: {d.market_gap_direction} | Trades: {d.trades_taken} | "
                f"P&L: {d.total_pnl:+.3f}% | Best: {d.best_trade} | Worst: {d.worst_trade}"
            )
        return "\n".join(lines)

    def query_exit_pattern_history(self) -> str:
        """Which exit reasons make money?"""
        lines = []
        for reason, pnls in sorted(self._exit_outcomes.items(), key=lambda x: -sum(x[1])):
            avg = sum(pnls) / len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            lines.append(f"{reason}: {len(pnls)} exits, {wins} profitable, avg={avg:+.3f}%")
        return "\n".join(lines) if lines else "No exit history."

    def get_full_memory_snapshot(self) -> str:
        """Complete memory dump for LLM context (called once per scan, not per signal)."""
        lines = ["## Market Memory\n"]

        if self._days:
            lines.append("Recent days:")
            for d in self._days[-3:]:
                lines.append(f"  {d.date}: {d.regime} | P&L={d.total_pnl:+.3f}% | {d.trades_taken} trades")

        lines.append("\nStrategy performance:")
        for strat in ["lnz3", "gdr4", "aft7"]:
            outcomes = self._strategy_outcomes.get(strat, [])
            if outcomes:
                wins = sum(1 for o in outcomes if o["pnl"] > 0)
                total = sum(o["pnl"] for o in outcomes)
                lines.append(f"  {strat}: {len(outcomes)} trades, {wins/len(outcomes):.0%} WR, P&L={total:+.3f}%")

        lines.append("\nSector performance:")
        for sector in sorted(self._sector_outcomes):
            outcomes = self._sector_outcomes[sector]
            if outcomes:
                total = sum(o["pnl"] for o in outcomes)
                lines.append(f"  {sector}: {len(outcomes)} trades, P&L={total:+.3f}%")

        lines.append(f"\nExit patterns:")
        lines.append(self.query_exit_pattern_history())

        return "\n".join(lines)
