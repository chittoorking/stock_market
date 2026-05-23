"""
MEGA STRATEGY LIBRARY — every intraday strategy I know.
Each one detects a specific market condition and generates a signal.
All work on 5-min OHLCV. Same StrategySignal interface.

From my training on:
- Prop desk playbooks (SMB Capital, Axia, T3)
- Quantitative research (Chan, Narang, de Prado)
- Indian market specialists (Zerodha Varsity, NSE strategies)
- Price action masters (Al Brooks, Bob Volman, Lance Beggs)
- Technical analysis classics (Murphy, Bulkowski, Kirkpatrick)

Categories:
A. BREAKOUT strategies (trade the break of a range)
B. MOMENTUM strategies (trade strong directional moves)
C. MEAN REVERSION strategies (trade extremes back to mean)
D. PATTERN strategies (trade specific candlestick/chart patterns)
E. FLOW strategies (trade institutional order flow signals)
"""
from __future__ import annotations
from typing import List, Dict, Optional
from app.signals.base import StrategySignal, SignalUrgency

# ═══════════════════════════════════════════════════════════════
# A. BREAKOUT STRATEGIES
# ═══════════════════════════════════════════════════════════════

class OpeningRangeBreakout:
    """ORB — first 30-min range defines the day. Break = trend.
    From: Toby Crabel's 'Day Trading with Short Term Price Patterns'"""
    NAME = "orb"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict], prev_close: float = 0) -> Optional[StrategySignal]:
        if len(bars) < 7:  # Need 6+ bars (30 min) + breakout bar
            return None
        # Opening range = first 6 bars (30 min of 5-min)
        or_high = max(b['high'] for b in bars[:6])
        or_low = min(b['low'] for b in bars[:6])
        or_range = or_high - or_low
        if or_range <= 0: return None

        current = bars[-1]
        # Breakout above OR high
        if current['close'] > or_high and current['close'] > current['open']:
            entry = current['close']
            stop = or_high - or_range * 0.3
            target = entry + or_range  # 1x OR range target
            conf = 0.65
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                suggested_entry=round(entry,2), suggested_stop=round(stop,2),
                suggested_target=round(target,2), raw_confidence=conf,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason=f"ORB break above {or_high:.2f}, range={or_range:.2f}")
        # Breakout below OR low
        elif current['close'] < or_low and current['close'] < current['open']:
            entry = current['close']
            stop = or_low + or_range * 0.3
            target = entry - or_range
            conf = 0.65
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                suggested_entry=round(entry,2), suggested_stop=round(stop,2),
                suggested_target=round(target,2), raw_confidence=conf,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason=f"ORB break below {or_low:.2f}, range={or_range:.2f}")
        return None


class PivotBreakout:
    """Pivot R1/S1 breakout — institutional reference levels.
    From: Floor trader pivot points, used by every prop desk."""
    NAME = "pvt"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict], prev_close: float = 0) -> Optional[StrategySignal]:
        if len(bars) < 4 or prev_close <= 0: return None
        # Calculate pivots from prev day (approximated from today's range + prev close)
        day_high = max(b['high'] for b in bars)
        day_low = min(b['low'] for b in bars)
        pivot = (day_high + day_low + prev_close) / 3
        r1 = 2 * pivot - day_low
        s1 = 2 * pivot - day_high
        current = bars[-1]
        price = current['close']
        atr = sum(b['high']-b['low'] for b in bars) / len(bars)

        if price > r1 and current['close'] > current['open']:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                suggested_entry=round(price,2), suggested_stop=round(r1 - atr,2),
                suggested_target=round(price + atr*2,2), raw_confidence=0.62,
                urgency=SignalUrgency.STANDARD,
                signal_reason=f"Pivot R1 break {r1:.2f}")
        elif price < s1 and current['close'] < current['open']:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                suggested_entry=round(price,2), suggested_stop=round(s1 + atr,2),
                suggested_target=round(price - atr*2,2), raw_confidence=0.62,
                urgency=SignalUrgency.STANDARD,
                signal_reason=f"Pivot S1 break {s1:.2f}")
        return None


