"""
Trade Development Graph — tracks every trade through its entire lifecycle.

Every trade is a node with edges to:
- The signal that created it (which strategy, what confidence, what market state)
- Every bar since entry (price, volume, P&L at each point)
- Every context snapshot (sector flow at bar 6, 12, 18...)
- Every agent observation (volume divergence at bar 15, fade started at bar 21)
- The exit (what triggered it, what was the state)

This is NOT a log. It's a QUERYABLE graph. The LLM asks:
- "What's the P&L curve of this trade?"
- "When did the fade start?"
- "Was volume supporting when we entered?"
- "How does this compare to the last trade on this stock?"

The graph ANSWERS with data. The LLM REASONS from the answer.
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class BarSnapshot:
    """One bar's worth of data for an open trade."""
    bar_index: int
    timestamp: str
    price: float
    high: float
    low: float
    volume: int
    pnl_pct: float
    mfe_pct: float        # Running max favorable
    mae_pct: float        # Running max adverse
    fade_pct: float       # How much of MFE given back
    zone: str             # scratch/small_win/decent_win/strong_win/loss
    # Context at this bar
    sector_leader_change: float = 0
    sector_strength: float = 0
    volume_divergence: str = "none"
    regime: str = "unknown"


@dataclass
class TradeNode:
    """A single trade in the development graph."""
    trade_id: str
    symbol: str
    direction: str
    strategy: str
    sector: str

    # Entry context
    entry_price: float = 0
    entry_bar: int = 0
    entry_time: str = ""
    entry_signal_confidence: float = 0
    entry_sector_flow: float = 0      # Leader change at entry
    entry_sector_strength: float = 0  # Laggard following % at entry
    entry_rvol: float = 0
    entry_regime: str = "unknown"
    entry_vwap_position: str = ""
    entry_atr_pct: float = 0
    entry_gap_pct: float = 0

    # Development (bar-by-bar snapshots)
    snapshots: List[BarSnapshot] = field(default_factory=list)

    # Observations (agents write here, LLM reads)
    observations: List[Dict[str, Any]] = field(default_factory=list)

    # Exit
    exit_price: float = 0
    exit_bar: int = 0
    exit_reason: str = ""
    final_pnl_pct: float = 0
    max_mfe_pct: float = 0
    max_mae_pct: float = 0
    bars_held: int = 0
    partial_taken: bool = False
    partial_pnl: float = 0

    # Closed
    is_closed: bool = False

    def add_snapshot(self, snap: BarSnapshot):
        self.snapshots.append(snap)
        self.max_mfe_pct = max(self.max_mfe_pct, snap.mfe_pct)
        self.max_mae_pct = max(self.max_mae_pct, snap.mae_pct)

    def add_observation(self, bar: int, source: str, observation: str, data: Dict = None):
        self.observations.append({
            "bar": bar, "source": source,
            "observation": observation, "data": data or {},
        })

    # ─── Queries the LLM can ask ───

    def query_pnl_curve(self) -> str:
        """Returns P&L curve as text the LLM can read."""
        if not self.snapshots:
            return "No data yet."
        lines = []
        for s in self.snapshots[-10:]:  # Last 10 bars
            lines.append(f"bar {s.bar_index} [{s.timestamp}]: P&L={s.pnl_pct:+.3f}% MFE={s.mfe_pct:.3f}% fade={s.fade_pct:.0f}%")
        return "\n".join(lines)

    def query_current_state(self) -> str:
        """Returns current position state."""
        if not self.snapshots:
            return "No data."
        s = self.snapshots[-1]
        return (
            f"P&L: {s.pnl_pct:+.3f}% | MFE: {self.max_mfe_pct:.3f}% | "
            f"Fade: {s.fade_pct:.0f}% | Zone: {s.zone} | "
            f"Bars held: {len(self.snapshots)} | "
            f"Sector: leader {s.sector_leader_change:+.2f}% strength {s.sector_strength:.0%} | "
            f"Volume: {s.volume_divergence}"
        )

    def query_observations(self) -> str:
        """Returns all observations agents have made."""
        if not self.observations:
            return "No observations."
        return "\n".join(
            f"[bar {o['bar']}] {o['source']}: {o['observation']}"
            for o in self.observations[-5:]
        )

    def query_entry_context(self) -> str:
        """Returns why this trade was entered."""
        return (
            f"Strategy: {self.strategy} | Confidence: {self.entry_signal_confidence:.0%}\n"
            f"Sector flow: leader {self.entry_sector_flow:+.2f}%, {self.entry_sector_strength:.0%} following\n"
            f"RVOL: {self.entry_rvol:.1f}x | VWAP: {self.entry_vwap_position} | Regime: {self.entry_regime}\n"
            f"ATR: {self.entry_atr_pct:.2f}% | Gap: {self.entry_gap_pct:+.2f}%"
        )

    def query_fade_analysis(self) -> str:
        """When did fade start? How fast?"""
        if self.max_mfe_pct < 0.1:
            return "Never reached meaningful profit."

        peak_bar = None
        fade_start_bar = None
        for s in self.snapshots:
            if s.mfe_pct == self.max_mfe_pct and peak_bar is None:
                peak_bar = s.bar_index
            if peak_bar and s.fade_pct > 30 and fade_start_bar is None:
                fade_start_bar = s.bar_index

        if peak_bar is None:
            return "No peak detected."

        bars_since_peak = (self.snapshots[-1].bar_index - peak_bar) if self.snapshots else 0
        return (
            f"Peak at bar {peak_bar} (MFE {self.max_mfe_pct:.3f}%). "
            f"{'Fade started at bar ' + str(fade_start_bar) if fade_start_bar else 'No significant fade yet'}. "
            f"{bars_since_peak} bars since peak."
        )


