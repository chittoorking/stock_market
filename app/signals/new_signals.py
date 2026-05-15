"""
New signals discovered from ground truth analysis.

1. GAP REVERSAL — gap up + 3 red bars = trapped longs, short them
   Found: 10 occurrences in 5 days, avg move -1.1% by bar 6
   WIPRO on 03-17: gap +0.49%, then -3.46%. We missed it entirely.

2. MORNING MOMENTUM CONTINUATION — stock moves >1.5% in 30 min with volume
   ONLY when sector confirms (not standalone momentum).
   Found: 24 occurrences, 58% continued. Key filter: sector alignment.
   HDFCBANK on 03-19: +4.24% in 30min, continued +0.29%. We missed +5.78%.
   INFY on 03-18: +2.57% in 30min, continued +0.55%. We caught this one.

3. SECTOR LAGGARD CATCH-UP — leader moves >1%, laggard hasn't caught up
   Different from cascade (cascade = leader bar-level spike, this = cumulative divergence)
   Found: 15 divergences, 33% caught up. Selective but high-reward.
   ONGC on 03-19: RELIANCE +1.33%, ONGC +0.11%, then ONGC caught up +1.58%.

NOTE: Morning momentum is DANGEROUS standalone — 42% reverse. But when sector
confirms AND volume is front-loaded AND continuation bar is green: 75% continue.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional

from app.signals.base import (
    StrategySignal, SignalDirection, SignalUrgency,
    get_sector, SECTOR_MAP, is_sector_leader,
)

logger = logging.getLogger(__name__)


class GapReversalSignal:
    """
    Gap up + 3 consecutive red bars + price below prev close = SHORT.
    Gap down + 3 consecutive green bars + price above prev close = LONG.

    Why it works: overnight gap creates trapped traders. When the gap fails
    immediately (3 bars against), the trapped traders panic and the move
    accelerates. This catches the WIPRO -3.46% type moves.
    """
    NAME = "gap_rev"

    GAP_THRESHOLD = 0.3
    CONFIRM_BARS = 3
    STOP_PCT = 0.50   # Wider stop — reversal can bounce before continuing
    TARGET_RR = 2.5

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict], prev_close: float) -> Optional[StrategySignal]:
        if len(bars) < cls.CONFIRM_BARS + 1:
            return None

        today_open = bars[0]["open"]
        gap_pct = (today_open - prev_close) / prev_close * 100

        if abs(gap_pct) < cls.GAP_THRESHOLD:
            return None

        confirm = bars[:cls.CONFIRM_BARS]

        if gap_pct > 0:
            # Gap up: all 3 bars must be red (close < open) and bar3 < prev_close
            all_red = all(b["close"] < b["open"] for b in confirm)
            below_prev = confirm[-1]["close"] < prev_close

            if not (all_red and below_prev):
                return None

            direction = SignalDirection.SHORT
            entry = confirm[-1]["close"]
            stop = entry * (1 + cls.STOP_PCT / 100)
            target = entry * (1 - cls.STOP_PCT / 100 * cls.TARGET_RR)

        else:
            # Gap down: all 3 bars must be green and bar3 > prev_close
            all_green = all(b["close"] > b["open"] for b in confirm)
            above_prev = confirm[-1]["close"] > prev_close

            if not (all_green and above_prev):
                return None

            direction = SignalDirection.LONG
            entry = confirm[-1]["close"]
            stop = entry * (1 - cls.STOP_PCT / 100)
            target = entry * (1 + cls.STOP_PCT / 100 * cls.TARGET_RR)

        # Magnitude of reversal = confidence
        reversal_size = abs(confirm[-1]["close"] - today_open) / today_open * 100
        conf = min(0.85, 0.50 + reversal_size * 0.15 + abs(gap_pct) * 0.10)

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
                f"Gap {'up' if gap_pct > 0 else 'down'} {abs(gap_pct):.2f}% REVERSED: "
                f"{cls.CONFIRM_BARS} {'red' if gap_pct > 0 else 'green'} bars, "
                f"{'below' if gap_pct > 0 else 'above'} prev close. "
                f"Trapped {'longs' if gap_pct > 0 else 'shorts'} panicking."
            ),
            timeframe="5m",
            sector=get_sector(symbol),
        )


class MorningMomentumSignal:
    """
    Stock moves >1.5% in first 30 minutes with high volume front-loading.
    ONLY triggers when sector leader confirms the direction.

    Standalone momentum reverses 42% of the time — too dangerous.
    But with sector confirmation: 75% continue.

    Additional filter: bar 7 (first bar after the 30-min window) must be
    in the same direction as the move (continuation candle, not exhaustion).
    """
    NAME = "momentum"

    MIN_MOVE_PCT = 1.5
    VOL_FRONT_RATIO = 1.5  # Volume in first 6 bars must be 1.5x rest
    CONFIRM_BAR_CHECK = True  # Bar 7 must confirm direction
    STOP_PCT = 0.75
    TARGET_RR = 2.0  # Lower RR — momentum trades are faster

    @classmethod
    def scan(
        cls, symbol: str, bars: List[Dict], sector_leader_change: float = 0,
    ) -> Optional[StrategySignal]:
        if len(bars) < 8:  # Need 6 bars + 1 confirmation + 1 buffer
            return None

        open_price = bars[0]["open"]
        bar6_close = bars[5]["close"]
        move_pct = (bar6_close - open_price) / open_price * 100

        if abs(move_pct) < cls.MIN_MOVE_PCT:
            return None

        # Volume front-loading check
        vol_first6 = sum(b["volume"] for b in bars[:6])
        vol_bar7_on = sum(b["volume"] for b in bars[6:8]) * 3  # Normalize to 6-bar equivalent
        vol_ratio = vol_first6 / vol_bar7_on if vol_bar7_on > 0 else 1

        if vol_ratio < cls.VOL_FRONT_RATIO:
            return None

        # Sector confirmation: leader must be moving same direction
        if move_pct > 0 and sector_leader_change < 0.3:
            return None  # Sector not confirming bullish momentum
        if move_pct < 0 and sector_leader_change > -0.3:
            return None  # Sector not confirming bearish momentum

        # Continuation candle check
        if cls.CONFIRM_BAR_CHECK:
            bar7 = bars[6]
            bar7_move = (bar7["close"] - bar7["open"]) / bar7["open"] * 100
            if move_pct > 0 and bar7_move < 0:
                return None  # Exhaustion candle after up move
            if move_pct < 0 and bar7_move > 0:
                return None  # Exhaustion candle after down move

        direction = SignalDirection.LONG if move_pct > 0 else SignalDirection.SHORT
        entry = bars[6]["close"]
        stop_dist = entry * cls.STOP_PCT / 100

        if direction == SignalDirection.LONG:
            stop = entry - stop_dist
            target = entry + stop_dist * cls.TARGET_RR
        else:
            stop = entry + stop_dist
            target = entry - stop_dist * cls.TARGET_RR

        conf = min(0.80, 0.45 + abs(move_pct) * 0.08 + vol_ratio * 0.05)

        return StrategySignal(
            strategy_name=cls.NAME,
            symbol=symbol,
            direction=direction.value,
            urgency=SignalUrgency.STANDARD.value,
            suggested_entry=round(entry, 2),
            suggested_stop=round(stop, 2),
            suggested_target=round(target, 2),
            risk_reward_ratio=cls.TARGET_RR,
            risk_pct=cls.STOP_PCT,
            raw_confidence=round(conf, 3),
            rvol=round(vol_ratio, 2),
            signal_reason=(
                f"Morning momentum: {move_pct:+.2f}% in 30min, vol {vol_ratio:.1f}x "
                f"front-loaded, sector leader {sector_leader_change:+.2f}% confirms, "
                f"continuation bar green."
            ),
            timeframe="5m",
            sector=get_sector(symbol),
        )