class PreviousDayHighLowBreak:
    """PDH/PDL break — previous day's high/low as key levels.
    From: Mark Fisher's ACD method, Linda Raschke."""
    NAME = "pdhl"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict], prev_close: float = 0, prev_high: float = 0, prev_low: float = 0) -> Optional[StrategySignal]:
        if len(bars) < 3 or prev_high <= 0: return None
        current = bars[-1]
        price = current['close']
        atr = sum(b['high']-b['low'] for b in bars) / len(bars)

        if price > prev_high and current['close'] > current['open']:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                suggested_entry=round(price,2), suggested_stop=round(prev_high - atr*0.5,2),
                suggested_target=round(price + (price-prev_high)*2,2), raw_confidence=0.60,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason=f"PDH break {prev_high:.2f}")
        elif price < prev_low and current['close'] < current['open']:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                suggested_entry=round(price,2), suggested_stop=round(prev_low + atr*0.5,2),
                suggested_target=round(price - (prev_low-price)*2,2), raw_confidence=0.60,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason=f"PDL break {prev_low:.2f}")
        return None


# ═══════════════════════════════════════════════════════════════
# B. MOMENTUM STRATEGIES
# ═══════════════════════════════════════════════════════════════

class EMA9_21Cross:
    """EMA crossover — trend confirmation.
    From: Every technical analysis textbook. Simple but effective on 5-min."""
    NAME = "emx"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        if len(bars) < 22: return None
        closes = [b['close'] for b in bars]
        def ema(data, period):
            mult = 2/(period+1)
            val = sum(data[:period])/period
            for p in data[period:]: val = (p-val)*mult+val
            return val
        ema9_now = ema(closes, 9)
        ema21_now = ema(closes, 21)
        ema9_prev = ema(closes[:-1], 9)
        ema21_prev = ema(closes[:-1], 21)

        atr = sum(b['high']-b['low'] for b in bars[-10:]) / 10
        price = closes[-1]

        # Bullish cross: EMA9 crosses above EMA21
        if ema9_prev <= ema21_prev and ema9_now > ema21_now:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                suggested_entry=round(price,2), suggested_stop=round(price-atr*1.5,2),
                suggested_target=round(price+atr*3,2), raw_confidence=0.60,
                urgency=SignalUrgency.STANDARD,
                signal_reason=f"EMA9/21 bullish cross at {price:.2f}")
        # Bearish cross
        elif ema9_prev >= ema21_prev and ema9_now < ema21_now:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                suggested_entry=round(price,2), suggested_stop=round(price+atr*1.5,2),
                suggested_target=round(price-atr*3,2), raw_confidence=0.60,
                urgency=SignalUrgency.STANDARD,
                signal_reason=f"EMA9/21 bearish cross at {price:.2f}")
        return None


class SuperTrendSignal:
    """SuperTrend — ATR-based trend following.
    From: Olivier Seban. Very popular in Indian markets."""
    NAME = "str"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        if len(bars) < 12: return None
        period = 10; mult = 3
        # ATR
        trs = [max(bars[i]['high']-bars[i]['low'],
                   abs(bars[i]['high']-bars[i-1]['close']),
                   abs(bars[i]['low']-bars[i-1]['close'])) for i in range(1, len(bars))]
        atr = sum(trs[-period:]) / period if len(trs) >= period else sum(trs)/len(trs)

        hl2 = (bars[-1]['high'] + bars[-1]['low']) / 2
        upper = hl2 + mult * atr
        lower = hl2 - mult * atr
        price = bars[-1]['close']

        hl2_prev = (bars[-2]['high'] + bars[-2]['low']) / 2
        upper_prev = hl2_prev + mult * atr
        lower_prev = hl2_prev - mult * atr
        prev_price = bars[-2]['close']

        # Flip from bearish to bullish
        if prev_price < lower_prev and price > lower:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                suggested_entry=round(price,2), suggested_stop=round(lower,2),
                suggested_target=round(price + (price-lower)*2,2), raw_confidence=0.62,
                urgency=SignalUrgency.STANDARD,
                signal_reason=f"SuperTrend flip bullish at {price:.2f}")
        # Flip from bullish to bearish
        elif prev_price > upper_prev and price < upper:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                suggested_entry=round(price,2), suggested_stop=round(upper,2),
                suggested_target=round(price - (upper-price)*2,2), raw_confidence=0.62,
                urgency=SignalUrgency.STANDARD,
                signal_reason=f"SuperTrend flip bearish at {price:.2f}")
        return None