class TradeDevGraph:
    """
    Manages all trade nodes. The system writes, the LLM queries.
    """
    def __init__(self):
        self._active: Dict[str, TradeNode] = {}   # symbol -> active trade
        self._closed: List[TradeNode] = []

    def open_trade(self, trade: TradeNode):
        self._active[trade.symbol] = trade

    def get_active(self, symbol: str) -> Optional[TradeNode]:
        return self._active.get(symbol)

    def get_all_active(self) -> Dict[str, TradeNode]:
        return dict(self._active)

    def close_trade(self, symbol: str, exit_price: float, exit_bar: int, exit_reason: str):
        trade = self._active.pop(symbol, None)
        if not trade:
            return None
        trade.exit_price = exit_price
        trade.exit_bar = exit_bar
        trade.exit_reason = exit_reason
        trade.bars_held = exit_bar - trade.entry_bar
        trade.is_closed = True

        if trade.direction == "LONG":
            trade.final_pnl_pct = (exit_price - trade.entry_price) / trade.entry_price * 100
        else:
            trade.final_pnl_pct = (trade.entry_price - exit_price) / trade.entry_price * 100

        self._closed.append(trade)
        return trade

    # ─── Queries for LLM ───

    def query_active_summary(self) -> str:
        """What positions are open right now?"""
        if not self._active:
            return "No open positions."
        lines = []
        for sym, t in self._active.items():
            lines.append(f"{t.direction} {sym} [{t.strategy}]: {t.query_current_state()}")
        return "\n".join(lines)

    def query_history_for_symbol(self, symbol: str) -> str:
        """What happened last time we traded this stock?"""
        past = [t for t in self._closed if t.symbol == symbol]
        if not past:
            return f"No previous trades on {symbol}."
        last = past[-1]
        return (
            f"Last trade: {last.direction} {symbol} [{last.strategy}] "
            f"P&L={last.final_pnl_pct:+.3f}% MFE={last.max_mfe_pct:.3f}% "
            f"Exit: {last.exit_reason} after {last.bars_held} bars"
        )

    def query_strategy_track_record(self, strategy: str) -> str:
        """How has this strategy performed?"""
        trades = [t for t in self._closed if t.strategy == strategy]
        if not trades:
            return f"No completed trades for {strategy}."
        wins = sum(1 for t in trades if t.final_pnl_pct > 0)
        total_pnl = sum(t.final_pnl_pct for t in trades)
        avg_mfe = sum(t.max_mfe_pct for t in trades) / len(trades)
        return (
            f"{strategy}: {len(trades)} trades, {wins} wins ({wins/len(trades):.0%} WR), "
            f"total P&L={total_pnl:+.3f}%, avg MFE={avg_mfe:.3f}%"
        )

    def query_sector_track_record(self, sector: str) -> str:
        """How has this sector performed for us?"""
        trades = [t for t in self._closed if t.sector == sector]
        if not trades:
            return f"No completed trades in {sector}."
        wins = sum(1 for t in trades if t.final_pnl_pct > 0)
        total_pnl = sum(t.final_pnl_pct for t in trades)
        return (
            f"{sector}: {len(trades)} trades, {wins} wins ({wins/len(trades):.0%} WR), "
            f"total P&L={total_pnl:+.3f}%"
        )

    def query_common_exit_patterns(self) -> str:
        """What are the most common exit patterns?"""
        if not self._closed:
            return "No closed trades."
        by_exit = {}
        for t in self._closed:
            by_exit.setdefault(t.exit_reason, []).append(t.final_pnl_pct)
        lines = []
        for reason, pnls in sorted(by_exit.items(), key=lambda x: -len(x[1])):
            avg = sum(pnls) / len(pnls)
            lines.append(f"{reason}: {len(pnls)} trades, avg P&L={avg:+.3f}%")
        return "\n".join(lines)
