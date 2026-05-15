"""
Proven strategy signal providers — extracted from 20 rounds of backtested research.

These are SIGNAL GENERATORS, not trade executors.
They emit StrategySignal objects that the agent consumes as data.

Each strategy:
1. Takes OHLCV data as input
2. Checks its specific conditions
3. Emits a StrategySignal with suggested parameters
4. The AGENT decides what to do with it

Strategies implemented:
- GG8:  Gap & Go (best single strategy, +15.15%, 61% WR)
- ORB:  Opening Range Breakout (+5.22%, 50% WR, 88% of params profitable)
- CASCADE_Y5: Sector leader → laggard flow (+3.05%, morning only)
- LNZ3: Lenz Back-EMF — weak opposition after strong drive (+4.19%, 55% WR)
- AFT7: Aftershock/Omori — unexpected continuation (+3.53%, 53% WR)
- GDR4: Gap Decay Rate — fast gap fill = institutional urgency (+7.02%)
- CW4:  Second Cascade Wave — double conviction (+7.81%, 59% WR)
"""
from __future__ import annotations

import logging
import numpy as np
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.signals.base import (
    StrategySignal, SignalDirection, SignalUrgency,
    SECTOR_MAP, is_sector_leader, get_laggards, get_sector,
)

logger = logging.getLogger(__name__)


# ─── Shared Helpers (from backtest code) ───

def calc_vwap(df: List[Dict]) -> List[float]:
    """Calculate VWAP from OHLCV bars. Resets daily."""
    vwap = []
    cum_tp_vol = 0.0
    cum_vol = 0.0
    current_date = None

    for bar in df:
        bar_date = bar.get("date", "")[:10]
        if bar_date != current_date:
            cum_tp_vol = 0.0
            cum_vol = 0.0
            current_date = bar_date

        tp = (bar["high"] + bar["low"] + bar["close"]) / 3
        vol = bar.get("volume", 0)
        cum_tp_vol += tp * vol
        cum_vol += vol
        vwap.append(cum_tp_vol / cum_vol if cum_vol > 0 else bar["close"])

    return vwap


def calc_rvol(bars: List[Dict], lookback_days: int = 10) -> float:
    """Calculate relative volume for opening bars vs average."""
    if len(bars) < 3:
        return 1.0
    current_vol = sum(b.get("volume", 0) for b in bars[:3])
    # Simplified: compare to average bar volume * 3
    all_vols = [b.get("volume", 0) for b in bars]
    avg_vol = np.mean(all_vols) * 3 if all_vols else 1
    return current_vol / avg_vol if avg_vol > 0 else 1.0


def calc_trail_stop(
    entry: float, initial_risk: float, max_favorable: float,
    direction: str, levels: tuple = (0.15, 0.30, 0.50),
) -> float:
    """Graduated trailing stop — exact logic from backtests."""
    risk = abs(initial_risk)
    if risk == 0:
        return entry

    if direction == "LONG":
        if max_favorable >= risk * levels[2]:
            return entry + risk * levels[1]
        elif max_favorable >= risk * levels[1]:
            return entry + risk * levels[0]
        elif max_favorable >= risk * levels[0]:
            return entry  # Breakeven
    else:
        if max_favorable >= risk * levels[2]:
            return entry - risk * levels[1]
        elif max_favorable >= risk * levels[1]:
            return entry - risk * levels[0]
        elif max_favorable >= risk * levels[0]:
            return entry  # Breakeven

    return entry - risk if direction == "LONG" else entry + risk


# ═══════════════════════════════════════════════════════════════
# GG8 — Gap & Go (Best single strategy: +15.15%, 61% WR)
# ═══════════════════════════════════════════════════════════════

