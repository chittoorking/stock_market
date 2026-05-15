"""
Volatility Agent — adjusts stop width based on stock's actual volatility.

Problem it solves: BPCL moves 0.5% per bar but has a 0.75% stop = dead in 2 bars.
TATASTEEL moves 0.1% per bar with 0.75% stop = plenty of room.

The fix: measure ATR (Average True Range) per stock and set stops as a
multiple of ATR, not a fixed percentage.

Stop = max(1.5 * ATR, min_stop_pct)

This means:
- Volatile stocks (BPCL, ADANIENT) get wider stops
- Calm stocks (ITC, HINDUNILVR) get tighter stops
- The risk per trade stays proportional to the stock's actual movement
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional
from app.agents.base import AgentOpinion, ActionType, Urgency


class VolatilityAgent:
    NAME = "volatility"

    ATR_PERIOD = 14         # Standard ATR lookback
    STOP_ATR_MULT = 2.0     # Stop = 2x ATR
    MIN_STOP_PCT = 0.30     # Never less than 0.30%
    MAX_STOP_PCT = 2.00     # Never more than 2.00%
    TARGET_RR = 2.5         # Risk-reward ratio

    def calculate_atr(self, bars: List[Dict]) -> float:
        """Calculate Average True Range from OHLCV bars."""
        if len(bars) < 2:
            return 0

        true_ranges = []
        for i in range(1, len(bars)):
            high = bars[i]["high"]
            low = bars[i]["low"]
            prev_close = bars[i - 1]["close"]

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)

        period = min(self.ATR_PERIOD, len(true_ranges))
        if period == 0:
            return 0

        return sum(true_ranges[-period:]) / period

    def get_adjusted_stop(
        self,
        entry_price: float,
        direction: str,
        bars: List[Dict],
    ) -> Dict[str, float]:
        """
        Calculate ATR-adjusted stop and target.
        Returns {stop, target, atr, stop_pct, atr_pct}
        """
        atr = self.calculate_atr(bars)
        atr_pct = (atr / entry_price * 100) if entry_price > 0 else 0.5

        # Stop distance = ATR * multiplier, clamped to min/max
        stop_pct = max(self.MIN_STOP_PCT, min(self.MAX_STOP_PCT, atr_pct * self.STOP_ATR_MULT))
        stop_distance = entry_price * stop_pct / 100

        if direction == "LONG":
            stop = entry_price - stop_distance
            target = entry_price + stop_distance * self.TARGET_RR
        else:
            stop = entry_price + stop_distance
            target = entry_price - stop_distance * self.TARGET_RR

        return {
            "stop": round(stop, 2),
            "target": round(target, 2),
            "atr": round(atr, 2),
            "atr_pct": round(atr_pct, 3),
            "stop_pct": round(stop_pct, 3),
        }

    def analyze_entry(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        proposed_stop: float,
        bars: List[Dict],
    ) -> AgentOpinion:
        """Check if proposed stop is appropriate for this stock's volatility."""
        adjusted = self.get_adjusted_stop(entry_price, direction, bars)
        atr_pct = adjusted["atr_pct"]
        proposed_stop_pct = abs(entry_price - proposed_stop) / entry_price * 100

        # Is the proposed stop too tight for this stock?
        if proposed_stop_pct < atr_pct * 1.2:
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.WIDEN_STOP,
                urgency=Urgency.HIGH,
                confidence=0.85,
                reason=(
                    f"Stop too tight for {symbol}: proposed {proposed_stop_pct:.2f}% "
                    f"but ATR is {atr_pct:.2f}%. Widening to {adjusted['stop_pct']:.2f}% "
                    f"(2x ATR). Stock will hit stop on normal noise otherwise."
                ),
                suggested_stop=adjusted["stop"],
                data=adjusted,
            )

        # Is the proposed stop too wide? (wasting risk budget)
        if proposed_stop_pct > atr_pct * 4:
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.TIGHTEN_STOP,
                urgency=Urgency.LOW,
                confidence=0.60,
                reason=(
                    f"Stop wider than needed for {symbol}: {proposed_stop_pct:.2f}% "
                    f"but ATR only {atr_pct:.2f}%. Could tighten to {adjusted['stop_pct']:.2f}%."
                ),
                suggested_stop=adjusted["stop"],
                data=adjusted,
            )

        return AgentOpinion(
            agent_name=self.NAME,
            action=ActionType.HOLD,
            urgency=Urgency.LOW,
            confidence=0.70,
            reason=f"Stop OK for {symbol}: {proposed_stop_pct:.2f}% vs ATR {atr_pct:.2f}%",
            data=adjusted,
        )
