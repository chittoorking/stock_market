"""
Coded MoE — all expert knowledge encoded as scoring functions.
Zero LLM calls. Deterministic. Instant. Tunable.

Same 6 domain experts as the LLM version, but as pure math:
1. Volume Expert    → RVOL, volume sustain, entry bar spike
2. Sector Expert    → sector alignment, breadth, leader flow
3. Price Expert     → candle quality, gap, VWAP, noise ratio
4. Momentum Expert  → morning alignment, consecutive bars, EMA
5. Market Expert    → broad breadth, trend quality
6. Macro Expert     → India VIX, US overnight, Asian markets

Each expert scores 0-10. Aggregator picks top 3 with score >= threshold.
"""
from __future__ import annotations

import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from pathlib import Path

from app.signals.base import get_sector, SECTOR_MAP
from app.agents.data_providers import SectorAnalyzer, VolumeProfiler, PriceStructure


# ═══ MACRO DATA LOADER ═══
_macro_cache: Dict = {}

def _load_macro(date: str) -> Dict:
    if not _macro_cache:
        macro_file = Path('data/macro/daily_macro.json')
        if macro_file.exists():
            with open(macro_file) as f:
                for entry in json.load(f):
                    _macro_cache[entry['date']] = entry
    return _macro_cache.get(date, {})


# ═══ DELIVERY DATA LOADER ═══
_delivery_cache: Dict = {}

def _load_delivery(symbol: str, date: str) -> Dict:
    import csv
    if symbol not in _delivery_cache:
        f = Path(f'data/delivery/{symbol}_delivery.csv')
        if f.exists():
            with open(f) as fh:
                _delivery_cache[symbol] = {r['date']: r for r in csv.DictReader(fh)}
        else:
            _delivery_cache[symbol] = {}
    # Get most recent before date
    data = _delivery_cache.get(symbol, {})
    recent = sorted(d for d in data if d < date)
    if recent:
        r = data[recent[-1]]
        try:
            return {'delivery_pct': float(r.get('delivery_pct', 0) or 0), 'date': recent[-1]}
        except (ValueError, TypeError):
            return {}
    return {}


# ═══════════════════════════════════════════════════════════════
# EXPERT SCORING FUNCTIONS — each returns 0-10
# ═══════════════════════════════════════════════════════════════

def score_volume(symbol: str, bars: List[Dict], all_data: Dict, date: str, bar_idx: int) -> float:
    """Volume Expert: RVOL, sustainability, entry bar quality, delivery %."""
    score = 5.0  # Neutral start

    if not bars or len(bars) <= bar_idx:
        return 3.0

    # RVOL
    prev_bars = [b for b in all_data.get(symbol, []) if b['timestamp'][:10] < date]
    prev_dates = sorted(set(b['timestamp'][:10] for b in prev_bars))[-5:]
    prev_avg_vols = []
    for pd in prev_dates:
        pd_bars = [b for b in prev_bars if b['timestamp'][:10] == pd]
        if len(pd_bars) > bar_idx:
            prev_avg_vols.append(sum(b['volume'] for b in pd_bars[:bar_idx + 1]))
    today_vol = sum(b['volume'] for b in bars[:bar_idx + 1])
    if prev_avg_vols:
        avg_prev = sum(prev_avg_vols) / len(prev_avg_vols)
        rvol = today_vol / avg_prev if avg_prev > 0 else 1
        if rvol > 2.0: score += 2.0
        elif rvol > 1.5: score += 1.5
        elif rvol > 1.2: score += 0.5
        elif rvol < 0.8: score -= 1.5
        elif rvol < 1.0: score -= 0.5

    # Volume sustainability
    if bar_idx >= 5:
        v1 = sum(bars[i]['volume'] for i in range(3))
        v2 = sum(bars[i]['volume'] for i in range(3, min(6, bar_idx + 1)))
        ratio = v2 / v1 if v1 > 0 else 0
        if ratio > 0.8: score += 1.0
        elif ratio < 0.35: score -= 1.5
        elif ratio < 0.5: score -= 0.5

    # Entry bar volume spike (block deal detection)
    if bar_idx >= 1:
        prev_v = bars[bar_idx - 1]['volume']
        curr_v = bars[bar_idx]['volume']
        if prev_v > 0:
            spike = (curr_v - prev_v) / prev_v * 100
            if spike > 100: score -= 2.0  # Block deal
            elif spike > 50: score -= 1.0
            elif spike < -30: score += 0.5  # Organic flow

    # Delivery %
    del_data = _load_delivery(symbol, date)
    if del_data:
        dpct = del_data.get('delivery_pct', 0)
        if dpct > 55: score += 1.0  # Institutional
        elif dpct < 25: score -= 1.0  # Speculative

    return max(0, min(10, score))