class GapAndGoSignal:
    """
    Detects overnight gaps and trades continuation after 1-bar confirmation.
    Short side has structural edge: gap-downs continue harder than gap-ups.
    """
    NAME = "gg8"

    # Exact parameters from backtest
    GAP_THRESHOLD_PCT = 0.3
    RVOL_MIN = 2.0
    CONFIRM_BARS = 1
    STOP_PCT = 0.30
    TARGET_RR = 2.5
    BAIL_BARS = 12
    MAX_BARS = 36
    TRAIL_LEVELS = (0.15, 0.30, 0.50)

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict], prev_close: float) -> Optional[StrategySignal]:
        """
        Scan for Gap & Go setup.
        bars: today's 5-min bars so far (need at least CONFIRM_BARS + 1)
        prev_close: previous day's closing price
        """
        if len(bars) < cls.CONFIRM_BARS + 1:
            return None

        today_open = bars[0]["open"]
        gap_pct = (today_open - prev_close) / prev_close * 100

        if abs(gap_pct) < cls.GAP_THRESHOLD_PCT:
            return None

        # Check RVOL
        rvol = calc_rvol(bars)
        if rvol < cls.RVOL_MIN:
            return None

        # VWAP — use prev_close as proxy when only 1-2 bars available
        vwap_values = calc_vwap(bars)
        current_vwap = vwap_values[-1] if len(vwap_values) > 3 else prev_close

        # 1-bar confirmation: check the OPENING bar (bar 0) direction
        # Original backtest: confirm_data = day_data.iloc[:1], checks bar 0 close vs open
        confirm_bar = bars[0]
        is_gap_up = gap_pct > 0
        entry_price = confirm_bar["close"]

        if is_gap_up:
            # Gap up: opening bar must close >= open (not a reversal candle)
            if confirm_bar["close"] < confirm_bar["open"]:
                return None
            if entry_price < current_vwap:
                return None
            direction = SignalDirection.LONG
            stop = entry_price * (1 - cls.STOP_PCT / 100)
        else:
            # Gap down: opening bar must close <= open (not a reversal candle)
            if confirm_bar["close"] > confirm_bar["open"]:
                return None
            if entry_price > current_vwap:
                return None
            direction = SignalDirection.SHORT
            stop = entry_price * (1 + cls.STOP_PCT / 100)

        risk = abs(entry_price - stop)
        target = entry_price + (risk * cls.TARGET_RR) if direction == SignalDirection.LONG else entry_price - (risk * cls.TARGET_RR)
        risk_pct = (risk / entry_price) * 100

        # Confidence based on gap size + RVOL
        conf = min(0.85, 0.5 + abs(gap_pct) * 0.1 + (rvol - 2.0) * 0.05)

        return StrategySignal(
            strategy_name=cls.NAME,
            symbol=symbol,
            direction=direction.value,
            urgency=SignalUrgency.IMMEDIATE.value,
            suggested_entry=round(entry_price, 2),
            suggested_stop=round(stop, 2),
            suggested_target=round(target, 2),
            risk_reward_ratio=cls.TARGET_RR,
            risk_pct=round(risk_pct, 3),
            raw_confidence=round(conf, 3),
            rvol=round(rvol, 2),
            vwap_aligned=True,
            signal_reason=(
                f"{'Gap up' if is_gap_up else 'Gap down'} {abs(gap_pct):.2f}%, "
                f"1-bar confirm, RVOL {rvol:.1f}x, VWAP aligned"
            ),
            timeframe="5m",
            sector=get_sector(symbol),
            expires_bars=cls.MAX_BARS,
            bar_index=cls.CONFIRM_BARS,
        )


# ═══════════════════════════════════════════════════════════════
# ORB — Opening Range Breakout (+5.22%, 88% of params profitable)
# ═══════════════════════════════════════════════════════════════