class VWAPCrossSignal:
    """VWAP cross — institutional benchmark.
    From: Every institutional desk. VWAP is THE reference price."""
    NAME = "vwc"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        if len(bars) < 5: return None
        # Calculate VWAP
        cum_tp_vol = 0; cum_vol = 0
        vwaps = []
        for b in bars:
            tp = (b['high']+b['low']+b['close'])/3
            cum_tp_vol += tp * b['volume']
            cum_vol += b['volume']
            vwaps.append(cum_tp_vol/cum_vol if cum_vol > 0 else b['close'])

        vwap = vwaps[-1]
        price = bars[-1]['close']
        prev_price = bars[-2]['close']
        prev_vwap = vwaps[-2]
        atr = sum(b['high']-b['low'] for b in bars[-5:]) / 5

        # Cross above VWAP
        if prev_price < prev_vwap and price > vwap and bars[-1]['close'] > bars[-1]['open']:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                suggested_entry=round(price,2), suggested_stop=round(vwap - atr,2),
                suggested_target=round(price + atr*2.5,2), raw_confidence=0.63,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason=f"VWAP cross above {vwap:.2f}")
        # Cross below VWAP
        elif prev_price > prev_vwap and price < vwap and bars[-1]['close'] < bars[-1]['open']:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                suggested_entry=round(price,2), suggested_stop=round(vwap + atr,2),
                suggested_target=round(price - atr*2.5,2), raw_confidence=0.63,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason=f"VWAP cross below {vwap:.2f}")
        return None


class MomentumBurst:
    """Momentum burst — sudden acceleration in price.
    From: Larry Connors, Cesar Alvarez — mean reversion with momentum entry."""
    NAME = "mbr"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        if len(bars) < 6: return None
        # Current bar range vs average range
        curr_range = bars[-1]['high'] - bars[-1]['low']
        avg_range = sum(b['high']-b['low'] for b in bars[-6:-1]) / 5
        if avg_range <= 0: return None

        range_ratio = curr_range / avg_range
        price = bars[-1]['close']
        atr = avg_range

        # Burst: current bar > 2x average range with strong body
        body = abs(bars[-1]['close'] - bars[-1]['open'])
        body_ratio = body / curr_range if curr_range > 0 else 0

        if range_ratio > 2.0 and body_ratio > 0.6:
            if bars[-1]['close'] > bars[-1]['open']:  # Bullish burst
                return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                    suggested_entry=round(price,2), suggested_stop=round(bars[-1]['low'],2),
                    suggested_target=round(price + curr_range,2), raw_confidence=0.58,
                    urgency=SignalUrgency.IMMEDIATE,
                    signal_reason=f"Momentum burst {range_ratio:.1f}x avg range")
            else:  # Bearish burst
                return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                    suggested_entry=round(price,2), suggested_stop=round(bars[-1]['high'],2),
                    suggested_target=round(price - curr_range,2), raw_confidence=0.58,
                    urgency=SignalUrgency.IMMEDIATE,
                    signal_reason=f"Momentum burst {range_ratio:.1f}x avg range")
        return None


# ═══════════════════════════════════════════════════════════════
# C. MEAN REVERSION STRATEGIES
# ═══════════════════════════════════════════════════════════════

class RSIExtreme:
    """RSI extreme reversal — oversold/overbought bounce.
    From: Welles Wilder, Larry Connors RSI2 strategy."""
    NAME = "rsi"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        if len(bars) < 15: return None
        closes = [b['close'] for b in bars]
        gains = [max(0, closes[i]-closes[i-1]) for i in range(1, len(closes))]
        losses = [max(0, closes[i-1]-closes[i]) for i in range(1, len(closes))]
        ag = sum(gains[-14:]) / 14
        al = sum(losses[-14:]) / 14
        rsi = 100 - 100/(1+ag/al) if al > 0 else (100 if ag > 0 else 50)

        price = closes[-1]
        atr = sum(b['high']-b['low'] for b in bars[-5:]) / 5

        # Oversold bounce (RSI < 25 + bullish candle)
        if rsi < 25 and bars[-1]['close'] > bars[-1]['open']:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                suggested_entry=round(price,2), suggested_stop=round(bars[-1]['low'] - atr*0.3,2),
                suggested_target=round(price + atr*2,2), raw_confidence=0.58,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason=f"RSI {rsi:.0f} oversold bounce")

        # Overbought reversal (RSI > 75 + bearish candle)
        elif rsi > 75 and bars[-1]['close'] < bars[-1]['open']:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                suggested_entry=round(price,2), suggested_stop=round(bars[-1]['high'] + atr*0.3,2),
                suggested_target=round(price - atr*2,2), raw_confidence=0.58,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason=f"RSI {rsi:.0f} overbought reversal")
        return None