def score_sector(symbol: str, all_data: Dict, date: str, bar_idx: int, direction: str) -> float:
    """Sector Expert: alignment, breadth, leader/laggard flow."""
    score = 5.0
    sector = get_sector(symbol)
    sec_info = SECTOR_MAP.get(sector, {})

    if not sec_info:
        return 4.0  # Unknown sector penalty

    sector_data = SectorAnalyzer.compute(all_data, date, bar_idx)
    sec = sector_data.get(sector, {})
    if not sec:
        return 4.0

    leader_chg = sec.get('leader_change_pct', 0)
    strength = sec.get('strength', 0)

    # Alignment
    aligned = (direction == "LONG" and leader_chg > 0.3) or (direction == "SHORT" and leader_chg < -0.3)
    against = (direction == "LONG" and leader_chg < -0.3) or (direction == "SHORT" and leader_chg > 0.3)

    if aligned and abs(leader_chg) > 1.0: score += 2.5
    elif aligned: score += 1.5
    elif against: score -= 2.0

    # Breadth
    if strength >= 0.75: score += 1.0
    elif strength <= 0.25: score -= 1.5

    return max(0, min(10, score))


def score_price(symbol: str, bars: List[Dict], bar_idx: int, prev_close: float, direction: str) -> float:
    """Price Expert: candle quality, gap, VWAP, noise ratio."""
    score = 5.0

    if not bars or len(bars) <= bar_idx:
        return 3.0

    # Opening bar quality
    bar0 = bars[0]
    bar0_body = abs(bar0['close'] - bar0['open'])
    bar0_range = bar0['high'] - bar0['low']
    bar0_ratio = bar0_body / bar0_range * 100 if bar0_range > 0 else 0
    bar0_move = (bar0['close'] - bar0['open']) / bar0['open'] * 100

    if bar0_ratio > 70: score += 1.0
    elif bar0_ratio < 25: score -= 1.0

    # Entry bar quality
    eb = bars[bar_idx]
    eb_body = abs(eb['close'] - eb['open'])
    eb_range = eb['high'] - eb['low']
    eb_ratio = eb_body / eb_range * 100 if eb_range > 0 else 0
    if eb_ratio < 20: score -= 1.5
    elif eb_ratio > 60: score += 0.5

    # Gap alignment
    if prev_close > 0:
        gap = (bars[0]['open'] - prev_close) / prev_close * 100
        gap_aligned = (direction == "LONG" and gap > 0.3) or (direction == "SHORT" and gap < -0.3)
        gap_against = (direction == "LONG" and gap < -0.5) or (direction == "SHORT" and gap > 0.5)
        if gap_aligned: score += 1.0
        if gap_against: score -= 1.5

    # VWAP position
    tp_vol = sum((b['high']+b['low']+b['close'])/3 * b['volume'] for b in bars[:bar_idx+1])
    cum_vol = sum(b['volume'] for b in bars[:bar_idx+1])
    vwap = tp_vol / cum_vol if cum_vol > 0 else bars[bar_idx]['close']
    price = bars[bar_idx]['close']
    if direction == "LONG" and price > vwap: score += 0.5
    elif direction == "SHORT" and price < vwap: score += 0.5
    elif direction == "LONG" and price < vwap * 0.997: score -= 0.5
    elif direction == "SHORT" and price > vwap * 1.003: score -= 0.5

    # Noise ratio
    if bar_idx >= 3:
        ranges = [bars[i]['high'] - bars[i]['low'] for i in range(max(0, bar_idx-5), bar_idx+1)]
        moves = [abs(bars[i]['close'] - bars[i-1]['close']) for i in range(max(1, bar_idx-4), bar_idx+1)]
        noise = sum(ranges) / sum(moves) if moves and sum(moves) > 0 else 5
        if noise > 3.5: score -= 1.5
        elif noise < 1.8: score += 1.0

    return max(0, min(10, score))