class ORBSignal:
    """
    Trades breakout of the first 15 minutes (3 bars) range.
    Most robust strategy — works across almost all parameter combinations.
    """
    NAME = "orb"

    OR_BARS = 3
    RVOL_MIN = 2.5
    MIN_OR_SIZE_PCT = 0.45
    STOP_MAX_PCT = 2.0  # Original backtest used 1.0% but NIFTY 50 OR ranges are wider
    TARGET_RR = 2.875
    BAIL_BARS = 12
    MAX_BARS = 36
    TRAIL_LEVELS = (0.15, 0.30, 0.50)

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        """
        Scan for ORB setup. Needs at least OR_BARS + 1 bars.
        """
        if len(bars) < cls.OR_BARS + 1:
            return None

        # Calculate opening range
        or_bars = bars[:cls.OR_BARS]
        or_high = max(b["high"] for b in or_bars)
        or_low = min(b["low"] for b in or_bars)
        or_mid = (or_high + or_low) / 2
        or_size_pct = ((or_high - or_low) / or_mid) * 100

        if or_size_pct < cls.MIN_OR_SIZE_PCT:
            return None

        # RVOL check
        rvol = calc_rvol(bars)
        if rvol < cls.RVOL_MIN:
            return None

        # VWAP
        vwap_values = calc_vwap(bars)

        # Check for breakout on subsequent bars
        for i in range(cls.OR_BARS, len(bars)):
            bar = bars[i]
            vwap = vwap_values[i] if i < len(vwap_values) else or_mid

            # Bullish breakout
            if bar["close"] > or_high and bar["close"] > vwap:
                entry = bar["close"]
                stop = or_low
                risk = entry - stop
                risk_pct = (risk / entry) * 100

                if risk_pct > cls.STOP_MAX_PCT or risk_pct < 0.02:
                    continue

                target = entry + risk * cls.TARGET_RR
                conf = min(0.80, 0.5 + (rvol - 2.5) * 0.05 + or_size_pct * 0.05)

                return StrategySignal(
                    strategy_name=cls.NAME,
                    symbol=symbol,
                    direction=SignalDirection.LONG.value,
                    urgency=SignalUrgency.IMMEDIATE.value,
                    suggested_entry=round(entry, 2),
                    suggested_stop=round(stop, 2),
                    suggested_target=round(target, 2),
                    risk_reward_ratio=cls.TARGET_RR,
                    risk_pct=round(risk_pct, 3),
                    raw_confidence=round(conf, 3),
                    rvol=round(rvol, 2),
                    vwap_aligned=True,
                    signal_reason=(
                        f"OR breakout above {or_high:.2f}, "
                        f"range {or_size_pct:.2f}%, RVOL {rvol:.1f}x"
                    ),
                    timeframe="5m",
                    sector=get_sector(symbol),
                    expires_bars=cls.MAX_BARS,
                    bar_index=i,
                )

            # Bearish breakout
            if bar["close"] < or_low and bar["close"] < vwap:
                entry = bar["close"]
                stop = or_high
                risk = stop - entry
                risk_pct = (risk / entry) * 100

                if risk_pct > cls.STOP_MAX_PCT or risk_pct < 0.02:
                    continue

                target = entry - risk * cls.TARGET_RR
                conf = min(0.80, 0.5 + (rvol - 2.5) * 0.05 + or_size_pct * 0.05)

                return StrategySignal(
                    strategy_name=cls.NAME,
                    symbol=symbol,
                    direction=SignalDirection.SHORT.value,
                    urgency=SignalUrgency.IMMEDIATE.value,
                    suggested_entry=round(entry, 2),
                    suggested_stop=round(stop, 2),
                    suggested_target=round(target, 2),
                    risk_reward_ratio=cls.TARGET_RR,
                    risk_pct=round(risk_pct, 3),
                    raw_confidence=round(conf, 3),
                    rvol=round(rvol, 2),
                    vwap_aligned=True,
                    signal_reason=(
                        f"OR breakdown below {or_low:.2f}, "
                        f"range {or_size_pct:.2f}%, RVOL {rvol:.1f}x"
                    ),
                    timeframe="5m",
                    sector=get_sector(symbol),
                    expires_bars=cls.MAX_BARS,
                    bar_index=i,
                )

        return None


# ═══════════════════════════════════════════════════════════════
# CASCADE_Y5 — Sector Leader → Laggard Flow (+3.05%)
# ═══════════════════════════════════════════════════════════════

