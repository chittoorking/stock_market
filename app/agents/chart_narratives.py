"""
Chart Narratives — turns raw technicals into STORIES the AI can reason about.

Instead of: "RSI 32, hammer, EMA9 > EMA21, support at 198"
The AI sees: "SHAKEOUT SETUP: price dipped to support (198) forming a hammer,
             RSI oversold at 32 but EMA trend still bullish. This is a
             shakeout before continuation — hold and expect bounce."

Each narrative is a SETUP TYPE with:
- What the chart is telling us (the story)
- What usually happens next (the expectation)
- What would invalidate it (when to bail)
- Suggested stop/target based on chart structure (not ATR)

The AI reads the narrative and decides. Not 15 separate flags.
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class ChartNarrative:
    """One story about what the chart is doing."""
    setup_type: str          # "shakeout", "breakout", "exhaustion", "continuation", "reversal", "consolidation"
    direction_bias: str      # "bullish", "bearish", "neutral"
    confidence: float        # 0-1
    story: str               # Human-readable narrative
    expectation: str         # What usually happens next
    invalidation: str        # When this narrative is wrong
    suggested_stop: Optional[float] = None  # Chart-based, not ATR
    suggested_target: Optional[float] = None


class NarrativeBuilder:
    """Builds chart narratives from raw bar data."""

    @staticmethod
    def build(bars: List[Dict], current_bar: int, direction: str = "", entry_price: float = 0) -> List[ChartNarrative]:
        """Build all applicable narratives for this moment."""
        if current_bar < 10 or current_bar >= len(bars):
            return []

        narratives = []
        window = bars[max(0, current_bar - 50):current_bar + 1]
        if len(window) < 10:
            return narratives

        curr = window[-1]
        price = curr["close"]

        # Compute basics
        ema9 = _ema([b["close"] for b in window], 9)
        ema21 = _ema([b["close"] for b in window], 21)
        rsi = _rsi([b["close"] for b in window])
        support, resistance = _support_resistance(window[-15:])
        recent_highs = [b["high"] for b in window[-10:]]
        recent_lows = [b["low"] for b in window[-10:]]
        vol_avg = sum(b["volume"] for b in window[-10:]) / 10
        curr_vol_ratio = curr["volume"] / vol_avg if vol_avg > 0 else 1

        # Candle basics
        body = curr["close"] - curr["open"]
        full_range = curr["high"] - curr["low"]
        lower_wick = min(curr["close"], curr["open"]) - curr["low"]
        upper_wick = curr["high"] - max(curr["close"], curr["open"])
        is_hammer = full_range > 0 and lower_wick > abs(body) * 2
        is_shooting_star = full_range > 0 and upper_wick > abs(body) * 2
        is_doji = full_range > 0 and abs(body) / full_range < 0.15
        is_big_green = body > 0 and abs(body) / full_range > 0.6 if full_range > 0 else False
        is_big_red = body < 0 and abs(body) / full_range > 0.6 if full_range > 0 else False

        # Three soldiers/crows
        last3 = window[-3:]
        three_green = all(b["close"] > b["open"] for b in last3) and all(last3[i]["close"] > last3[i-1]["close"] for i in range(1, 3))
        three_red = all(b["close"] < b["open"] for b in last3) and all(last3[i]["close"] < last3[i-1]["close"] for i in range(1, 3))

        # EMA relationship
        ema_bullish = ema9 and ema21 and ema9 > ema21
        ema_bearish = ema9 and ema21 and ema9 < ema21
        price_above_ema = ema21 and price > ema21
        price_below_ema = ema21 and price < ema21

        dist_to_support = (price - support) / price * 100 if support else 999
        dist_to_resistance = (resistance - price) / price * 100 if resistance else 999

        # ─── Narrative Detection ───

        # SHAKEOUT: Price dips to support + hammer/doji + RSI oversold + EMA still bullish
        if (dist_to_support < 0.3 and (is_hammer or is_doji) and
                rsi and rsi < 40 and ema_bullish):
            narratives.append(ChartNarrative(
                setup_type="shakeout",
                direction_bias="bullish",
                confidence=0.75,
                story=f"Price dipped to support ({support:.2f}) forming {'hammer' if is_hammer else 'doji'}. RSI at {rsi:.0f} (oversold) but EMA9 still above EMA21 — trend intact.",
                expectation="Shakeout before continuation UP. Weak hands shaken out, buyers step in at support.",
                invalidation=f"Breaks below {support:.2f} with volume — then support failed and trend is broken.",
                suggested_stop=round(support - (price - support) * 0.5, 2),
                suggested_target=round(resistance, 2) if resistance else None,
            ))

        # CONTINUATION: Three green soldiers + EMA bullish + volume rising
        if three_green and ema_bullish and curr_vol_ratio > 1.2:
            narratives.append(ChartNarrative(
                setup_type="continuation",
                direction_bias="bullish",
                confidence=0.70,
                story=f"Three consecutive green bars with rising closes. EMA9 > EMA21. Volume {curr_vol_ratio:.1f}x average — conviction present.",
                expectation="Trend continuation. Let it run toward resistance at {:.2f}.".format(resistance) if resistance else "Trend continuation.",
                invalidation="Red bar that closes below EMA21 — momentum broken.",
                suggested_target=round(resistance, 2) if resistance else None,
            ))

        # BEARISH CONTINUATION: Three red + EMA bearish + volume
        if three_red and ema_bearish and curr_vol_ratio > 1.2:
            narratives.append(ChartNarrative(
                setup_type="continuation",
                direction_bias="bearish",
                confidence=0.70,
                story=f"Three consecutive red bars with falling closes. EMA9 < EMA21. Volume {curr_vol_ratio:.1f}x — selling pressure sustained.",
                expectation="Continue down toward support at {:.2f}.".format(support) if support else "Continue down.",
                invalidation="Green bar closing above EMA21.",
                suggested_target=round(support, 2) if support else None,
            ))

        # EXHAUSTION TOP: Shooting star at resistance + RSI overbought + volume spike
        if (is_shooting_star and dist_to_resistance < 0.3 and
                rsi and rsi > 65 and curr_vol_ratio > 1.5):
            narratives.append(ChartNarrative(
                setup_type="exhaustion",
                direction_bias="bearish",
                confidence=0.80,
                story=f"Shooting star at resistance ({resistance:.2f}). RSI {rsi:.0f} overbought. Volume spike {curr_vol_ratio:.1f}x — climax selling after rally.",
                expectation="Reversal DOWN. Rally exhausted. Take profits on longs, consider shorts.",
                invalidation=f"Closes above {resistance:.2f} — breakout, not exhaustion.",
            ))

        # EXHAUSTION BOTTOM: Hammer at support + RSI oversold + volume spike
        if (is_hammer and dist_to_support < 0.3 and
                rsi and rsi < 35 and curr_vol_ratio > 1.5):
            narratives.append(ChartNarrative(
                setup_type="exhaustion",
                direction_bias="bullish",
                confidence=0.80,
                story=f"Hammer at support ({support:.2f}). RSI {rsi:.0f} oversold. Volume spike {curr_vol_ratio:.1f}x — capitulation selling, buyers absorbing.",
                expectation="Reversal UP. Selling exhausted. Bounce imminent.",
                invalidation=f"Breaks below {support:.2f} on next bar — not absorption, real breakdown.",
                suggested_stop=round(support - (curr["high"] - support) * 0.3, 2),
            ))

        # CONSOLIDATION: Narrow range + inside bars + doji
        recent_ranges = [b["high"] - b["low"] for b in window[-5:]]
        narrowing = len(recent_ranges) >= 3 and recent_ranges[-1] < recent_ranges[0] * 0.6
        if narrowing and (is_doji or (full_range > 0 and abs(body) / full_range < 0.3)):
            narratives.append(ChartNarrative(
                setup_type="consolidation",
                direction_bias="neutral",
                confidence=0.60,
                story="Bars narrowing with small bodies — coiling. Energy building for a breakout.",
                expectation=f"Breakout imminent. Direction unclear but move will be significant. Watch {resistance:.2f} (up) or {support:.2f} (down)." if resistance and support else "Breakout imminent.",
                invalidation="More consolidation bars — delay, not failure.",
            ))

        # REVERSAL (bearish engulfing after uptrend)
        if len(window) >= 2:
            prev = window[-2]
            prev_body = prev["close"] - prev["open"]
            if prev_body > 0 and body < 0 and curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
                if ema_bullish and rsi and rsi > 55:
                    narratives.append(ChartNarrative(
                        setup_type="reversal",
                        direction_bias="bearish",
                        confidence=0.75,
                        story=f"Bearish engulfing after uptrend. Current bar swallowed previous green bar. RSI {rsi:.0f} coming off highs.",
                        expectation="Short-term reversal DOWN. At minimum expect a pullback to EMA21.",
                        invalidation="Next bar closes above today's high — engulfing failed.",
                        suggested_target=round(ema21, 2) if ema21 else None,
                    ))

        # REVERSAL (bullish engulfing after downtrend)
        if len(window) >= 2:
            prev = window[-2]
            prev_body = prev["close"] - prev["open"]
            if prev_body < 0 and body > 0 and curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
                if ema_bearish and rsi and rsi < 45:
                    narratives.append(ChartNarrative(
                        setup_type="reversal",
                        direction_bias="bullish",
                        confidence=0.75,
                        story=f"Bullish engulfing after downtrend. Buyers overwhelmed sellers. RSI {rsi:.0f} turning up from lows.",
                        expectation="Short-term reversal UP. Expect bounce to EMA21 at minimum.",
                        invalidation="Next bar closes below today's low — engulfing failed.",
                        suggested_target=round(ema21, 2) if ema21 else None,
                    ))

        # TREND STRENGTH: Price pulling back to EMA but bouncing off it
        if ema21 and abs(price - ema21) / price * 100 < 0.15:
            # Price touching EMA
            if ema_bullish and body > 0:
                narratives.append(ChartNarrative(
                    setup_type="trend_pullback",
                    direction_bias="bullish",
                    confidence=0.65,
                    story=f"Price pulled back to EMA21 ({ema21:.2f}) and bounced with a green bar. Textbook buy-the-dip in uptrend.",
                    expectation="Continuation UP. EMA21 is acting as dynamic support.",
                    invalidation=f"Closes below EMA21 ({ema21:.2f}) — trend weakening.",
                    suggested_stop=round(ema21 - (ema21 * 0.002), 2),
                ))
            elif ema_bearish and body < 0:
                narratives.append(ChartNarrative(
                    setup_type="trend_pullback",
                    direction_bias="bearish",
                    confidence=0.65,
                    story=f"Price rallied to EMA21 ({ema21:.2f}) and got rejected with a red bar. Sell-the-rally in downtrend.",
                    expectation="Continuation DOWN. EMA21 is dynamic resistance.",
                    invalidation=f"Closes above EMA21 — trend reversing.",
                    suggested_stop=round(ema21 + (ema21 * 0.002), 2),
                ))

        return narratives

    @staticmethod
    def format_for_llm(narratives: List[ChartNarrative], position_direction: str = "") -> str:
        """Format narratives for LLM consumption. Shows how chart relates to position."""
        if not narratives:
            return "CHART: No clear pattern forming. Price action is ambiguous."

        lines = ["CHART NARRATIVES:"]
        for n in narratives:
            # How does this narrative relate to our position?
            alignment = ""
            if position_direction:
                if (position_direction == "LONG" and n.direction_bias == "bullish") or \
                   (position_direction == "SHORT" and n.direction_bias == "bearish"):
                    alignment = " [SUPPORTS YOUR POSITION]"
                elif (position_direction == "LONG" and n.direction_bias == "bearish") or \
                     (position_direction == "SHORT" and n.direction_bias == "bullish"):
                    alignment = " [AGAINST YOUR POSITION]"

            lines.append(f"\n  {n.setup_type.upper()} ({n.direction_bias}, {n.confidence:.0%}){alignment}")
            lines.append(f"  Story: {n.story}")
            lines.append(f"  Expect: {n.expectation}")
            lines.append(f"  Invalid if: {n.invalidation}")
            if n.suggested_stop:
                lines.append(f"  Chart stop: {n.suggested_stop:.2f}")
            if n.suggested_target:
                lines.append(f"  Chart target: {n.suggested_target:.2f}")

        return "\n".join(lines)


# ─── Helper functions ───

def _ema(data: List[float], period: int) -> Optional[float]:
    if len(data) < period:
        return None
    mult = 2 / (period + 1)
    val = sum(data[:period]) / period
    for p in data[period:]:
        val = (p - val) * mult + val
    return round(val, 2)


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = []; losses = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(0, d)); losses.append(max(0, -d))
    if len(gains) < period:
        return None
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100
    return round(100 - 100 / (1 + ag / al), 1)


def _support_resistance(bars: List[Dict]):
    if not bars:
        return None, None
    lows = sorted(b["low"] for b in bars)
    highs = sorted((b["high"] for b in bars), reverse=True)
    support = lows[0] if lows else None
    resistance = highs[0] if highs else None
    return support, resistance