def score_momentum(symbol: str, bars: List[Dict], bar_idx: int, direction: str) -> float:
    """Momentum Expert: morning alignment, consecutive bars, EMA trend."""
    score = 5.0

    if not bars or len(bars) <= bar_idx:
        return 3.0

    # Morning move alignment (the #1 predictor)
    morning_move = (bars[bar_idx]['close'] - bars[0]['open']) / bars[0]['open'] * 100
    morning_aligned = (direction == "LONG" and morning_move > 0) or (direction == "SHORT" and morning_move < 0)
    if morning_aligned and abs(morning_move) > 0.5: score += 2.0
    elif morning_aligned: score += 1.0
    elif not morning_aligned and abs(morning_move) > 0.3: score -= 2.0

    # Opening bar strength + direction
    bar0_move = (bars[0]['close'] - bars[0]['open']) / bars[0]['open'] * 100
    bar0_aligned = (direction == "LONG" and bar0_move > 0.5) or (direction == "SHORT" and bar0_move < -0.5)
    if bar0_aligned and abs(bar0_move) > 0.8: score += 1.0

    # Consecutive direction bars
    consec = 0
    for i in range(bar_idx, 0, -1):
        bar_dir = bars[i]['close'] > bars[i]['open']
        if (direction == "LONG" and bar_dir) or (direction == "SHORT" and not bar_dir):
            consec += 1
        else:
            break
    if consec >= 3 and consec <= 4: score += 1.0
    elif consec >= 5: score -= 0.5  # Exhaustion

    # EMA trend
    closes = [b['close'] for b in bars[:bar_idx + 1]]
    if len(closes) >= 9:
        ema9 = sum(closes[-9:]) / 9
        ema_aligned = (direction == "LONG" and closes[-1] > ema9) or (direction == "SHORT" and closes[-1] < ema9)
        if ema_aligned: score += 0.5

    # Pre-entry momentum
    if bar_idx >= 5:
        with_count = sum(1 for i in range(max(0, bar_idx-5), bar_idx+1)
                        if (direction == "LONG" and bars[i]['close'] > bars[i]['open']) or
                           (direction == "SHORT" and bars[i]['close'] < bars[i]['open']))
        if with_count >= 4: score += 0.5
        elif with_count <= 2: score -= 1.0

    return max(0, min(10, score))


def score_market(all_data: Dict, date: str, bar_idx: int, direction: str) -> float:
    """Market Regime Expert: breadth, trend quality, gap status."""
    score = 5.0
    up = 0; down = 0; total = 0

    for sym, bars_all in all_data.items():
        if sym in ('NIFTY_50', 'NIFTY_BANK'): continue
        db = [b for b in bars_all if b['timestamp'][:10] == date]
        if not db or len(db) <= bar_idx: continue
        total += 1
        move = (db[bar_idx]['close'] - db[0]['open']) / db[0]['open'] * 100
        if move > 0.15: up += 1
        elif move < -0.15: down += 1

    if total == 0: return 5.0

    up_pct = up / total * 100
    down_pct = down / total * 100

    # Breadth alignment with direction
    if direction == "LONG" and up_pct > 65: score += 2.0
    elif direction == "LONG" and up_pct > 55: score += 1.0
    elif direction == "LONG" and up_pct < 35: score -= 2.0
    elif direction == "SHORT" and down_pct > 65: score += 2.0
    elif direction == "SHORT" and down_pct > 55: score += 1.0
    elif direction == "SHORT" and down_pct < 35: score -= 2.0

    # Choppy market penalty
    flat = total - up - down
    if flat > total * 0.4: score -= 1.0

    return max(0, min(10, score))


