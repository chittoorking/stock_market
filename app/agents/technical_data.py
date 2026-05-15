"""
Technical Analysis Data Provider — computes chart patterns, indicators,
and candle formations that get written to the trade dev graph.

The monitor doesn't just see "P&L = -0.1%". It sees:
- "P&L = -0.1% BUT hammer candle forming at support, RSI 32 (oversold),
   EMA 9 still above EMA 21, volume spike on this bar = buyers stepping in"

That changes "close" to "hold — this is a shakeout before continuation."

Computes:
1. Candle patterns (doji, hammer, engulfing, three soldiers/crows)
2. Moving averages (EMA 9/21/50 position and crossovers)
3. RSI (overbought/oversold + divergence)
4. Support/resistance from recent highs/lows
5. Bar-level patterns (inside bar, outside bar, narrow range)
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional


class TechnicalDataProvider:
    """Computes technical data for the trade dev graph and monitor."""

    @staticmethod
    def compute(bars: List[Dict], current_bar: int) -> Dict[str, Any]:
        """Compute all technical data at current bar. Returns dict for graph."""
        if current_bar < 2 or current_bar >= len(bars):
            return {"patterns": [], "indicators": {}, "levels": {}}

        result = {
            "patterns": [],
            "indicators": {},
            "levels": {},
            "summary": "",
        }

        # Slice up to current bar
        window = bars[max(0, current_bar - 50):current_bar + 1]
        if len(window) < 3:
            return result

        curr = window[-1]
        prev = window[-2]
        prev2 = window[-3] if len(window) >= 3 else prev

        # ─── 1. Candle Patterns ───
        patterns = []

        body = curr["close"] - curr["open"]
        full_range = curr["high"] - curr["low"]
        abs_body = abs(body)
        upper_wick = curr["high"] - max(curr["close"], curr["open"])
        lower_wick = min(curr["close"], curr["open"]) - curr["low"]

        # Doji: tiny body, big wicks = indecision
        if full_range > 0 and abs_body / full_range < 0.15:
            patterns.append("doji (indecision — market can't decide direction)")

        # Hammer: small body at top, long lower wick = buyers at bottom
        if full_range > 0 and lower_wick > abs_body * 2 and upper_wick < abs_body * 0.5:
            patterns.append("hammer (buyers stepping in at lows — potential reversal UP)")

        # Inverted hammer / shooting star
        if full_range > 0 and upper_wick > abs_body * 2 and lower_wick < abs_body * 0.5:
            if body < 0:
                patterns.append("shooting_star (sellers rejecting highs — potential reversal DOWN)")
            else:
                patterns.append("inverted_hammer (testing higher — buyers probing)")

        # Bullish engulfing: current green candle engulfs previous red
        prev_body = prev["close"] - prev["open"]
        if prev_body < 0 and body > 0 and curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
            patterns.append("bullish_engulfing (buyers overwhelmed sellers — strong reversal signal)")

        # Bearish engulfing
        if prev_body > 0 and body < 0 and curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
            patterns.append("bearish_engulfing (sellers overwhelmed buyers — strong reversal DOWN)")

        # Three green/red soldiers
        if len(window) >= 3:
            last3 = window[-3:]
            all_green = all(b["close"] > b["open"] for b in last3)
            all_red = all(b["close"] < b["open"] for b in last3)
            # Each bar should close higher/lower than previous
            ascending = all(last3[i]["close"] > last3[i-1]["close"] for i in range(1, 3))
            descending = all(last3[i]["close"] < last3[i-1]["close"] for i in range(1, 3))

            if all_green and ascending:
                patterns.append("three_green_soldiers (strong sustained buying — momentum UP)")
            if all_red and descending:
                patterns.append("three_red_crows (strong sustained selling — momentum DOWN)")

        # Inside bar: current bar's range is within previous bar
        if curr["high"] <= prev["high"] and curr["low"] >= prev["low"]:
            patterns.append("inside_bar (consolidation — breakout coming)")

        # Outside bar: current bar engulfs previous bar's range
        if curr["high"] > prev["high"] and curr["low"] < prev["low"]:
            if body > 0:
                patterns.append("bullish_outside_bar (expansion with buyers winning)")
            else:
                patterns.append("bearish_outside_bar (expansion with sellers winning)")

        # Narrow range: current bar is smallest of last 7
        if len(window) >= 7:
            ranges = [b["high"] - b["low"] for b in window[-7:]]
            if full_range == min(ranges):
                patterns.append("narrow_range_7 (coiling — big move imminent)")

        result["patterns"] = patterns

        # ─── 2. Moving Averages ───
        closes = [b["close"] for b in window]

        def ema(data, period):
            if len(data) < period:
                return None
            mult = 2 / (period + 1)
            val = sum(data[:period]) / period
            for p in data[period:]:
                val = (p - val) * mult + val
            return val

        ema9 = ema(closes, 9)
        ema21 = ema(closes, 21)
        ema50 = ema(closes, 50) if len(closes) >= 50 else None

        ma_signals = []
        price = curr["close"]

        if ema9 and ema21:
            if ema9 > ema21:
                ma_signals.append(f"EMA9({ema9:.2f}) > EMA21({ema21:.2f}) — short-term uptrend")
            else:
                ma_signals.append(f"EMA9({ema9:.2f}) < EMA21({ema21:.2f}) — short-term downtrend")

            # Crossover detection (last 3 bars)
            if len(closes) >= 22:
                prev_ema9 = ema(closes[:-1], 9)
                prev_ema21 = ema(closes[:-1], 21)
                if prev_ema9 and prev_ema21:
                    if prev_ema9 <= prev_ema21 and ema9 > ema21:
                        ma_signals.append("GOLDEN CROSS: EMA9 just crossed ABOVE EMA21 — bullish")
                    elif prev_ema9 >= prev_ema21 and ema9 < ema21:
                        ma_signals.append("DEATH CROSS: EMA9 just crossed BELOW EMA21 — bearish")

        if ema50:
            if price > ema50:
                ma_signals.append(f"Price above EMA50({ema50:.2f}) — long-term uptrend intact")
            else:
                ma_signals.append(f"Price below EMA50({ema50:.2f}) — long-term downtrend")

        result["indicators"]["moving_averages"] = ma_signals
        result["indicators"]["ema9"] = round(ema9, 2) if ema9 else None
        result["indicators"]["ema21"] = round(ema21, 2) if ema21 else None
        result["indicators"]["ema50"] = round(ema50, 2) if ema50 else None

        # ─── 3. RSI ───
        if len(closes) >= 15:
            gains = []
            losses_list = []
            for i in range(1, len(closes)):
                diff = closes[i] - closes[i-1]
                gains.append(max(0, diff))
                losses_list.append(max(0, -diff))

            period = 14
            if len(gains) >= period:
                avg_gain = sum(gains[-period:]) / period
                avg_loss = sum(losses_list[-period:]) / period
                if avg_loss > 0:
                    rs = avg_gain / avg_loss
                    rsi = 100 - (100 / (1 + rs))
                else:
                    rsi = 100

                rsi_signal = f"RSI({rsi:.1f})"
                if rsi < 30:
                    rsi_signal += " — OVERSOLD (buyers likely to step in)"
                elif rsi < 40:
                    rsi_signal += " — approaching oversold"
                elif rsi > 70:
                    rsi_signal += " — OVERBOUGHT (sellers likely to step in)"
                elif rsi > 60:
                    rsi_signal += " — approaching overbought"
                else:
                    rsi_signal += " — neutral"

                result["indicators"]["rsi"] = round(rsi, 1)
                result["indicators"]["rsi_signal"] = rsi_signal

        # ─── 4. Support/Resistance from recent highs/lows ───
        if len(window) >= 10:
            recent = window[-10:]
            recent_highs = sorted([b["high"] for b in recent], reverse=True)
            recent_lows = sorted([b["low"] for b in recent])

            resistance = recent_highs[0]
            support = recent_lows[0]

            dist_to_resistance = (resistance - price) / price * 100
            dist_to_support = (price - support) / price * 100

            result["levels"]["resistance"] = round(resistance, 2)
            result["levels"]["support"] = round(support, 2)
            result["levels"]["dist_to_resistance_pct"] = round(dist_to_resistance, 3)
            result["levels"]["dist_to_support_pct"] = round(dist_to_support, 3)

            level_signals = []
            if dist_to_support < 0.15:
                level_signals.append(f"AT SUPPORT ({support:.2f}, only {dist_to_support:.2f}% away) — bounce likely")
            if dist_to_resistance < 0.15:
                level_signals.append(f"AT RESISTANCE ({resistance:.2f}, only {dist_to_resistance:.2f}% away) — rejection likely")
            result["levels"]["signals"] = level_signals

        # ─── 5. Build summary for the monitor ───
        summary_parts = []
        if patterns:
            summary_parts.append("Candles: " + ", ".join(patterns[:2]))
        if ma_signals:
            summary_parts.append("MA: " + "; ".join(ma_signals[:2]))
        if result["indicators"].get("rsi_signal"):
            summary_parts.append(result["indicators"]["rsi_signal"])
        if result["levels"].get("signals"):
            summary_parts.append("Levels: " + "; ".join(result["levels"]["signals"]))

        result["summary"] = " | ".join(summary_parts) if summary_parts else "No notable patterns"

        return result

    @staticmethod
    def format_for_monitor(tech_data: Dict) -> str:
        """Format technical data as text for the monitor LLM."""
        lines = []

        if tech_data.get("patterns"):
            lines.append("CANDLE PATTERNS:")
            for p in tech_data["patterns"]:
                lines.append(f"  - {p}")

        if tech_data.get("indicators", {}).get("moving_averages"):
            lines.append("MOVING AVERAGES:")
            for m in tech_data["indicators"]["moving_averages"]:
                lines.append(f"  - {m}")

        if tech_data.get("indicators", {}).get("rsi_signal"):
            lines.append(f"RSI: {tech_data['indicators']['rsi_signal']}")

        if tech_data.get("levels", {}).get("signals"):
            lines.append("LEVELS:")
            for l in tech_data["levels"]["signals"]:
                lines.append(f"  - {l}")

        return "\n".join(lines) if lines else "No notable technical patterns"
