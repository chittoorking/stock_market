"""
Multi-Timeframe Analysis — aggregate 5-min bars into higher timeframes.

Check if the setup is confirmed at every level:
- 5min (current): the signal itself
- 30min (6 bars): is the half-hour candle confirming?
- 1hr (12 bars): is the hourly trend aligned?
- Day (previous): was yesterday's bias with or against?

If ALL timeframes agree → strong multi-timeframe convergence.
If lower TF says yes but higher says no → setup may fail at the bigger picture level.

This is DATA for the LLM. Simple facts per timeframe.
"""
from __future__ import annotations

from typing import Dict, List, Optional


def aggregate_bars(bars: list, group_size: int) -> list:
    """Aggregate 5-min bars into larger timeframe candles."""
    candles = []
    for i in range(0, len(bars), group_size):
        group = bars[i:i + group_size]
        if not group:
            continue
        candles.append({
            'open': group[0]['open'],
            'high': max(b['high'] for b in group),
            'low': min(b['low'] for b in group),
            'close': group[-1]['close'],
            'volume': sum(b['volume'] for b in group),
            'timestamp': group[0]['timestamp'],
            'bars_count': len(group),
        })
    return candles


def analyze_timeframe(candles: list, direction: str) -> dict:
    """Analyze a set of candles for trend direction and strength."""
    if not candles or len(candles) < 2:
        return {"signal": 0, "detail": "insufficient data"}

    last = candles[-1]
    body = last['close'] - last['open']
    rng = last['high'] - last['low']
    body_pct = abs(body) / rng * 100 if rng > 0 else 0

    # Is the last candle WITH the direction?
    candle_with = (direction == 'LONG' and body > 0) or (direction == 'SHORT' and body < 0)

    # Is the trend (last 3 candles) WITH the direction?
    recent = candles[-min(3, len(candles)):]
    trend_move = (recent[-1]['close'] - recent[0]['open']) / recent[0]['open'] * 100
    trend_with = (direction == 'LONG' and trend_move > 0) or (direction == 'SHORT' and trend_move < 0)

    # Volume trend
    if len(candles) >= 2:
        vol_recent = candles[-1]['volume']
        vol_prev = candles[-2]['volume']
        vol_growing = vol_recent > vol_prev * 0.8
    else:
        vol_growing = True

    # Score
    if candle_with and trend_with and vol_growing:
        signal = 1
        detail = f"confirms {direction} (body={body_pct:.0f}%, trend={trend_move:+.2f}%, vol OK)"
    elif candle_with and trend_with:
        signal = 1
        detail = f"confirms {direction} (trend={trend_move:+.2f}%, vol fading)"
    elif not candle_with and not trend_with:
        signal = -1
        detail = f"AGAINST {direction} (body={body_pct:.0f}%, trend={trend_move:+.2f}%)"
    else:
        signal = 0
        detail = f"mixed (candle={'with' if candle_with else 'against'}, trend={trend_move:+.2f}%)"

    return {"signal": signal, "detail": detail}