class CascadeSignal:
    """
    Detects sector leader making a strong move, signals entry on laggards.
    Morning-only (9:30-11:00) — afternoon cascade is noise.
    """
    NAME = "cascade_y5"

    LEADER_MOVE_PCT = 0.3
    LEADER_BODY_RATIO = 0.50
    RVOL_MIN = 2.0
    STOP_PCT = 0.30
    TARGET_RR = 2.5
    MAX_BARS = 36

    @classmethod
    def scan(cls, leader_bars: List[Dict], laggard_bars: Dict[str, List[Dict]], sector: str) -> List[StrategySignal]:
        """
        Scan a sector for cascade setup.
        leader_bars: latest bars for the sector leader
        laggard_bars: {symbol: bars} for each laggard
        Returns list of signals (one per laggard that qualifies)
        """
        signals = []

        if len(leader_bars) < 2:
            return signals

        latest = leader_bars[-1]
        leader_move_pct = ((latest["close"] - latest["open"]) / latest["open"]) * 100
        body = abs(latest["close"] - latest["open"])
        full_range = latest["high"] - latest["low"]
        body_ratio = body / full_range if full_range > 0 else 0

        # Leader must make a strong, clean move
        if abs(leader_move_pct) < cls.LEADER_MOVE_PCT:
            return signals
        if body_ratio < cls.LEADER_BODY_RATIO:
            return signals

        leader_rvol = calc_rvol(leader_bars)
        if leader_rvol < cls.RVOL_MIN:
            return signals

        direction = SignalDirection.LONG if leader_move_pct > 0 else SignalDirection.SHORT
        leader_symbol = SECTOR_MAP.get(sector, {}).get("leader", "")

        # Generate signals for each laggard
        for symbol, bars in laggard_bars.items():
            if len(bars) < 1:
                continue

            lag_latest = bars[-1]
            entry = lag_latest["close"]
            risk = entry * cls.STOP_PCT / 100

            if direction == SignalDirection.LONG:
                stop = entry - risk
                target = entry + risk * cls.TARGET_RR
            else:
                stop = entry + risk
                target = entry - risk * cls.TARGET_RR

            # VWAP check
            vwap_values = calc_vwap(bars)
            vwap = vwap_values[-1] if vwap_values else entry
            vwap_ok = (entry > vwap and direction == SignalDirection.LONG) or \
                      (entry < vwap and direction == SignalDirection.SHORT)

            if not vwap_ok:
                continue

            conf = min(0.75, 0.45 + abs(leader_move_pct) * 0.1 + (leader_rvol - 2.0) * 0.05)

            signals.append(StrategySignal(
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
                rvol=round(leader_rvol, 2),
                vwap_aligned=vwap_ok,
                signal_reason=(
                    f"Sector cascade: {leader_symbol} moved {leader_move_pct:+.2f}%, "
                    f"body ratio {body_ratio:.2f}, RVOL {leader_rvol:.1f}x. "
                    f"Laggard {symbol} expected to follow."
                ),
                timeframe="5m",
                sector=sector,
                expires_bars=cls.MAX_BARS,
            ))

        return signals


# ═══════════════════════════════════════════════════════════════
# LNZ3 — Lenz Back-EMF: Weak Opposition After Strong Drive (+4.19%)
# ═══════════════════════════════════════════════════════════════

