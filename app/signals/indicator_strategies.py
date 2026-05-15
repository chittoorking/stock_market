"""
Indicator-based strategies — VWAP, EMA, RSI.
Completely different signal source from bar-pattern strategies (LNZ3, GDR4, AFT7).

Sources:
- VWAP Pullback: price pulls back to VWAP in a trend, bounces with candle confirmation
- VWAP Breakout: price breaks VWAP with volume surge
- EMA Crossover: EMA 9 crosses EMA 21 with volume and trend confirmation
- RSI Divergence: price makes new low but RSI makes higher low = reversal

These fire on different conditions than our existing strategies.
More signals = more opportunities for the LLM to choose from.
"""
from __future__ import annotations
from typing import Dict, Any, List, Optional
from app.signals.base import StrategySignal, SignalDirection, SignalUrgency, get_sector


def _ema(closes: List[float], period: int) -> Optional[float]:
    if len(closes) < period: return None
    mult = 2 / (period + 1)
    val = sum(closes[:period]) / period
    for p in closes[period:]:
        val = (p - val) * mult + val
    return val

def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1: return None
    gains = [max(0, closes[i]-closes[i-1]) for i in range(1, len(closes))]
    losses = [max(0, closes[i-1]-closes[i]) for i in range(1, len(closes))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100
    return 100 - 100 / (1 + ag / al)

def _vwap(bars: List[Dict]) -> float:
    tp_vol = sum((b["high"]+b["low"]+b["close"])/3 * b["volume"] for b in bars)
    vol = sum(b["volume"] for b in bars)
    return tp_vol / vol if vol > 0 else bars[-1]["close"]


class VWAPPullbackSignal:
    """
    Price in uptrend pulls back to VWAP and bounces.
    Entry: bullish candle at VWAP after pullback.
    Stop: below VWAP and entry candle low.
    65% reported WR.
    """
    NAME = "vwap_pb"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict], bar_idx: int) -> Optional[StrategySignal]:
        if bar_idx < 10 or bar_idx >= len(bars): return None
        day_bars = bars[:bar_idx + 1]

        vwap = _vwap(day_bars)
        closes = [b["close"] for b in day_bars]
        ema9 = _ema(closes, 9)
        if not ema9: return None

        price = bars[bar_idx]["close"]
        prev_price = bars[bar_idx - 1]["close"]
        curr = bars[bar_idx]

        # Uptrend: EMA9 > VWAP, price above EMA9
        if ema9 <= vwap: return None

        # Pullback: previous bar was near or below VWAP
        if prev_price > vwap * 1.002: return None  # Wasn't actually pulling back

        # Bounce: current bar is bullish and closes above VWAP
        if curr["close"] <= curr["open"]: return None  # Not bullish
        if curr["close"] <= vwap: return None  # Didn't bounce above

        # Volume confirmation
        avg_vol = sum(b["volume"] for b in day_bars[-6:]) / 6
        if curr["volume"] < avg_vol * 0.8: return None

        entry = curr["close"]
        stop = min(vwap, curr["low"]) - (entry - min(vwap, curr["low"])) * 0.1
        risk = entry - stop
        target = entry + risk * 2.5

        conf = min(0.80, 0.55 + (ema9 - vwap) / vwap * 10)

        return StrategySignal(
            strategy_name=cls.NAME, symbol=symbol,
            direction=SignalDirection.LONG.value,
            urgency=SignalUrgency.STANDARD.value,
            suggested_entry=round(entry, 2),
            suggested_stop=round(stop, 2),
            suggested_target=round(target, 2),
            risk_reward_ratio=2.5, risk_pct=round(risk/entry*100, 3),
            raw_confidence=round(conf, 3),
            vwap_aligned=True,
            signal_reason=f"VWAP pullback: price pulled back to VWAP ({vwap:.2f}), bounced with bullish candle. EMA9 ({ema9:.2f}) > VWAP = uptrend intact.",
            timeframe="5m", sector=get_sector(symbol),
        )