def score_macro(date: str, direction: str) -> float:
    """Macro Sentiment Expert: VIX, US overnight, Asian markets."""
    score = 5.0
    ctx = _load_macro(date)
    if not ctx: return 5.0

    vix = ctx.get('india_vix', 0)
    sp = ctx.get('sp500_overnight', 0)
    nq = ctx.get('nasdaq_overnight', 0)
    nifty_prev = ctx.get('nifty_prev_return', 0)

    # VIX regime
    if vix > 22: score -= 1.5  # High fear
    elif vix > 18: score -= 0.5
    elif vix < 13: score += 1.0  # Calm trending

    # US overnight alignment
    us_avg = (sp + nq) / 2
    if direction == "LONG" and us_avg > 0.5: score += 1.5
    elif direction == "LONG" and us_avg < -0.5: score -= 1.5
    elif direction == "SHORT" and us_avg < -0.5: score += 1.5
    elif direction == "SHORT" and us_avg > 0.5: score -= 1.5

    # Nifty previous day (continuation vs exhaustion)
    if direction == "LONG" and 0 < nifty_prev < 1.0: score += 0.5  # Mild bullish continuation
    elif direction == "LONG" and nifty_prev > 1.5: score -= 0.5  # Exhaustion risk
    elif direction == "SHORT" and -1.0 < nifty_prev < 0: score += 0.5
    elif direction == "SHORT" and nifty_prev < -1.5: score -= 0.5

    return max(0, min(10, score))


# ═══════════════════════════════════════════════════════════════
# CODED AGGREGATOR — weighted combination, deterministic
# ═══════════════════════════════════════════════════════════════

@dataclass
class SignalScore:
    symbol: str = ""
    direction: str = ""
    strategy: str = ""
    volume: float = 0
    sector: float = 0
    price: float = 0
    momentum: float = 0
    market: float = 0
    macro: float = 0
    total: float = 0
    reason: str = ""


def coded_moe_scan(
    signals: list,
    all_data: Dict,
    date: str,
    bar_idx: int,
    max_trades: int = 3,
    min_score: float = 35.0,
) -> List[Dict]:
    """
    Coded MoE — scores all signals, picks best.
    Zero LLM calls. Instant. Deterministic.
    """
    scored = []
    sectors_seen = set()

    for sig in signals:
        sym = sig.symbol
        direction = sig.direction
        sector = get_sector(sym)

        bars = [b for b in all_data.get(sym, []) if b['timestamp'][:10] == date]
        prev_bars = [b for b in all_data.get(sym, []) if b['timestamp'][:10] < date]
        prev_close = prev_bars[-1]['close'] if prev_bars else 0

        # Score each domain
        v = score_volume(sym, bars, all_data, date, bar_idx)
        s = score_sector(sym, all_data, date, bar_idx, direction)
        p = score_price(sym, bars, bar_idx, prev_close, direction)
        m = score_momentum(sym, bars, bar_idx, direction)
        mkt = score_market(all_data, date, bar_idx, direction)
        mac = score_macro(date, direction)

        # Weighted total (Volume + Momentum strongest, as discovered)
        total = v * 1.2 + s * 1.0 + p * 1.0 + m * 1.3 + mkt * 0.8 + mac * 0.7
        # Normalize to 0-60 range, threshold accordingly

        scored.append(SignalScore(
            symbol=sym, direction=direction, strategy=sig.strategy_name,
            volume=round(v, 1), sector=round(s, 1), price=round(p, 1),
            momentum=round(m, 1), market=round(mkt, 1), macro=round(mac, 1),
            total=round(total, 1),
            reason=f"V:{v:.0f} S:{s:.0f} P:{p:.0f} M:{m:.0f} Mkt:{mkt:.0f} Mac:{mac:.0f}",
        ))

    # Sort by total score, pick top N
    scored.sort(key=lambda x: -x.total)

    ranked = []
    for ss in scored:
        if ss.total < min_score: break
        if ss.symbol in [r['symbol'] for r in ranked]: continue  # No duplicate stocks
        sector = get_sector(ss.symbol)
        if sector in sectors_seen: continue  # One per sector
        sectors_seen.add(sector)

        ranked.append({
            'symbol': ss.symbol,
            'direction': ss.direction,
            'strategy': ss.strategy,
            'score': ss.total,
            'reason': ss.reason,
        })
        if len(ranked) >= max_trades: break

    return ranked
