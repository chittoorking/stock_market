"""
EXHAUSTIVE v2 — The Real Deep Dive.
Previous scan only tested momentum-based entries at fixed scan bars.
This tests FUNDAMENTALLY DIFFERENT strategy types:

A. GAP FILL — gaps fill ~70% of the time, trade the fill
B. VWAP RECLAIM — price crosses VWAP with volume confirmation
C. OPENING RANGE BREAKOUT — first N bars define range, trade breakout
D. MEAN REVERSION — RSI/Stoch oversold+bounce
E. RELATIVE STRENGTH — strongest stock vs Nifty50
F. PREVIOUS DAY LEVEL — S/R at prev day high/low/close
G. EVERY SCAN BAR — bar 1 through 20, not just 3/6/10/15/20
H. TIME-WEIGHTED — different exits for different entry times
I. INTRADAY RANGE — narrow range → breakout

For each: compute entry, stop, target, simulate, record.
"""
import sys; sys.path.insert(0, '.')
import csv, json, math, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from app.agents.volatility import VolatilityAgent

# ─── DATA LOADING ───
data_dir = Path('data/5min')
all_data = {}
date_bars = defaultdict(dict)

print('Loading data...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    bars = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
             'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]
    all_data[sym] = bars
    by_date = defaultdict(list)
    for b in bars:
        by_date[b['timestamp'][:10]].append(b)
    for d, bs in by_date.items():
        date_bars[d][sym] = bs

all_dates = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))

# Pre-compute
prev_close_map = {}
prev_day_data = {}  # (date, sym) -> {high, low, close, open}
nifty_bars = {}  # date -> bars for NIFTY50 or proxy

for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    for i, d in enumerate(dates_for_sym):
        if i > 0:
            prev_d = dates_for_sym[i-1]
            prev_d_bars = date_bars[prev_d].get(sym, [])
            if prev_d_bars:
                prev_close_map[(d, sym)] = prev_d_bars[-1]['close']
                prev_day_data[(d, sym)] = {
                    'high': max(b['high'] for b in prev_d_bars),
                    'low': min(b['low'] for b in prev_d_bars),
                    'close': prev_d_bars[-1]['close'],
                    'open': prev_d_bars[0]['open'],
                }

# Macro
macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

vol_agent = VolatilityAgent()
EXIT_BAR = 69  # 3PM
print(f'{len(all_data)} stocks, {len(all_dates)} days loaded.\n')


def simulate(db, entry_bar, entry_price, direction, stop, target, max_exit_bar=69):
    """Simulate trade with stop/target/time exit"""
    for j in range(entry_bar + 1, min(len(db), max_exit_bar + 1)):
        if direction == 'LONG':
            if db[j]['low'] <= stop: return stop, 'stop'
            if db[j]['high'] >= target: return target, 'target'
        else:
            if db[j]['high'] >= stop: return stop, 'stop'
            if db[j]['low'] <= target: return target, 'target'
    exit_p = db[min(max_exit_bar, len(db)-1)]['close']
    return exit_p, 'time'


def pnl_calc(entry, exit_p, direction):
    return (exit_p - entry)/entry*100 if direction == 'LONG' else (entry - exit_p)/entry*100


# ═══════════════════════════════════════════════════════════════
# STRATEGY A: GAP FILL
# Gaps fill ~70% of the time. If gap up, short to fill. If gap down, long to fill.
# ═══════════════════════════════════════════════════════════════
print('='*80)
print('STRATEGY A: GAP FILL')
print('='*80, flush=True)