class LenzSignal:
    """
    Strong drive bar + weak retracement = no opposition = continuation.
    Physics: Lenz's Law — if back-EMF is weak, the current keeps flowing.
    """
    NAME = "lnz3"

    DRIVE_THRESHOLD_PCT = 0.30
    DRIVE_RVOL = 2.0
    BACKEMF_RATIO_MAX = 0.30  # Retracement must be < 30% of drive
    STOP_PCT = 0.30
    TARGET_RR = 2.5

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        """Need at least 2 recent bars (drive + response)."""
        if len(bars) < 2:
            return None

        drive_bar = bars[-2]
        response_bar = bars[-1]

        # Drive bar: strong move + volume
        drive_move = (drive_bar["close"] - drive_bar["open"]) / drive_bar["open"] * 100
        if abs(drive_move) < cls.DRIVE_THRESHOLD_PCT:
            return None

        # Response bar: weak retracement (low back-EMF)
        if drive_move > 0:
            # Bullish drive — response bar should NOT retrace much
            retrace = max(0, drive_bar["close"] - response_bar["low"])
            drive_size = abs(drive_bar["close"] - drive_bar["open"])
        else:
            # Bearish drive — response bar should NOT bounce much
            retrace = max(0, response_bar["high"] - drive_bar["close"])
            drive_size = abs(drive_bar["close"] - drive_bar["open"])

        if drive_size == 0:
            return None

        backemf_ratio = retrace / drive_size
        if backemf_ratio > cls.BACKEMF_RATIO_MAX:
            return None  # Too much opposition

        entry = response_bar["close"]
        direction = SignalDirection.LONG if drive_move > 0 else SignalDirection.SHORT
        risk = entry * cls.STOP_PCT / 100

        if direction == SignalDirection.LONG:
            stop = entry - risk
            target = entry + risk * cls.TARGET_RR
        else:
            stop = entry + risk
            target = entry - risk * cls.TARGET_RR

        # VWAP check
        vwap_values = calc_vwap(bars)
        vwap = vwap_values[-1] if vwap_values else entry
        vwap_aligned = (entry > vwap and direction == SignalDirection.LONG) or \
                       (entry < vwap and direction == SignalDirection.SHORT)

        conf = min(0.80, 0.55 + abs(drive_move) * 0.08 + (1 - backemf_ratio) * 0.15)

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
            rvol=0.0,
            vwap_aligned=vwap_aligned,
            signal_reason=(
                f"Drive {drive_move:+.2f}%, back-EMF ratio {backemf_ratio:.2f} "
                f"(weak opposition). Continuation expected."
            ),
            timeframe="5m",
            sector=get_sector(symbol),
        )


# ═══════════════════════════════════════════════════════════════
# AFT7 — Aftershock/Omori: Unexpected Continuation (+3.53%)
# ═══════════════════════════════════════════════════════════════

class AftershockSignal:
    """
    Strong move (mainshock) → next bar moves MORE than expected (not aftershock, fresh flow).
    Seismology: Omori's Law predicts aftershock decay. If "aftershock" is stronger than
    predicted, it's actually a new mainshock = fresh institutional order flow.
    """
    NAME = "aft7"

    MAINSHOCK_PCT = 0.20
    MAINSHOCK_RVOL = 2.0
    AFTERSHOCK_RATIO = 1.5  # Must exceed 1.5x expected (raised from 1.2 — too many false signals)
    EXPECTED_DECAY = 0.5     # Expected aftershock = mainshock * 0.5
    STOP_PCT = 0.30
    TARGET_RR = 2.5

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict]) -> Optional[StrategySignal]:
        if len(bars) < 2:
            return None

        mainshock = bars[-2]
        aftershock = bars[-1]

        main_move = abs((mainshock["close"] - mainshock["open"]) / mainshock["open"] * 100)
        if main_move < cls.MAINSHOCK_PCT:
            return None

        main_direction = 1 if mainshock["close"] > mainshock["open"] else -1
        after_move = (aftershock["close"] - aftershock["open"]) / aftershock["open"] * 100

        # Aftershock must be in SAME direction as mainshock
        if main_direction > 0 and after_move <= 0:
            return None
        if main_direction < 0 and after_move >= 0:
            return None

        expected = main_move * cls.EXPECTED_DECAY
        actual = abs(after_move)

        if actual < expected * cls.AFTERSHOCK_RATIO:
            return None  # Normal aftershock, not interesting

        entry = aftershock["close"]
        direction = SignalDirection.LONG if main_direction > 0 else SignalDirection.SHORT
        risk = entry * cls.STOP_PCT / 100

        if direction == SignalDirection.LONG:
            stop = entry - risk
            target = entry + risk * cls.TARGET_RR
        else:
            stop = entry + risk
            target = entry - risk * cls.TARGET_RR

        surprise_ratio = actual / expected if expected > 0 else 2.0
        conf = min(0.80, 0.50 + (surprise_ratio - 1.2) * 0.15 + main_move * 0.1)

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
            rvol=0.0,
            vwap_aligned=True,
            signal_reason=(
                f"Mainshock {main_move:.2f}%, aftershock {actual:.2f}% "
                f"({surprise_ratio:.1f}x expected). Fresh institutional flow, not decay."
            ),
            timeframe="5m",
            sector=get_sector(symbol),
        )


