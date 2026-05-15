"""
Comprehensive Candlestick Pattern Detection — 35+ patterns from textbook sources.

Each pattern returns:
- name, type (reversal/continuation/indecision)
- direction bias (bullish/bearish/neutral)
- reliability (high/moderate/low)
- what it means in plain English

These get written to the trade dev graph. The LLM reads them as context.
"""
from __future__ import annotations
from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class CandlePattern:
    name: str
    pattern_type: str      # reversal, continuation, indecision
    direction: str         # bullish, bearish, neutral
    reliability: str       # high, moderate, low
    candles: int
    meaning: str


def detect_patterns(bars: List[Dict], idx: int) -> List[CandlePattern]:
    """Detect all candlestick patterns at the given bar index."""
    if idx < 4 or idx >= len(bars):
        return []

    patterns = []
    c = bars[idx]       # current
    p = bars[idx - 1]   # previous
    p2 = bars[idx - 2]  # 2 ago
    p3 = bars[idx - 3] if idx >= 3 else p2
    p4 = bars[idx - 4] if idx >= 4 else p3

    # Helpers
    def body(b): return b["close"] - b["open"]
    def abs_body(b): return abs(body(b))
    def full_range(b): return b["high"] - b["low"]
    def upper_wick(b): return b["high"] - max(b["close"], b["open"])
    def lower_wick(b): return min(b["close"], b["open"]) - b["low"]
    def is_green(b): return b["close"] > b["open"]
    def is_red(b): return b["close"] < b["open"]
    def body_ratio(b): return abs_body(b) / full_range(b) if full_range(b) > 0 else 0
    def midpoint(b): return (b["open"] + b["close"]) / 2

    fr = full_range(c)
    ab = abs_body(c)

    # ═══ SINGLE CANDLE PATTERNS ═══

    # Doji
    if fr > 0 and ab / fr < 0.10:
        patterns.append(CandlePattern("doji", "indecision", "neutral", "moderate", 1,
            "Market indecision — buyers and sellers balanced. Direction depends on context."))

    # Dragonfly Doji (long lower wick, no upper)
    if fr > 0 and ab / fr < 0.10 and lower_wick(c) > fr * 0.6 and upper_wick(c) < fr * 0.1:
        patterns.append(CandlePattern("dragonfly_doji", "reversal", "bullish", "moderate", 1,
            "Strong rejection of lower prices — sellers pushed down but buyers absorbed everything."))

    # Gravestone Doji
    if fr > 0 and ab / fr < 0.10 and upper_wick(c) > fr * 0.6 and lower_wick(c) < fr * 0.1:
        patterns.append(CandlePattern("gravestone_doji", "reversal", "bearish", "moderate", 1,
            "Strong rejection of higher prices — buyers pushed up but sellers drove it back down."))

    # Hammer
    if fr > 0 and lower_wick(c) > ab * 2 and upper_wick(c) < ab * 0.5 and ab / fr > 0.1:
        patterns.append(CandlePattern("hammer", "reversal", "bullish", "moderate", 1,
            "Buyers stepped in aggressively at lows — potential bottom reversal."))

    # Inverted Hammer
    if fr > 0 and upper_wick(c) > ab * 2 and lower_wick(c) < ab * 0.5 and ab / fr > 0.1 and is_green(c):
        patterns.append(CandlePattern("inverted_hammer", "reversal", "bullish", "moderate", 1,
            "Buyers tested higher prices — early sign of buying interest at bottom."))

    # Shooting Star
    if fr > 0 and upper_wick(c) > ab * 2 and lower_wick(c) < ab * 0.5 and is_red(c):
        patterns.append(CandlePattern("shooting_star", "reversal", "bearish", "high", 1,
            "Sellers rejected the rally — buyers tried but failed. Top reversal signal."))

    # Hanging Man
    if fr > 0 and lower_wick(c) > ab * 2 and upper_wick(c) < ab * 0.5 and is_red(c):
        patterns.append(CandlePattern("hanging_man", "reversal", "bearish", "moderate", 1,
            "Selling pressure appeared at the top — bearish if confirmed by next bar."))

    # Marubozu (full body, no wicks)
    if fr > 0 and ab / fr > 0.90:
        d = "bullish" if is_green(c) else "bearish"
        patterns.append(CandlePattern("marubozu", "continuation", d, "high", 1,
            f"Full conviction {'buying' if d == 'bullish' else 'selling'} — no wicks, pure momentum."))

    # Spinning Top
    if fr > 0 and ab / fr < 0.30 and upper_wick(c) > ab and lower_wick(c) > ab:
        patterns.append(CandlePattern("spinning_top", "indecision", "neutral", "low", 1,
            "Small body with equal wicks — neither side winning. Trend may pause."))

    # Belt Hold (bullish)
    if is_green(c) and lower_wick(c) < fr * 0.05 and ab / fr > 0.6:
        patterns.append(CandlePattern("bullish_belt_hold", "reversal", "bullish", "moderate", 1,
            "Opened at low and closed near high — strong buying from the open."))

    # Belt Hold (bearish)
    if is_red(c) and upper_wick(c) < fr * 0.05 and ab / fr > 0.6:
        patterns.append(CandlePattern("bearish_belt_hold", "reversal", "bearish", "moderate", 1,
            "Opened at high and closed near low — strong selling from the open."))

    # ═══ TWO CANDLE PATTERNS ═══

    # Bullish Engulfing
    if is_red(p) and is_green(c) and c["open"] <= p["close"] and c["close"] >= p["open"]:
        patterns.append(CandlePattern("bullish_engulfing", "reversal", "bullish", "high", 2,
            "Buyers completely overwhelmed previous sellers — strong reversal signal."))

    # Bearish Engulfing
    if is_green(p) and is_red(c) and c["open"] >= p["close"] and c["close"] <= p["open"]:
        patterns.append(CandlePattern("bearish_engulfing", "reversal", "bearish", "high", 2,
            "Sellers completely overwhelmed previous buyers — strong reversal signal."))

    # Bullish Harami
    if is_red(p) and is_green(c) and c["open"] >= p["close"] and c["close"] <= p["open"] and abs_body(c) < abs_body(p) * 0.5:
        patterns.append(CandlePattern("bullish_harami", "reversal", "bullish", "moderate", 2,
            "Small green inside big red — selling pressure weakening, reversal possible."))

    # Bearish Harami
    if is_green(p) and is_red(c) and c["open"] <= p["close"] and c["close"] >= p["open"] and abs_body(c) < abs_body(p) * 0.5:
        patterns.append(CandlePattern("bearish_harami", "reversal", "bearish", "moderate", 2,
            "Small red inside big green — buying momentum fading, reversal possible."))

    # Piercing Line
    if is_red(p) and is_green(c) and c["open"] < p["low"] and c["close"] > midpoint(p) and c["close"] < p["open"]:
        patterns.append(CandlePattern("piercing_line", "reversal", "bullish", "high", 2,
            "Opened below previous low but closed above midpoint — aggressive buyer intervention."))

    # Dark Cloud Cover
    if is_green(p) and is_red(c) and c["open"] > p["high"] and c["close"] < midpoint(p) and c["close"] > p["open"]:
        patterns.append(CandlePattern("dark_cloud_cover", "reversal", "bearish", "high", 2,
            "Opened above previous high but closed below midpoint — sellers taking over despite initial strength."))

    # Tweezer Bottom
    if abs(c["low"] - p["low"]) < full_range(p) * 0.05 and is_red(p) and is_green(c):
        patterns.append(CandlePattern("tweezer_bottom", "reversal", "bullish", "moderate", 2,
            "Two bars tested same low and held — strong support found."))

    # Tweezer Top
    if abs(c["high"] - p["high"]) < full_range(p) * 0.05 and is_green(p) and is_red(c):
        patterns.append(CandlePattern("tweezer_top", "reversal", "bearish", "moderate", 2,
            "Two bars tested same high and failed — strong resistance found."))

    # Kicking (bullish)
    if is_red(p) and is_green(c) and c["open"] > p["open"] and body_ratio(p) > 0.8 and body_ratio(c) > 0.8:
        patterns.append(CandlePattern("bullish_kicking", "reversal", "bullish", "high", 2,
            "Dramatic gap up with full-body candles — sudden shift to bullish sentiment."))

    # Kicking (bearish)
    if is_green(p) and is_red(c) and c["open"] < p["open"] and body_ratio(p) > 0.8 and body_ratio(c) > 0.8:
        patterns.append(CandlePattern("bearish_kicking", "reversal", "bearish", "high", 2,
            "Dramatic gap down with full-body candles — sudden shift to bearish sentiment."))

    # On-Neck (bearish continuation)
    if is_red(p) and is_green(c) and abs(c["close"] - p["low"]) < full_range(p) * 0.05:
        patterns.append(CandlePattern("on_neck", "continuation", "bearish", "moderate", 2,
            "Small bounce to previous low only — buyers too weak, selling continues."))

    # ═══ THREE CANDLE PATTERNS ═══

    # Morning Star
    if (is_red(p2) and abs_body(p2) > full_range(p2) * 0.5 and
        abs_body(p) < full_range(p) * 0.3 and
        is_green(c) and abs_body(c) > full_range(c) * 0.5 and
        c["close"] > midpoint(p2)):
        patterns.append(CandlePattern("morning_star", "reversal", "bullish", "high", 3,
            "Classic 3-bar reversal: strong selling, indecision, then strong buying. High confidence bottom."))

    # Evening Star
    if (is_green(p2) and abs_body(p2) > full_range(p2) * 0.5 and
        abs_body(p) < full_range(p) * 0.3 and
        is_red(c) and abs_body(c) > full_range(c) * 0.5 and
        c["close"] < midpoint(p2)):
        patterns.append(CandlePattern("evening_star", "reversal", "bearish", "high", 3,
            "Classic 3-bar top reversal: strong buying, indecision, then strong selling. High confidence top."))

    # Three White Soldiers
    if (is_green(p2) and is_green(p) and is_green(c) and
        p["close"] > p2["close"] and c["close"] > p["close"] and
        body_ratio(p2) > 0.5 and body_ratio(p) > 0.5 and body_ratio(c) > 0.5):
        patterns.append(CandlePattern("three_white_soldiers", "reversal", "bullish", "high", 3,
            "Three strong green bars with higher closes — sustained buying, strong uptrend signal."))

    # Three Black Crows
    if (is_red(p2) and is_red(p) and is_red(c) and
        p["close"] < p2["close"] and c["close"] < p["close"] and
        body_ratio(p2) > 0.5 and body_ratio(p) > 0.5 and body_ratio(c) > 0.5):
        patterns.append(CandlePattern("three_black_crows", "reversal", "bearish", "high", 3,
            "Three strong red bars with lower closes — sustained selling, strong downtrend signal."))

    # Three Inside Up
    if (is_red(p2) and is_green(p) and is_green(c) and
        p["open"] >= p2["close"] and p["close"] <= p2["open"] and  # p inside p2
        c["close"] > p2["open"]):  # c breaks above
        patterns.append(CandlePattern("three_inside_up", "reversal", "bullish", "high", 3,
            "Harami confirmed by third bar breaking higher — strong bullish reversal."))

    # Three Inside Down
    if (is_green(p2) and is_red(p) and is_red(c) and
        p["open"] <= p2["close"] and p["close"] >= p2["open"] and
        c["close"] < p2["open"]):
        patterns.append(CandlePattern("three_inside_down", "reversal", "bearish", "high", 3,
            "Bearish harami confirmed by third bar breaking lower — strong bearish reversal."))

    # Stick Sandwich
    if (is_red(p2) and is_green(p) and is_red(c) and
        abs(p2["close"] - c["close"]) < full_range(p2) * 0.1):
        patterns.append(CandlePattern("stick_sandwich", "reversal", "bullish", "moderate", 3,
            "Two red bars closing at same level with green between — strong support found."))

    # ═══ FIVE CANDLE PATTERNS ═══

    if idx >= 4:
        # Rising Three Methods (bullish continuation)
        if (is_green(p4) and body_ratio(p4) > 0.5 and
            all(is_red(bars[idx-i]) for i in range(1, 4)) and  # 3 small reds
            all(bars[idx-i]["low"] > p4["low"] for i in range(1, 4)) and
            is_green(c) and c["close"] > p4["close"]):
            patterns.append(CandlePattern("rising_three_methods", "continuation", "bullish", "high", 5,
                "Strong green, three small pullback bars, then green breaks higher — textbook uptrend continuation."))

        # Falling Three Methods (bearish continuation)
        if (is_red(p4) and body_ratio(p4) > 0.5 and
            all(is_green(bars[idx-i]) for i in range(1, 4)) and
            all(bars[idx-i]["high"] < p4["high"] for i in range(1, 4)) and
            is_red(c) and c["close"] < p4["close"]):
            patterns.append(CandlePattern("falling_three_methods", "continuation", "bearish", "high", 5,
                "Strong red, three small bounce bars, then red breaks lower — textbook downtrend continuation."))

    return patterns


def format_patterns_for_graph(patterns: List[CandlePattern]) -> str:
    """Format patterns as concise text for the trade dev graph and LLM."""
    if not patterns:
        return "No candlestick patterns detected."

    lines = []
    for p in patterns:
        reliability_marker = {"high": "***", "moderate": "**", "low": "*"}[p.reliability]
        lines.append(f"{reliability_marker} {p.name.upper()} ({p.direction} {p.pattern_type}): {p.meaning}")

    return "\n".join(lines)
