"""ORB (Opening Range Breakout) — reusable breakout detection.

Used by: news_orb agent.
Reusable: takes broker + symbols, returns OR data + filter results.
No trade execution here — just data.
"""
import time
from datetime import datetime

import numpy as np

from trading.logger import get_logger, audit

log = get_logger('orb')

from trading.config import (ORB_MIN_REMAINING as MIN_REMAINING_PCT,
                           ORB_MAX_ENTRY_MOVE as MAX_ENTRY_MOVE_PCT,
                           ORB_BUFFER_PCT as CONFIRM_BUFFER_PCT)


def collect_range(broker, symbols: list[str], poll_seconds: int = 15) -> dict:
    """Collect opening range (9:15-9:20) using broker API. No Yahoo.
    Returns {sym: {high, low, open, prev_close}}.
    """
    data = {}
    for sym in symbols:
        data[sym] = {'high': 0, 'low': float('inf'), 'open': 0, 'prev_close': 0}

    # Fetch prev close from broker full quote
    try:
        from live.indmoney_client import get_full_quote
        quotes = get_full_quote(symbols)
        for sym in symbols:
            q = quotes.get(sym, {})
            pc = q.get('prev_close', 0)
            if pc > 0:
                data[sym]['prev_close'] = pc
                log.info(f'{sym} prev_close={pc:.1f}')
    except Exception as e:
        log.warning(f'Full quote failed: {e}')
        # Fallback: LTP as prev_close (after hours LTP = closing price)
        for sym in symbols:
            price = broker.ltp_safe(sym)
            if price > 0:
                data[sym]['prev_close'] = price

    # Wait until 9:20:05 (5 sec buffer for candle to form)
    end = datetime.now().replace(hour=9, minute=20, second=5, microsecond=0)
    log.info(f'Collecting OR for {len(symbols)} stocks...')
    while datetime.now() < end:
        time.sleep(5)

    # Fetch 5-min candle from broker API
    today = datetime.now().date()
    start_ts = int(datetime(today.year, today.month, today.day, 9, 15).timestamp() * 1000)
    end_ts = int(datetime(today.year, today.month, today.day, 9, 20).timestamp() * 1000)

    for sym in symbols:
        try:
            candles = broker.get_candles(sym, '5minute', start_ts, end_ts)
            if candles:
                c = candles[0]
                data[sym]['open'] = float(c.get('o', 0))
                data[sym]['high'] = float(c.get('h', 0))
                data[sym]['low'] = float(c.get('l', 0))
                r = (data[sym]['high'] - data[sym]['low']) / data[sym]['open'] * 100 if data[sym]['open'] > 0 else 0
                log.info(f'OR {sym}: H={data[sym]["high"]:.1f} L={data[sym]["low"]:.1f} R={r:.1f}% (candle)')
            else:
                price = broker.ltp_safe(sym)
                if price > 0:
                    data[sym]['open'] = price
                    data[sym]['high'] = price
                    data[sym]['low'] = price
                    log.warning(f'OR {sym}: candle failed, using LTP {price:.1f}')
        except Exception as e:
            log.warning(f'OR {sym}: candle error ({e}), using LTP')
            price = broker.ltp_safe(sym)
            if price > 0:
                data[sym]['open'] = price
                data[sym]['high'] = price
                data[sym]['low'] = price

    return data


def apply_filters(trades: list[dict], or_data: dict) -> list[dict]:
    """Apply remaining move filter using entry price (includes gap + 5min move).
    Returns list of trades that passed, with entry_price added.
    """
    passed = []
    for t in trades:
        sym = t.get('nse_symbol', t['symbol'])
        d = or_data.get(sym)
        if not d or d['open'] == 0:
            log.warning(f'{sym}: no OR data')
            continue

        pc = d.get('prev_close', 0)
        if pc <= 0:
            log.warning(f'{sym}: no prev_close')
            continue

        # Entry price with buffer
        if t['call'] == 'BUY':
            entry = d['high'] * (1 + CONFIRM_BUFFER_PCT / 100)
        else:
            entry = d['low'] * (1 - CONFIRM_BUFFER_PCT / 100)

        # Total move from prev close to entry (gap + 5min ORB move + buffer)
        if t['call'] == 'BUY':
            total_move = (entry - pc) / pc * 100
        else:
            total_move = (pc - entry) / pc * 100

        # Remaining = projected move - move already happened
        remaining = abs(t['projection']) - total_move

        if remaining < MIN_REMAINING_PCT:
            log.info(f'SKIP {sym}: total_move={total_move:.1f}% remaining={remaining:.1f}% < {MIN_REMAINING_PCT}%')
            audit('orb', 'SKIP_REMAINING', sym, proj=t['projection'],
                  total_move=round(total_move, 1), remaining=round(remaining, 1))
            continue

        # Entry move sanity check — don't enter if stock already moved > max
        if total_move > MAX_ENTRY_MOVE_PCT:
            log.info(f'SKIP {sym}: total_move={total_move:.1f}% > {MAX_ENTRY_MOVE_PCT}%')
            audit('orb', 'SKIP_ENTRY_MOVE', sym, total_move=round(total_move, 1))
            continue

        log.info(f'PASS {sym} {t["call"]}: entry={entry:.1f} moved={total_move:.1f}% rem={remaining:.1f}%')
        passed.append({**t, 'entry_price': round(entry, 2),
                       'or_high': d['high'], 'or_low': d['low']})

    log.info(f'Filters: {len(trades)} -> {len(passed)} passed')
    return passed


def get_atr_sl(sym: str, multiplier: float = 1.5, default: float = 3.0) -> float:
    """ATR-based stop loss percentage. Uses broker candles, no Yahoo."""
    try:
        from live.indmoney_client import get_historical_candles
        from datetime import timedelta
        now = datetime.now()
        end_ts = int(now.timestamp() * 1000)
        start_ts = int((now - timedelta(days=30)).timestamp() * 1000)
        candles = get_historical_candles(sym, '1day', start_ts, end_ts)
        if not candles or len(candles) < 5:
            return default
        h = np.array([float(c.get('h', 0)) for c in candles])
        l = np.array([float(c.get('l', 0)) for c in candles])
        c = np.array([float(c.get('c', 0)) for c in candles])
        tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
        atr = float(np.nanmean(tr[-14:]))
        if np.isnan(atr) or c[-2] == 0:
            return default
        atr_pct = atr / c[-2] * 100
        result = max(round(atr_pct * multiplier, 1), 1.0)
        return result if not np.isnan(result) else default
    except:
        return default


def monitor_fills(broker, pending: list[dict], deadline_hour: int = 14,
                  deadline_min: int = 45, poll_seconds: int = 10):
    """Monitor limit orders for fills via order book.
    Yields (order_info, fill_price) for each fill.
    Cancels unfilled at deadline.
    """
    remaining = list(pending)

    while remaining:
        now = datetime.now()
        if now.hour > deadline_hour or (now.hour == deadline_hour and now.minute >= deadline_min):
            log.info(f'Deadline: cancelling {len(remaining)} unfilled')
            for p in remaining:
                broker.cancel(p['order_id'])
                audit('orb', 'CANCEL_DEADLINE', p['sym'])
            for p in remaining:
                yield p, None
            return

        for p in remaining[:]:
            try:
                filled, price = broker.order_filled(p['order_id'])
                if filled:
                    log.info(f'FILLED: {p["sym"]} @ {price}')
                    remaining.remove(p)
                    yield p, price
            except Exception as e:
                log.warning(f'Fill check {p["sym"]}: {e}')

        if remaining:
            time.sleep(poll_seconds)
