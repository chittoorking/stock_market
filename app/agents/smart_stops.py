"""
Smart Stops — chart-structure-based stop placement.

The problem: executor sets stops at ATR distance. But the chart has
REAL levels — swing lows, EMA support, pattern boundaries.

A stop should be where the trade thesis is INVALID, not at an
arbitrary mathematical distance.

Stop hierarchy (use the FIRST one that applies):
1. Below the pattern boundary (hammer low, engulfing low, etc.)
2. Below the nearest swing low/high (the market already held there)
3. Below the EMA that's acting as support/resistance
4. Below ATR distance (last resort)

Each stop level comes with a REASON — the LLM sees why the stop is there.
"""
from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple


class SmartStopCalculator:
    """Calculates stops based on chart structure, not fixed ATR."""

    MIN_STOP_PCT = 0.20   # Never less than 0.20%
    MAX_STOP_PCT = 1.50   # Never more than 1.50%

    @staticmethod
    def calculate(
        bars: List[Dict],
        entry_bar: int,
        entry_price: float,
        direction: str,
        atr: float = 0,
    ) -> Tuple[float, str]:
        """
        Calculate the best stop level based on chart structure.
        Returns (stop_price, reason).
        """
        if entry_bar < 2 or entry_bar >= len(bars):
            # Fallback to ATR
            dist = max(entry_price * 0.005, atr * 1.5)
            stop = entry_price - dist if direction == "LONG" else entry_price + dist
            return round(stop, 2), "ATR-based (no chart data)"

        pre_bars = bars[max(0, entry_bar - 15):entry_bar + 1]
        entry_b = bars[entry_bar]

        if direction == "LONG":
            return SmartStopCalculator._long_stop(pre_bars, entry_price, entry_b, atr)
        else:
            return SmartStopCalculator._short_stop(pre_bars, entry_price, entry_b, atr)

    @staticmethod
    def _long_stop(bars: List[Dict], entry: float, entry_bar: Dict, atr: float) -> Tuple[float, str]:
        """Find the best stop for a LONG position."""
        candidates = []

        # 1. Entry bar's low (the candle we entered on)
        entry_low = entry_bar["low"]
        dist = (entry - entry_low) / entry * 100
        if 0.10 < dist < 1.50:
            # Add small buffer below
            stop = entry_low - (entry - entry_low) * 0.1
            candidates.append((stop, dist, f"Below entry bar low ({entry_low:.2f}) — if price breaks this level, the entry candle failed"))

        # 2. Previous bar's low (immediate support)
        if len(bars) >= 2:
            prev_low = bars[-2]["low"]
            dist = (entry - prev_low) / entry * 100
            if 0.10 < dist < 1.50:
                stop = prev_low - (entry - prev_low) * 0.1
                candidates.append((stop, dist, f"Below previous bar low ({prev_low:.2f}) — immediate support level"))

        # 3. Swing low from last 10 bars
        if len(bars) >= 5:
            lows = [b["low"] for b in bars[-10:]]
            # Find the lowest low that's still reasonable distance
            for low in sorted(set(lows)):
                dist = (entry - low) / entry * 100
                if 0.15 < dist < 1.20:
                    stop = low - (entry - low) * 0.05
                    candidates.append((stop, dist, f"Below swing low ({low:.2f}) — market held this level before"))
                    break

        # 4. EMA support
        if len(bars) >= 9:
            closes = [b["close"] for b in bars]
            ema9 = sum(closes[-9:]) / 9
            dist = (entry - ema9) / entry * 100
            if 0.10 < dist < 1.00 and ema9 < entry:
                stop = ema9 - (entry - ema9) * 0.1
                candidates.append((stop, dist, f"Below EMA9 ({ema9:.2f}) — dynamic support, trend intact above this"))

        # 5. Hammer/engulfing pattern low
        body = abs(entry_bar["close"] - entry_bar["open"])
        lower_wick = min(entry_bar["close"], entry_bar["open"]) - entry_bar["low"]
        if lower_wick > body * 1.5:  # Hammer-like
            stop = entry_bar["low"] - body * 0.2
            dist = (entry - stop) / entry * 100
            if 0.10 < dist < 1.50:
                candidates.append((stop, dist, f"Below hammer wick ({entry_bar['low']:.2f}) — buyers defended this level aggressively"))

        if not candidates:
            dist = max(entry * 0.005, atr * 1.5) if atr else entry * 0.005
            return round(entry - dist, 2), "ATR fallback — no clear chart structure"

        # Pick the TIGHTEST stop that's still at a real level (smallest distance)
        # But prefer chart structure over pure distance
        candidates.sort(key=lambda x: x[1])  # Sort by distance (tightest first)

        # If tightest is < 0.20%, it's too tight — use the next one
        for stop, dist, reason in candidates:
            if dist >= SmartStopCalculator.MIN_STOP_PCT:
                return round(stop, 2), reason

        # All too tight — use the widest
        stop, dist, reason = candidates[-1]
        return round(stop, 2), reason

    @staticmethod
    def _short_stop(bars: List[Dict], entry: float, entry_bar: Dict, atr: float) -> Tuple[float, str]:
        """Find the best stop for a SHORT position."""
        candidates = []

        # 1. Entry bar's high
        entry_high = entry_bar["high"]
        dist = (entry_high - entry) / entry * 100
        if 0.10 < dist < 1.50:
            stop = entry_high + (entry_high - entry) * 0.1
            candidates.append((stop, dist, f"Above entry bar high ({entry_high:.2f}) — if price breaks this, entry candle failed"))

        # 2. Previous bar's high
        if len(bars) >= 2:
            prev_high = bars[-2]["high"]
            dist = (prev_high - entry) / entry * 100
            if 0.10 < dist < 1.50:
                stop = prev_high + (prev_high - entry) * 0.1
                candidates.append((stop, dist, f"Above previous bar high ({prev_high:.2f}) — immediate resistance"))

        # 3. Swing high
        if len(bars) >= 5:
            highs = [b["high"] for b in bars[-10:]]
            for high in sorted(set(highs), reverse=True):
                dist = (high - entry) / entry * 100
                if 0.15 < dist < 1.20:
                    stop = high + (high - entry) * 0.05
                    candidates.append((stop, dist, f"Above swing high ({high:.2f}) — market rejected this level"))
                    break

        # 4. EMA resistance
        if len(bars) >= 9:
            closes = [b["close"] for b in bars]
            ema9 = sum(closes[-9:]) / 9
            dist = (ema9 - entry) / entry * 100
            if 0.10 < dist < 1.00 and ema9 > entry:
                stop = ema9 + (ema9 - entry) * 0.1
                candidates.append((stop, dist, f"Above EMA9 ({ema9:.2f}) — dynamic resistance"))

        # 5. Shooting star high
        upper_wick = entry_bar["high"] - max(entry_bar["close"], entry_bar["open"])
        body = abs(entry_bar["close"] - entry_bar["open"])
        if upper_wick > body * 1.5:
            stop = entry_bar["high"] + body * 0.2
            dist = (stop - entry) / entry * 100
            if 0.10 < dist < 1.50:
                candidates.append((stop, dist, f"Above shooting star wick ({entry_bar['high']:.2f}) — sellers defended this level"))

        if not candidates:
            dist = max(entry * 0.005, atr * 1.5) if atr else entry * 0.005
            return round(entry + dist, 2), "ATR fallback"

        candidates.sort(key=lambda x: x[1])
        for stop, dist, reason in candidates:
            if dist >= SmartStopCalculator.MIN_STOP_PCT:
                return round(stop, 2), reason

        stop, dist, reason = candidates[-1]
        return round(stop, 2), reason

    @staticmethod
    def calculate_target(entry: float, stop: float, direction: str, rr: float = 2.5) -> Tuple[float, str]:
        """Calculate target from stop distance with risk-reward ratio."""
        risk = abs(entry - stop)
        if direction == "LONG":
            target = entry + risk * rr
        else:
            target = entry - risk * rr
        return round(target, 2), f"{rr:.1f}R target from chart-based stop"