gap_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 20: continue

        gap_pct = (db[0]['open'] - pc) / pc * 100

        # Only trade significant gaps
        for min_gap in [0.3, 0.5, 0.8, 1.0, 1.5]:
            if abs(gap_pct) < min_gap: continue

            # Gap up → short to fill, Gap down → long to fill
            direction = 'SHORT' if gap_pct > 0 else 'LONG'

            # Enter after first bar confirms direction (bar 1 or 2)
            for entry_bar in [1, 2, 3]:
                if len(db) <= entry_bar: continue

                # Confirmation: first bar moves toward fill
                if direction == 'LONG' and db[entry_bar]['close'] <= db[entry_bar]['open']: continue  # No bounce
                if direction == 'SHORT' and db[entry_bar]['close'] >= db[entry_bar]['open']: continue  # No drop

                entry = db[entry_bar]['close']
                # Target = previous close (gap fill)
                target = pc
                # Stop = 50% of gap extended
                gap_size = abs(db[0]['open'] - pc)
                stop = entry + gap_size * 0.5 if direction == 'SHORT' else entry - gap_size * 0.5

                for exit_bar in [36, 48, 69]:
                    exit_p, exit_type = simulate(db, entry_bar, entry, direction, stop, target, exit_bar)
                    pnl = pnl_calc(entry, exit_p, direction)

                    gap_results.append({
                        'date': date, 'sym': sym, 'dir': direction, 'gap': round(gap_pct, 2),
                        'min_gap': min_gap, 'entry_bar': entry_bar, 'exit_bar': exit_bar,
                        'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0, 'exit_type': exit_type,
                    })