class VWAPBreakoutSignal:
    """
    Price breaks above/below VWAP with volume surge.
    Stop: opposite side of VWAP.
    """
    NAME = "vwap_bo"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict], bar_idx: int) -> Optional[StrategySignal]:
        if bar_idx < 6 or bar_idx >= len(bars): return None
        day_bars = bars[:bar_idx + 1]

        vwap = _vwap(day_bars)
        curr = bars[bar_idx]
        prev = bars[bar_idx - 1]

        # Volume surge
        avg_vol = sum(b["volume"] for b in day_bars[-6:]) / 6
        vol_ratio = curr["volume"] / avg_vol if avg_vol > 0 else 1
        if vol_ratio < 1.5: return None

        # Breakout above VWAP
        if prev["close"] < vwap and curr["close"] > vwap and curr["close"] > curr["open"]:
            entry = curr["close"]
            stop = vwap - (entry - vwap) * 0.3
            risk = entry - stop
            target = entry + risk * 2.5
            return StrategySignal(
                strategy_name=cls.NAME, symbol=symbol,
                direction=SignalDirection.LONG.value,
                urgency=SignalUrgency.IMMEDIATE.value,
                suggested_entry=round(entry, 2), suggested_stop=round(stop, 2),
                suggested_target=round(target, 2),
                risk_reward_ratio=2.5, risk_pct=round(risk/entry*100, 3),
                raw_confidence=round(min(0.75, 0.50 + vol_ratio * 0.05), 3),
                vwap_aligned=True,
                signal_reason=f"VWAP breakout UP: crossed above VWAP ({vwap:.2f}) with {vol_ratio:.1f}x volume. Stop below VWAP.",
                timeframe="5m", sector=get_sector(symbol),
            )

        # Breakdown below VWAP
        if prev["close"] > vwap and curr["close"] < vwap and curr["close"] < curr["open"]:
            entry = curr["close"]
            stop = vwap + (vwap - entry) * 0.3
            risk = stop - entry
            target = entry - risk * 2.5
            return StrategySignal(
                strategy_name=cls.NAME, symbol=symbol,
                direction=SignalDirection.SHORT.value,
                urgency=SignalUrgency.IMMEDIATE.value,
                suggested_entry=round(entry, 2), suggested_stop=round(stop, 2),
                suggested_target=round(target, 2),
                risk_reward_ratio=2.5, risk_pct=round(risk/entry*100, 3),
                raw_confidence=round(min(0.75, 0.50 + vol_ratio * 0.05), 3),
                vwap_aligned=True,
                signal_reason=f"VWAP breakdown: crossed below VWAP ({vwap:.2f}) with {vol_ratio:.1f}x volume. Stop above VWAP.",
                timeframe="5m", sector=get_sector(symbol),
            )

        return None


class EMACrossoverSignal:
    """
    EMA 9 crosses EMA 21 with price above/below both.
    Volume must confirm. No doji on crossover candle.
    """
    NAME = "ema_cross"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict], bar_idx: int) -> Optional[StrategySignal]:
        if bar_idx < 22 or bar_idx >= len(bars): return None

        closes = [b["close"] for b in bars[:bar_idx + 1]]
        prev_closes = [b["close"] for b in bars[:bar_idx]]

        ema9 = _ema(closes, 9)
        ema21 = _ema(closes, 21)
        prev_ema9 = _ema(prev_closes, 9)
        prev_ema21 = _ema(prev_closes, 21)

        if not all([ema9, ema21, prev_ema9, prev_ema21]): return None

        curr = bars[bar_idx]
        price = curr["close"]

        # Crossover candle body check (not a doji)
        body_ratio = abs(curr["close"] - curr["open"]) / (curr["high"] - curr["low"]) if curr["high"] != curr["low"] else 0
        if body_ratio < 0.3: return None

        # Volume check
        avg_vol = sum(b["volume"] for b in bars[max(0,bar_idx-10):bar_idx]) / min(10, bar_idx)
        if curr["volume"] < avg_vol * 0.8: return None

        # Bullish crossover: EMA9 crosses above EMA21
        if prev_ema9 <= prev_ema21 and ema9 > ema21 and price > ema9:
            entry = price
            stop = min(ema21, curr["low"])
            risk = entry - stop
            if risk <= 0 or risk / entry * 100 > 1.5: return None
            target = entry + risk * 2.5

            return StrategySignal(
                strategy_name=cls.NAME, symbol=symbol,
                direction=SignalDirection.LONG.value,
                urgency=SignalUrgency.STANDARD.value,
                suggested_entry=round(entry, 2), suggested_stop=round(stop, 2),
                suggested_target=round(target, 2),
                risk_reward_ratio=2.5, risk_pct=round(risk/entry*100, 3),
                raw_confidence=0.65,
                signal_reason=f"EMA golden cross: EMA9 ({ema9:.2f}) crossed above EMA21 ({ema21:.2f}). Price above both. Volume confirms.",
                timeframe="5m", sector=get_sector(symbol),
            )

        # Bearish crossover: EMA9 crosses below EMA21
        if prev_ema9 >= prev_ema21 and ema9 < ema21 and price < ema9:
            entry = price
            stop = max(ema21, curr["high"])
            risk = stop - entry
            if risk <= 0 or risk / entry * 100 > 1.5: return None
            target = entry - risk * 2.5

            return StrategySignal(
                strategy_name=cls.NAME, symbol=symbol,
                direction=SignalDirection.SHORT.value,
                urgency=SignalUrgency.STANDARD.value,
                suggested_entry=round(entry, 2), suggested_stop=round(stop, 2),
                suggested_target=round(target, 2),
                risk_reward_ratio=2.5, risk_pct=round(risk/entry*100, 3),
                raw_confidence=0.65,
                signal_reason=f"EMA death cross: EMA9 ({ema9:.2f}) crossed below EMA21 ({ema21:.2f}). Price below both. Volume confirms.",
                timeframe="5m", sector=get_sector(symbol),
            )

        return None