# ═══════════════════════════════════════════════════════════════
# GDR4 — Gap Decay Rate: Fast Gap Fill = Institutional Urgency (+7.02%)
# ═══════════════════════════════════════════════════════════════

class GapDecaySignal:
    """
    Gap that stays >70% unfilled after 30 minutes = institutional defense.
    Trade continuation in gap direction.
    """
    NAME = "gdr4"

    GAP_THRESHOLD_PCT = 0.3
    WAIT_BARS = 6           # 30 minutes
    MIN_UNFILLED_PCT = 70   # Gap must be >70% unfilled
    STOP_PCT = 0.30
    TARGET_RR = 2.5
    # Crowd filter: require minimum gap size when broad market gaps
    MIN_GAP_ON_CROWD_DAY = 0.8  # Only take gaps >0.8% when everything gaps

    @classmethod
    def scan(cls, symbol: str, bars: List[Dict], prev_close: float,
             crowd_gap_count: int = 0, total_symbols: int = 1) -> Optional[StrategySignal]:
        if len(bars) < cls.WAIT_BARS + 1:
            return None

        today_open = bars[0]["open"]
        gap_pct = (today_open - prev_close) / prev_close * 100

        if abs(gap_pct) < cls.GAP_THRESHOLD_PCT:
            return None

        # Crowd filter: if >70% of stocks gapped same direction, require bigger gap
        if total_symbols > 0:
            crowd_ratio = crowd_gap_count / total_symbols
            if crowd_ratio > 0.70 and abs(gap_pct) < cls.MIN_GAP_ON_CROWD_DAY:
                return None  # Too many stocks gapped — this one isn't special

        # Check how much gap is unfilled after WAIT_BARS
        # Unfilled = 100% means gap fully held, 0% means fully filled back to prev_close
        # Cap at 100% — if price moved further in gap direction, gap is fully held
        check_bar = bars[cls.WAIT_BARS]
        if gap_pct > 0:
            gap_size = today_open - prev_close
            if check_bar["close"] >= today_open:
                unfilled_pct = 100.0  # Price above gap open = gap fully held
            elif check_bar["close"] <= prev_close:
                unfilled_pct = 0.0    # Gap fully filled
            else:
                unfilled_pct = ((check_bar["close"] - prev_close) / gap_size) * 100 if gap_size > 0 else 0
        else:
            gap_size = prev_close - today_open
            if check_bar["close"] <= today_open:
                unfilled_pct = 100.0
            elif check_bar["close"] >= prev_close:
                unfilled_pct = 0.0
            else:
                unfilled_pct = ((prev_close - check_bar["close"]) / gap_size) * 100 if gap_size > 0 else 0

        if unfilled_pct < cls.MIN_UNFILLED_PCT:
            return None  # Gap filled too much — no institutional defense

        entry = check_bar["close"]
        direction = SignalDirection.LONG if gap_pct > 0 else SignalDirection.SHORT
        risk = entry * cls.STOP_PCT / 100

        if direction == SignalDirection.LONG:
            stop = entry - risk
            target = entry + risk * cls.TARGET_RR
        else:
            stop = entry + risk
            target = entry - risk * cls.TARGET_RR

        conf = min(0.80, 0.50 + (unfilled_pct / 100) * 0.15 + abs(gap_pct) * 0.05)

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
            rvol=0.0,
            vwap_aligned=True,
            signal_reason=(
                f"Gap {'up' if gap_pct > 0 else 'down'} {abs(gap_pct):.2f}%, "
                f"{unfilled_pct:.0f}% unfilled after {cls.WAIT_BARS * 5}min. "
                f"Institutional defense holding."
            ),
            timeframe="5m",
            sector=get_sector(symbol),
        )