# Analyze gap fill
print(f'Total gap fill signals: {len(gap_results)}')
for min_gap in [0.3, 0.5, 0.8, 1.0, 1.5]:
    for entry_bar in [1, 2, 3]:
        for exit_bar in [36, 48, 69]:
            sub = [r for r in gap_results if r['min_gap'] == min_gap and r['entry_bar'] == entry_bar and r['exit_bar'] == exit_bar]
            if len(sub) < 10: continue
            # Dedup by date+sym
            seen = set()
            unique = []
            for r in sub:
                key = (r['date'], r['sym'])
                if key not in seen: seen.add(key); unique.append(r)
            w = sum(r['win'] for r in unique)
            wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique)
            days = len(set(r['date'] for r in unique))
            if wr >= 50:
                print(f'  gap>={min_gap} entry@{entry_bar} exit@{exit_bar}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# STRATEGY B: OPENING RANGE BREAKOUT (ORB)
# First N bars define a range. Trade breakout of that range.
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('STRATEGY B: OPENING RANGE BREAKOUT (various ranges)')
print('='*80, flush=True)

orb_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for range_bars in [1, 2, 3, 4, 5, 6]:  # 5min, 10min, 15min, 20min, 25min, 30min ORB
            range_high = max(b['high'] for b in db[:range_bars])
            range_low = min(b['low'] for b in db[:range_bars])
            range_size = range_high - range_low
            if range_size == 0: continue
            range_pct = range_size / pc * 100

            # Wait for breakout in bars after range
            for j in range(range_bars, min(len(db), 24)):  # Scan up to 2 hours
                # Breakout above range
                if db[j]['high'] > range_high and db[j]['close'] > range_high:
                    entry = range_high + range_size * 0.02  # Small buffer
                    direction = 'LONG'
                    # Volume confirmation
                    vol_ok = db[j]['volume'] > sum(b['volume'] for b in db[:range_bars])/range_bars * 1.2

                    for rr in [1.5, 2.0, 2.5, 3.0]:
                        stop = range_low
                        target = entry + (entry - stop) * rr
                        for exit_bar in [36, 48, 69]:
                            exit_p, exit_type = simulate(db, j, entry, direction, stop, target, exit_bar)
                            pnl = pnl_calc(entry, exit_p, direction)
                            orb_results.append({
                                'date': date, 'sym': sym, 'dir': direction,
                                'range_bars': range_bars, 'breakout_bar': j,
                                'range_pct': round(range_pct, 3), 'rr': rr,
                                'exit_bar': exit_bar, 'vol_ok': vol_ok,
                                'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                            })
                    break  # Only first breakout

                # Breakdown below range
                if db[j]['low'] < range_low and db[j]['close'] < range_low:
                    entry = range_low - range_size * 0.02
                    direction = 'SHORT'
                    vol_ok = db[j]['volume'] > sum(b['volume'] for b in db[:range_bars])/range_bars * 1.2

                    for rr in [1.5, 2.0, 2.5, 3.0]:
                        stop = range_high
                        target = entry - (stop - entry) * rr
                        for exit_bar in [36, 48, 69]:
                            exit_p, exit_type = simulate(db, j, entry, direction, stop, target, exit_bar)
                            pnl = pnl_calc(entry, exit_p, direction)
                            orb_results.append({
                                'date': date, 'sym': sym, 'dir': direction,
                                'range_bars': range_bars, 'breakout_bar': j,
                                'range_pct': round(range_pct, 3), 'rr': rr,
                                'exit_bar': exit_bar, 'vol_ok': vol_ok,
                                'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                            })
                    break

print(f'Total ORB signals: {len(orb_results)}')
for range_bars in [1, 2, 3, 4, 5, 6]:
    for rr in [1.5, 2.0, 2.5, 3.0]:
        for exit_bar in [36, 48, 69]:
            for vol_filter in [False, True]:
                sub = [r for r in orb_results if r['range_bars'] == range_bars and r['rr'] == rr
                       and r['exit_bar'] == exit_bar and (not vol_filter or r['vol_ok'])]
                if len(sub) < 10: continue
                seen = set()
                unique = []
                for r in sub:
                    key = (r['date'], r['sym'])
                    if key not in seen: seen.add(key); unique.append(r)
                w = sum(r['win'] for r in unique)
                wr = w/len(unique)*100
                pnl = sum(r['pnl'] for r in unique)
                days = len(set(r['date'] for r in unique))
                if wr >= 50:
                    vl = '+vol' if vol_filter else ''
                    print(f'  ORB-{range_bars*5}min R:{rr}{vl} exit@{exit_bar}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# STRATEGY C: VWAP RECLAIM
# Price drops below VWAP, then reclaims with volume = institutional buy
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('STRATEGY C: VWAP RECLAIM')
print('='*80, flush=True)

vwap_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        # Compute running VWAP
        cum_tp_vol = 0; cum_vol = 0
        vwaps = []
        for b in db:
            tp = (b['high']+b['low']+b['close'])/3
            cum_tp_vol += tp * b['volume']
            cum_vol += b['volume']
            vwaps.append(cum_tp_vol/cum_vol if cum_vol > 0 else b['close'])

        # Look for VWAP reclaim: was below, now crosses above
        for j in range(3, min(len(db), 30)):  # Scan first 2.5 hours
            if j < 2: continue
            price = db[j]['close']
            prev_price = db[j-1]['close']
            vwap = vwaps[j]
            prev_vwap = vwaps[j-1]

            # LONG: was below VWAP, now above with volume
            if prev_price < prev_vwap and price > vwap:
                avg_vol = sum(b['volume'] for b in db[:j])/j
                vol_spike = db[j]['volume'] / avg_vol if avg_vol > 0 else 1

                entry = price
                direction = 'LONG'
                atr = sum(b['high']-b['low'] for b in db[max(0,j-5):j+1])/min(6, j+1)

                for rr in [1.5, 2.0, 2.5, 3.0]:
                    stop = vwap - atr * 0.5
                    target = entry + (entry - stop) * rr
                    for exit_bar in [36, 48, 69]:
                        exit_p, exit_type = simulate(db, j, entry, direction, stop, target, exit_bar)
                        pnl = pnl_calc(entry, exit_p, direction)
                        vwap_results.append({
                            'date': date, 'sym': sym, 'dir': direction,
                            'entry_bar': j, 'rr': rr, 'exit_bar': exit_bar,
                            'vol_spike': round(vol_spike, 2),
                            'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })
                break  # Only first reclaim

            # SHORT: was above VWAP, now below
            if prev_price > prev_vwap and price < vwap:
                avg_vol = sum(b['volume'] for b in db[:j])/j
                vol_spike = db[j]['volume'] / avg_vol if avg_vol > 0 else 1

                entry = price
                direction = 'SHORT'
                atr = sum(b['high']-b['low'] for b in db[max(0,j-5):j+1])/min(6, j+1)

                for rr in [1.5, 2.0, 2.5, 3.0]:
                    stop = vwap + atr * 0.5
                    target = entry - (stop - entry) * rr
                    for exit_bar in [36, 48, 69]:
                        exit_p, exit_type = simulate(db, j, entry, direction, stop, target, exit_bar)
                        pnl = pnl_calc(entry, exit_p, direction)
                        vwap_results.append({
                            'date': date, 'sym': sym, 'dir': direction,
                            'entry_bar': j, 'rr': rr, 'exit_bar': exit_bar,
                            'vol_spike': round(vol_spike, 2),
                            'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })
                break

print(f'Total VWAP reclaim signals: {len(vwap_results)}')
for rr in [1.5, 2.0, 2.5, 3.0]:
    for exit_bar in [36, 48, 69]:
        for vol_min in [0, 1.2, 1.5, 2.0]:
            sub = [r for r in vwap_results if r['rr'] == rr and r['exit_bar'] == exit_bar and r['vol_spike'] >= vol_min]
            if len(sub) < 10: continue
            seen = set()
            unique = []
            for r in sub:
                key = (r['date'], r['sym'])
                if key not in seen: seen.add(key); unique.append(r)
            w = sum(r['win'] for r in unique)
            wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique)
            days = len(set(r['date'] for r in unique))
            if wr >= 45:
                print(f'  R:{rr} vol>={vol_min} exit@{exit_bar}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# STRATEGY D: RELATIVE STRENGTH — trade strongest/weakest vs market
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('STRATEGY D: RELATIVE STRENGTH (strongest/weakest stock)')
print('='*80, flush=True)

rs_results = []
for date in all_dates:
    # Compute morning move for all stocks
    stock_moves = []
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for check_bar in [3, 6, 10]:
            if len(db) <= check_bar: continue
            move = (db[check_bar]['close'] - db[0]['open']) / db[0]['open'] * 100
            stock_moves.append({'sym': sym, 'move': move, 'check_bar': check_bar})

    if not stock_moves: continue

    for check_bar in [3, 6, 10]:
        moves = [s for s in stock_moves if s['check_bar'] == check_bar]
        if len(moves) < 10: continue
        moves.sort(key=lambda x: x['move'])

        # Strongest 3 → LONG, Weakest 3 → SHORT
        for rank, candidates in [('strongest', moves[-3:]), ('weakest', moves[:3])]:
            direction = 'LONG' if rank == 'strongest' else 'SHORT'
            for cand in candidates:
                sym = cand['sym']
                db = date_bars[date][sym]
                entry_bar = check_bar
                entry = db[entry_bar]['close']
                atr = sum(b['high']-b['low'] for b in db[:entry_bar+1])/(entry_bar+1)

                for rr in [1.5, 2.0, 2.5, 3.0]:
                    stop = entry - atr * 1.5 if direction == 'LONG' else entry + atr * 1.5
                    target = entry + (entry-stop)*rr if direction == 'LONG' else entry - (stop-entry)*rr
                    for exit_bar in [36, 48, 69]:
                        exit_p, exit_type = simulate(db, entry_bar, entry, direction, stop, target, exit_bar)
                        pnl = pnl_calc(entry, exit_p, direction)
                        rs_results.append({
                            'date': date, 'sym': sym, 'dir': direction,
                            'rank': rank, 'check_bar': check_bar,
                            'move': round(cand['move'], 3),
                            'rr': rr, 'exit_bar': exit_bar,
                            'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })

print(f'Total relative strength signals: {len(rs_results)}')
for rank in ['strongest', 'weakest']:
    for check_bar in [3, 6, 10]:
        for rr in [1.5, 2.0, 2.5, 3.0]:
            for exit_bar in [36, 48, 69]:
                sub = [r for r in rs_results if r['rank'] == rank and r['check_bar'] == check_bar
                       and r['rr'] == rr and r['exit_bar'] == exit_bar]
                if len(sub) < 10: continue
                seen = set()
                unique = []
                for r in sub:
                    key = (r['date'], r['sym'])
                    if key not in seen: seen.add(key); unique.append(r)
                w = sum(r['win'] for r in unique)
                wr = w/len(unique)*100
                pnl = sum(r['pnl'] for r in unique)
                days = len(set(r['date'] for r in unique))
                if wr >= 45:
                    print(f'  {rank} @bar{check_bar} R:{rr} exit@{exit_bar}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# STRATEGY E: PREVIOUS DAY LEVEL BOUNCE/BREAK
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('STRATEGY E: PREVIOUS DAY LEVEL (support/resistance)')
print('='*80, flush=True)

pdl_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pd = prev_day_data.get((date, sym))
        if pd is None or len(db) < 30: continue

        prev_high = pd['high']
        prev_low = pd['low']
        prev_c = pd['close']

        # Check each bar for touches of prev day levels
        for j in range(1, min(len(db), 30)):
            price = db[j]['close']
            atr = sum(b['high']-b['low'] for b in db[max(0,j-5):j+1])/min(6, j+1)
            tolerance = atr * 0.3

            # Bounce off prev day high (resistance → short)
            if abs(db[j]['high'] - prev_high) < tolerance and price < prev_high:
                entry = price; direction = 'SHORT'
                for rr in [1.5, 2.0, 2.5, 3.0]:
                    stop = prev_high + atr * 0.5
                    target = entry - (stop-entry)*rr
                    for exit_bar in [36, 48, 69]:
                        exit_p, _ = simulate(db, j, entry, direction, stop, target, exit_bar)
                        pnl = pnl_calc(entry, exit_p, direction)
                        pdl_results.append({
                            'date': date, 'sym': sym, 'dir': direction,
                            'level': 'prev_high', 'entry_bar': j,
                            'rr': rr, 'exit_bar': exit_bar,
                            'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })
                break

            # Bounce off prev day low (support → long)
            if abs(db[j]['low'] - prev_low) < tolerance and price > prev_low:
                entry = price; direction = 'LONG'
                for rr in [1.5, 2.0, 2.5, 3.0]:
                    stop = prev_low - atr * 0.5
                    target = entry + (entry-stop)*rr
                    for exit_bar in [36, 48, 69]:
                        exit_p, _ = simulate(db, j, entry, direction, stop, target, exit_bar)
                        pnl = pnl_calc(entry, exit_p, direction)
                        pdl_results.append({
                            'date': date, 'sym': sym, 'dir': direction,
                            'level': 'prev_low', 'entry_bar': j,
                            'rr': rr, 'exit_bar': exit_bar,
                            'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })
                break

print(f'Total prev day level signals: {len(pdl_results)}')
for level in ['prev_high', 'prev_low']:
    for rr in [1.5, 2.0, 2.5, 3.0]:
        for exit_bar in [36, 48, 69]:
            sub = [r for r in pdl_results if r['level'] == level and r['rr'] == rr and r['exit_bar'] == exit_bar]
            if len(sub) < 10: continue
            seen = set()
            unique = []
            for r in sub:
                key = (r['date'], r['sym'])
                if key not in seen: seen.add(key); unique.append(r)
            w = sum(r['win'] for r in unique)
            wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique)
            days = len(set(r['date'] for r in unique))
            if wr >= 45:
                print(f'  {level} R:{rr} exit@{exit_bar}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# STRATEGY F: NARROW RANGE BAR → BREAKOUT
# When early bars are narrow, breakout is explosive
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('STRATEGY F: NARROW RANGE BREAKOUT (NR4/NR7 intraday)')
print('='*80, flush=True)

nr_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        if len(db) < 30: continue
        pc = prev_close_map.get((date, sym))
        if pc is None: continue

        # Check if first N bars have narrow range
        for check_bars in [3, 5, 6]:
            if len(db) <= check_bars + 10: continue
            ranges = [b['high']-b['low'] for b in db[:check_bars]]
            avg_range = sum(ranges)/len(ranges)
            # Compare to typical range (use price-based threshold)
            narrow = avg_range/pc*100 < 0.15  # Less than 0.15% per bar
            if not narrow: continue

            range_high = max(b['high'] for b in db[:check_bars])
            range_low = min(b['low'] for b in db[:check_bars])
            rng = range_high - range_low

            # Wait for breakout
            for j in range(check_bars, min(len(db), 20)):
                if db[j]['close'] > range_high:
                    entry = db[j]['close']; direction = 'LONG'
                    for rr in [1.5, 2.0, 3.0]:
                        stop = range_low
                        target = entry + (entry-stop)*rr
                        for exit_bar in [36, 48, 69]:
                            exit_p, _ = simulate(db, j, entry, direction, stop, target, exit_bar)
                            pnl = pnl_calc(entry, exit_p, direction)
                            nr_results.append({
                                'date': date, 'sym': sym, 'dir': direction,
                                'check_bars': check_bars, 'rr': rr, 'exit_bar': exit_bar,
                                'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                            })
                    break
                if db[j]['close'] < range_low:
                    entry = db[j]['close']; direction = 'SHORT'
                    for rr in [1.5, 2.0, 3.0]:
                        stop = range_high
                        target = entry - (stop-entry)*rr
                        for exit_bar in [36, 48, 69]:
                            exit_p, _ = simulate(db, j, entry, direction, stop, target, exit_bar)
                            pnl = pnl_calc(entry, exit_p, direction)
                            nr_results.append({
                                'date': date, 'sym': sym, 'dir': direction,
                                'check_bars': check_bars, 'rr': rr, 'exit_bar': exit_bar,
                                'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                            })
                    break

print(f'Total narrow range signals: {len(nr_results)}')
for check_bars in [3, 5, 6]:
    for rr in [1.5, 2.0, 3.0]:
        for exit_bar in [36, 48, 69]:
            sub = [r for r in nr_results if r['check_bars'] == check_bars and r['rr'] == rr and r['exit_bar'] == exit_bar]
            if len(sub) < 10: continue
            seen = set()
            unique = []
            for r in sub:
                key = (r['date'], r['sym'])
                if key not in seen: seen.add(key); unique.append(r)
            w = sum(r['win'] for r in unique)
            wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique)
            days = len(set(r['date'] for r in unique))
            if wr >= 45:
                print(f'  NR-{check_bars} R:{rr} exit@{exit_bar}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# GRAND SUMMARY — across all strategy types
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('GRAND SUMMARY: Best setup from each strategy type')
print('='*80)

all_strategy_results = {
    'A_GapFill': gap_results,
    'B_ORB': orb_results,
    'C_VWAPReclaim': vwap_results,
    'D_RelStrength': rs_results,
    'E_PrevDayLevel': pdl_results,
    'F_NarrowRange': nr_results,
}

for strat_name, results in all_strategy_results.items():
    if not results: continue
    # Find best WR setup (min 10 trades)
    best_wr = 0; best_setup = None
    # Group by unique params (exclude date/sym/pnl)
    param_keys = [k for k in results[0].keys() if k not in ('date','sym','dir','pnl','win','move','gap','vol_spike','exit_type','range_pct')]

    from itertools import groupby
    param_groups = defaultdict(list)
    for r in results:
        key = tuple(r.get(k, '') for k in param_keys)
        param_groups[key].append(r)

    for key, group in param_groups.items():
        seen = set()
        unique = []
        for r in group:
            k = (r['date'], r['sym'])
            if k not in seen: seen.add(k); unique.append(r)
        if len(unique) < 10: continue
        w = sum(r['win'] for r in unique)
        wr = w/len(unique)*100
        if wr > best_wr:
            best_wr = wr
            pnl = sum(r['pnl'] for r in unique)
            days = len(set(r['date'] for r in unique))
            best_setup = {'wr': wr, 'n': len(unique), 'pnl': pnl, 'days': days,
                         'params': dict(zip(param_keys, key))}

    if best_setup:
        print(f'\n  {strat_name}: WR={best_setup["wr"]:.0f}%, {best_setup["n"]} trades, {best_setup["days"]} days, P&L={best_setup["pnl"]:+.1f}%')
        print(f'    Params: {best_setup["params"]}')
    else:
        print(f'\n  {strat_name}: No qualifying setups (all < 10 trades or < 45% WR)')

# How many unique trading days across ALL strategies?
all_dates_covered = set()
for results in all_strategy_results.values():
    for r in results:
        if r['win']: all_dates_covered.add(r['date'])
print(f'\nTotal unique winning days across all strategies: {len(all_dates_covered)}/{len(all_dates)}')