class BollingerBounce:
    """Bollinger Band extreme — price at band edge + reversal candle.
    From: John Bollinger, Keltner Channel variant."""
    NAME = "bbb"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        if len(bars) < 20: return None
        closes = [b['close'] for b in bars]
        sma = sum(closes[-20:]) / 20
        std = (sum((c-sma)**2 for c in closes[-20:]) / 20) ** 0.5
        upper = sma + 2*std
        lower = sma - 2*std
        price = closes[-1]
        atr = sum(b['high']-b['low'] for b in bars[-5:]) / 5

        # Lower band bounce
        if price <= lower and bars[-1]['close'] > bars[-1]['open']:
            lower_wick = min(bars[-1]['close'],bars[-1]['open']) - bars[-1]['low']
            body = abs(bars[-1]['close']-bars[-1]['open'])
            if lower_wick > body * 0.5:  # Rejection wick
                return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                    suggested_entry=round(price,2), suggested_stop=round(bars[-1]['low']-atr*0.2,2),
                    suggested_target=round(sma,2), raw_confidence=0.60,
                    urgency=SignalUrgency.IMMEDIATE,
                    signal_reason=f"BB lower bounce, target SMA {sma:.2f}")

        # Upper band rejection
        elif price >= upper and bars[-1]['close'] < bars[-1]['open']:
            upper_wick = bars[-1]['high'] - max(bars[-1]['close'],bars[-1]['open'])
            body = abs(bars[-1]['close']-bars[-1]['open'])
            if upper_wick > body * 0.5:
                return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                    suggested_entry=round(price,2), suggested_stop=round(bars[-1]['high']+atr*0.2,2),
                    suggested_target=round(sma,2), raw_confidence=0.60,
                    urgency=SignalUrgency.IMMEDIATE,
                    signal_reason=f"BB upper rejection, target SMA {sma:.2f}")
        return None


# ═══════════════════════════════════════════════════════════════
# D. PATTERN STRATEGIES
# ═══════════════════════════════════════════════════════════════

class EngulfingPattern:
    """Engulfing candle — strong reversal signal.
    From: Steve Nison's Japanese Candlestick Charting."""
    NAME = "eng"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        if len(bars) < 3: return None
        curr = bars[-1]; prev = bars[-2]
        atr = sum(b['high']-b['low'] for b in bars[-5:]) / min(5, len(bars))

        # Bullish engulfing
        if (prev['close'] < prev['open'] and  # prev was red
            curr['close'] > curr['open'] and   # curr is green
            curr['open'] <= prev['close'] and   # opens at/below prev close
            curr['close'] >= prev['open']):     # closes at/above prev open
            price = curr['close']
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                suggested_entry=round(price,2), suggested_stop=round(curr['low']-atr*0.2,2),
                suggested_target=round(price+atr*2.5,2), raw_confidence=0.62,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason="Bullish engulfing pattern")

        # Bearish engulfing
        if (prev['close'] > prev['open'] and
            curr['close'] < curr['open'] and
            curr['open'] >= prev['close'] and
            curr['close'] <= prev['open']):
            price = curr['close']
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                suggested_entry=round(price,2), suggested_stop=round(curr['high']+atr*0.2,2),
                suggested_target=round(price-atr*2.5,2), raw_confidence=0.62,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason="Bearish engulfing pattern")
        return None


class InsideBarBreakout:
    """Inside bar breakout — compression then expansion.
    From: Price action trading, Nial Fuller."""
    NAME = "ibs"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        if len(bars) < 3: return None
        mother = bars[-2]; curr = bars[-1]
        atr = sum(b['high']-b['low'] for b in bars[-5:]) / min(5, len(bars))

        # Check if previous bar was inside bar (contained within bar before it)
        if len(bars) >= 3:
            grandma = bars[-3]
            is_inside = mother['high'] <= grandma['high'] and mother['low'] >= grandma['low']
            if not is_inside: return None

            # Breakout above mother bar high
            if curr['close'] > mother['high'] and curr['close'] > curr['open']:
                price = curr['close']
                return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                    suggested_entry=round(price,2), suggested_stop=round(mother['low'],2),
                    suggested_target=round(price + (mother['high']-mother['low'])*2,2),
                    raw_confidence=0.60, urgency=SignalUrgency.IMMEDIATE,
                    signal_reason="Inside bar breakout UP")

            # Breakout below mother bar low
            elif curr['close'] < mother['low'] and curr['close'] < curr['open']:
                price = curr['close']
                return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                    suggested_entry=round(price,2), suggested_stop=round(mother['high'],2),
                    suggested_target=round(price - (mother['high']-mother['low'])*2,2),
                    raw_confidence=0.60, urgency=SignalUrgency.IMMEDIATE,
                    signal_reason="Inside bar breakout DOWN")
        return None


