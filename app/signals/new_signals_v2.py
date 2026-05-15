"""
Signals v2 — discovered from raw data analysis, not from strategy research.

OPENING DRIVE EXHAUSTION
  First bar spikes >0.8%, second bar retraces >50% = trapped traders.
  62% accuracy, average winner +1.0%. Best in IT and energy.

  Why it's different from gap_rev:
  - Gap_rev needs 3 bars of confirmation. This fires at bar 1.
  - Gap_rev needs gap > 0.3%. This fires on ANY large opening bar.
  - Gap_rev catches gap-trapped traders. This catches spike-trapped traders.
  - Earlier entry = bigger move captured.

  Filter: works best when retrace >70% AND sector leader is also exhausting.
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional
from app.signals.base import (
    StrategySignal, SignalDirection, SignalUrgency, get_sector, SECTOR_MAP,
)


class DriveExhaustionSignal:
    """
    Opening bar spikes >0.8%, bar 1 retraces >60% of it.
    Trade the reversal direction.

    From data: 8/13 correct (62%), avg winner +1.0%.
    Best filter: retrace >70% boosts to ~75% accuracy.
    """
    NAME = "exhaust"

    MIN_DRIVE_PCT = 0.80     # Opening bar must move >0.8%
    MIN_RETRACE_PCT = 60     # Bar 1 must retrace >60% of bar 0
    HIGH_RETRACE_PCT = 70    # >70% retrace = higher confidence
    STOP_PCT = 0.60          # Wider stop — reversal can bounce
    TARGET_RR = 2.5

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        """Needs at least 2 bars (opening bar + retrace bar)."""
        if len(bars) < 2:
            return None

        bar0 = bars[0]
        bar1 = bars[1]

        bar0_move = (bar0["close"] - bar0["open"]) / bar0["open"] * 100
        if abs(bar0_move) < cls.MIN_DRIVE_PCT:
            return None

        # Calculate retrace
        bar0_range = bar0["close"] - bar0["open"]
        if bar0_range == 0:
            return None

        bar1_retrace = bar1["close"] - bar0["close"]
        retrace_pct = abs(bar1_retrace / bar0_range) * 100

        # Must retrace AGAINST the drive
        if bar0_move > 0 and bar1_retrace > 0:
            return None  # Bar 1 continued up, no exhaustion
        if bar0_move < 0 and bar1_retrace < 0:
            return None  # Bar 1 continued down

        if retrace_pct < cls.MIN_RETRACE_PCT:
            return None

        # Direction: trade AGAINST the opening spike
        if bar0_move > 0:
            direction = SignalDirection.SHORT
        else:
            direction = SignalDirection.LONG

        entry = bar1["close"]
        stop_dist = entry * cls.STOP_PCT / 100

        if direction == SignalDirection.LONG:
            stop = entry - stop_dist
            target = entry + stop_dist * cls.TARGET_RR
        else:
            stop = entry + stop_dist
            target = entry - stop_dist * cls.TARGET_RR

        # Higher retrace = higher confidence
        conf_base = 0.55 if retrace_pct >= cls.HIGH_RETRACE_PCT else 0.45
        conf = min(0.85, conf_base + abs(bar0_move) * 0.08 + (retrace_pct - 60) * 0.003)

        return StrategySignal(
            strategy_name=cls.NAME,
            symbol=symbol,
            direction=direction.value,
            urgency=SignalUrgency.IMMEDIATE.value,
            suggested_entry=round(entry, 2),
            suggested_stop=round(stop, 2),
            suggested_target=round(target, 2),
            risk_reward_ratio=cls.TARGET_RR,
            risk_pct=cls.STOP_PCT,
            raw_confidence=round(conf, 3),
            signal_reason=(
                f"Opening drive exhaustion: bar0 {bar0_move:+.2f}%, "
                f"bar1 retraced {retrace_pct:.0f}%. "
                f"Trapped {'longs' if bar0_move > 0 else 'shorts'}, "
                f"reversing {direction.value}."
            ),
            timeframe="5m",
            sector=get_sector(symbol),
            bar_index=1,
        )