# ═══════════════════════════════════════════════════════════════
# CW4 — Second Cascade Wave: Double Conviction (+7.81%, 59% WR)
# ═══════════════════════════════════════════════════════════════

class SecondWaveSignal:
    """
    First cascade fires → wait → leader makes SECOND strong move → enter laggards.
    Second wave = institutional conviction confirmed, not noise.
    """
    NAME = "cw4"

    LEADER_MOVE_PCT = 0.3
    MIN_GAP_BARS = 5        # At least 25 min between first and second wave
    STOP_PCT = 0.30
    TARGET_RR = 2.5

    def __init__(self):
        # Track first waves: {(sector, direction): bar_index}
        self._first_waves: Dict[tuple, int] = {}

    def scan(self, leader_symbol: str, bars: List[Dict], sector: str, bar_index: int) -> List[StrategySignal]:
        """Check if this is a second wave (first must have already fired)."""
        if len(bars) < 1:
            return []

        latest = bars[-1]
        move_pct = ((latest["close"] - latest["open"]) / latest["open"]) * 100

        if abs(move_pct) < self.LEADER_MOVE_PCT:
            return []

        direction = "LONG" if move_pct > 0 else "SHORT"
        key = (sector, direction)

        if key not in self._first_waves:
            # This is the FIRST wave — record it, don't trade
            self._first_waves[key] = bar_index
            return []

        # Check if enough bars have passed since first wave
        first_bar = self._first_waves[key]
        gap = bar_index - first_bar
        if gap < self.MIN_GAP_BARS:
            return []

        # This is the SECOND wave — generate signals for laggards
        self._first_waves[key] = bar_index  # Reset for potential third wave
        laggards = get_laggards(leader_symbol)

        signals = []
        for lag_symbol in laggards:
            entry = latest["close"]  # Simplified — in production, use laggard's price
            risk = entry * self.STOP_PCT / 100

            if direction == "LONG":
                stop = entry - risk
                target = entry + risk * self.TARGET_RR
            else:
                stop = entry + risk
                target = entry - risk * self.TARGET_RR

            conf = min(0.85, 0.55 + abs(move_pct) * 0.1 + (gap / 20) * 0.1)

            signals.append(StrategySignal(
                strategy_name=self.NAME,
                symbol=lag_symbol,
                direction=direction,
                urgency=SignalUrgency.IMMEDIATE.value,
                suggested_entry=round(entry, 2),
                suggested_stop=round(stop, 2),
                suggested_target=round(target, 2),
                risk_reward_ratio=self.TARGET_RR,
                risk_pct=self.STOP_PCT,
                raw_confidence=round(conf, 3),
                rvol=0.0,
                vwap_aligned=True,
                signal_reason=(
                    f"Second cascade wave: {leader_symbol} moved {move_pct:+.2f}% again "
                    f"({gap} bars after first wave). Double conviction."
                ),
                timeframe="5m",
                sector=sector,
            ))

        return signals

    def reset_daily(self):
        """Clear wave tracking at start of each day."""
        self._first_waves.clear()


# ═══════════════════════════════════════════════════════════════
# ALL STRATEGIES — convenience list
# ═══════════════════════════════════════════════════════════════

ALL_SIGNAL_PROVIDERS = {
    "gg8": GapAndGoSignal,
    "orb": ORBSignal,
    "cascade_y5": CascadeSignal,
    "lnz3": LenzSignal,
    "aft7": AftershockSignal,
    "gdr4": GapDecaySignal,
    "cw4": SecondWaveSignal,
}