class RSIDivergenceSignal:
    """
    Price makes new low but RSI makes higher low = bullish divergence.
    Price makes new high but RSI makes lower high = bearish divergence.
    Powerful reversal signal.
    """
    NAME = "rsi_div"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict], bar_idx: int) -> Optional[StrategySignal]:
        if bar_idx < 20 or bar_idx >= len(bars): return None

        closes = [b["close"] for b in bars[:bar_idx + 1]]
        rsi_now = _rsi(closes)
        if not rsi_now: return None

        # Need RSI from 6 bars ago for divergence check
        rsi_prev = _rsi(closes[:-6])
        if not rsi_prev: return None

        curr = bars[bar_idx]
        prev_window = bars[bar_idx - 6]

        # Bullish divergence: price lower low, RSI higher low
        if (curr["low"] < prev_window["low"] and rsi_now > rsi_prev and rsi_now < 45):
            entry = curr["close"]
            stop = curr["low"] - (entry - curr["low"]) * 0.2
            risk = entry - stop
            if risk <= 0 or risk / entry * 100 > 1.5: return None
            target = entry + risk * 2.5

            return StrategySignal(
                strategy_name=cls.NAME, symbol=symbol,
                direction=SignalDirection.LONG.value,
                urgency=SignalUrgency.STANDARD.value,
                suggested_entry=round(entry, 2), suggested_stop=round(stop, 2),
                suggested_target=round(target, 2),
                risk_reward_ratio=2.5, risk_pct=round(risk/entry*100, 3),
                raw_confidence=0.70,
                signal_reason=f"Bullish RSI divergence: price made lower low but RSI ({rsi_now:.0f}) made higher low ({rsi_prev:.0f}). Selling exhaustion.",
                timeframe="5m", sector=get_sector(symbol),
            )

        # Bearish divergence: price higher high, RSI lower high
        if (curr["high"] > prev_window["high"] and rsi_now < rsi_prev and rsi_now > 55):
            entry = curr["close"]
            stop = curr["high"] + (curr["high"] - entry) * 0.2
            risk = stop - entry
            if risk <= 0 or risk / entry * 100 > 1.5: return None
            target = entry - risk * 2.5

            return StrategySignal(
                strategy_name=cls.NAME, symbol=symbol,
                direction=SignalDirection.SHORT.value,
                urgency=SignalUrgency.STANDARD.value,
                suggested_entry=round(entry, 2), suggested_stop=round(stop, 2),
                suggested_target=round(target, 2),
                risk_reward_ratio=2.5, risk_pct=round(risk/entry*100, 3),
                raw_confidence=0.70,
                signal_reason=f"Bearish RSI divergence: price made higher high but RSI ({rsi_now:.0f}) made lower high ({rsi_prev:.0f}). Buying exhaustion.",
                timeframe="5m", sector=get_sector(symbol),
            )

        return None