def multi_timeframe_check(
    all_data: Dict[str, list],
    symbol: str,
    direction: str,
    date: str,
    entry_bar: int,
) -> str:
    """
    Check the setup across multiple timeframes.
    Returns simple facts for the Verifier agent.
    """
    bars_today = [b for b in all_data.get(symbol, []) if b['timestamp'][:10] == date]
    prev_bars = [b for b in all_data.get(symbol, []) if b['timestamp'][:10] < date]

    if not bars_today or len(bars_today) <= entry_bar:
        return f"MTF [{symbol}]: no data"

    bars_up_to_entry = bars_today[:entry_bar + 1]

    results = []

    # 5min — current bar (already in signals, but summarize)
    last_5m = bars_up_to_entry[-1]
    body_5m = last_5m['close'] - last_5m['open']
    with_5m = (direction == 'LONG' and body_5m > 0) or (direction == 'SHORT' and body_5m < 0)
    results.append(f"5min: {'confirms' if with_5m else 'against'}")

    # 30min — aggregate into 30-min candles (6 bars each)
    candles_30m = aggregate_bars(bars_up_to_entry, 6)
    if candles_30m:
        tf30 = analyze_timeframe(candles_30m, direction)
        results.append(f"30min: {tf30['detail']}")

    # 1hr — aggregate into 1-hour candles (12 bars each)
    candles_1h = aggregate_bars(bars_up_to_entry, 12)
    if candles_1h:
        tf1h = analyze_timeframe(candles_1h, direction)
        results.append(f"1hr: {tf1h['detail']}")

    # Day level — previous day's structure
    if prev_bars and len(prev_bars) >= 5:
        # Get last full day
        prev_dates = sorted(set(b['timestamp'][:10] for b in prev_bars))
        if prev_dates:
            last_day = prev_dates[-1]
            last_day_bars = [b for b in prev_bars if b['timestamp'][:10] == last_day]
            if last_day_bars:
                day_move = (last_day_bars[-1]['close'] - last_day_bars[0]['open']) / last_day_bars[0]['open'] * 100
                day_with = (direction == 'LONG' and day_move > 0) or (direction == 'SHORT' and day_move < 0)
                day_body = abs(last_day_bars[-1]['close'] - last_day_bars[0]['open'])
                day_range = max(b['high'] for b in last_day_bars) - min(b['low'] for b in last_day_bars)
                day_body_pct = day_body / day_range * 100 if day_range > 0 else 0
                results.append(f"prev day: {'confirms' if day_with else 'AGAINST'} {direction} "
                              f"(moved {day_move:+.2f}%, body={day_body_pct:.0f}%)")

    # Count alignment
    confirms = sum(1 for r in results if 'confirms' in r.lower())
    against = sum(1 for r in results if 'against' in r.lower())
    total = len(results)

    if confirms >= 3:
        verdict = f"MTF ALIGNED ({confirms}/{total} timeframes confirm)"
    elif against >= 2:
        verdict = f"MTF DIVERGENT ({against}/{total} timeframes against)"
    else:
        verdict = f"MTF MIXED ({confirms}/{total} confirm, {against}/{total} against)"

    return f"MTF [{symbol}]: {verdict} | " + " | ".join(results)


def write_mtf_to_graph(
    all_data: Dict[str, list],
    symbol: str,
    direction: str,
    date: str,
    entry_bar: int,
    trade_graph,
):
    """Write multi-timeframe facts to the trade dev graph as events."""
    bars_today = [b for b in all_data.get(symbol, []) if b['timestamp'][:10] == date]
    prev_bars = [b for b in all_data.get(symbol, []) if b['timestamp'][:10] < date]

    if not bars_today or len(bars_today) <= entry_bar:
        return

    bars_up_to_entry = bars_today[:entry_bar + 1]

    # 30min
    candles_30m = aggregate_bars(bars_up_to_entry, 6)
    if candles_30m:
        tf30 = analyze_timeframe(candles_30m, direction)
        if tf30['signal'] != 0:
            trade_graph.write_signal_event(symbol, entry_bar,
                f"30min timeframe {'confirms' if tf30['signal'] > 0 else 'AGAINST'} {direction}")

    # 1hr
    candles_1h = aggregate_bars(bars_up_to_entry, 12)
    if candles_1h:
        tf1h = analyze_timeframe(candles_1h, direction)
        if tf1h['signal'] != 0:
            trade_graph.write_signal_event(symbol, entry_bar,
                f"1hr timeframe {'confirms' if tf1h['signal'] > 0 else 'AGAINST'} {direction}")

    # Previous day
    if prev_bars:
        prev_dates = sorted(set(b['timestamp'][:10] for b in prev_bars))
        if prev_dates:
            last_day = prev_dates[-1]
            last_day_bars = [b for b in prev_bars if b['timestamp'][:10] == last_day]
            if last_day_bars:
                day_move = (last_day_bars[-1]['close'] - last_day_bars[0]['open']) / last_day_bars[0]['open'] * 100
                day_with = (direction == 'LONG' and day_move > 0) or (direction == 'SHORT' and day_move < 0)
                trade_graph.write_signal_event(symbol, entry_bar,
                    f"previous day {'confirms' if day_with else 'AGAINST'} {direction} (moved {day_move:+.2f}%)")