class HammerShootingStar:
    """Hammer/Shooting Star — single candle reversal.
    From: Steve Nison, Thomas Bulkowski pattern recognition."""
    NAME = "hss"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        if len(bars) < 5: return None
        curr = bars[-1]
        body = abs(curr['close'] - curr['open'])
        rng = curr['high'] - curr['low']
        if rng <= 0 or body <= 0: return None
        atr = sum(b['high']-b['low'] for b in bars[-5:]) / 5

        lower_wick = min(curr['close'], curr['open']) - curr['low']
        upper_wick = curr['high'] - max(curr['close'], curr['open'])

        # Check for downtrend/uptrend context
        trend = (bars[-1]['close'] - bars[-5]['close']) / bars[-5]['close'] * 100

        # Hammer (in downtrend): long lower wick, small body at top
        if trend < -0.3 and lower_wick > body * 2 and upper_wick < body * 0.5:
            price = curr['close']
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                suggested_entry=round(price,2), suggested_stop=round(curr['low']-atr*0.1,2),
                suggested_target=round(price+atr*2,2), raw_confidence=0.60,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason=f"Hammer in downtrend ({trend:.1f}%)")

        # Shooting Star (in uptrend): long upper wick, small body at bottom
        if trend > 0.3 and upper_wick > body * 2 and lower_wick < body * 0.5:
            price = curr['close']
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                suggested_entry=round(price,2), suggested_stop=round(curr['high']+atr*0.1,2),
                suggested_target=round(price-atr*2,2), raw_confidence=0.60,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason=f"Shooting star in uptrend ({trend:.1f}%)")
        return None


# ═══════════════════════════════════════════════════════════════
# E. FLOW / VOLUME STRATEGIES
# ═══════════════════════════════════════════════════════════════

class VolumeSpike:
    """Volume spike with direction — institutional entry.
    From: Anna Coulling 'A Complete Guide to Volume Price Analysis'."""
    NAME = "vsp"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        if len(bars) < 6: return None
        avg_vol = sum(b['volume'] for b in bars[:-1]) / (len(bars)-1)
        curr = bars[-1]
        if avg_vol <= 0: return None

        vol_ratio = curr['volume'] / avg_vol
        body = abs(curr['close'] - curr['open'])
        rng = curr['high'] - curr['low']
        body_ratio = body / rng if rng > 0 else 0
        atr = sum(b['high']-b['low'] for b in bars[-5:]) / 5

        # Volume spike (3x+) with strong directional candle (body > 60%)
        if vol_ratio >= 3.0 and body_ratio > 0.6:
            price = curr['close']
            if curr['close'] > curr['open']:  # Bullish volume spike
                return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                    suggested_entry=round(price,2), suggested_stop=round(curr['low'],2),
                    suggested_target=round(price+atr*2.5,2), raw_confidence=0.60,
                    urgency=SignalUrgency.IMMEDIATE,
                    signal_reason=f"Volume spike {vol_ratio:.1f}x bullish")
            else:  # Bearish volume spike
                return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                    suggested_entry=round(price,2), suggested_stop=round(curr['high'],2),
                    suggested_target=round(price-atr*2.5,2), raw_confidence=0.60,
                    urgency=SignalUrgency.IMMEDIATE,
                    signal_reason=f"Volume spike {vol_ratio:.1f}x bearish")
        return None


class DayHighLowBreak:
    """Intraday high/low breakout — momentum continuation.
    From: Every prop desk uses this as a core setup."""
    NAME = "dhl"

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        if len(bars) < 5: return None
        # Current bar breaks today's high/low made before this bar
        prev_high = max(b['high'] for b in bars[:-1])
        prev_low = min(b['low'] for b in bars[:-1])
        curr = bars[-1]
        price = curr['close']
        atr = sum(b['high']-b['low'] for b in bars[-5:]) / 5

        # New intraday high with volume
        if curr['high'] > prev_high and curr['close'] > prev_high and curr['close'] > curr['open']:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="LONG",
                suggested_entry=round(price,2), suggested_stop=round(prev_high - atr*0.5,2),
                suggested_target=round(price + atr*2,2), raw_confidence=0.58,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason=f"New day high break {prev_high:.2f}")
        # New intraday low
        elif curr['low'] < prev_low and curr['close'] < prev_low and curr['close'] < curr['open']:
            return StrategySignal(strategy_name=cls.NAME, symbol=symbol, direction="SHORT",
                suggested_entry=round(price,2), suggested_stop=round(prev_low + atr*0.5,2),
                suggested_target=round(price - atr*2,2), raw_confidence=0.58,
                urgency=SignalUrgency.IMMEDIATE,
                signal_reason=f"New day low break {prev_low:.2f}")
        return None
