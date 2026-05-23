"""
EXHAUSTIVE v3 — ADVANCED STRATEGIES from every school of trading.
Everything from ICT/SMC, Wyckoff, Volume Profile, Heikin Ashi,
institutional flow detection, liquidity sweeps, fair value gaps.

These are concepts used by professional prop traders and smart money.
Testing ALL of them on our 121-day, 45-stock dataset.
"""
import sys; sys.path.insert(0, '.')
import csv, json, math, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# ─── DATA ───
data_dir = Path('data/5min')
all_data = {}; date_bars = defaultdict(dict)
print('Loading...', flush=True)
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

prev_close_map = {}; prev_day_data = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    for i, d in enumerate(dates_for_sym):
        if i > 0:
            pdb = date_bars[dates_for_sym[i-1]].get(sym, [])
            if pdb:
                prev_close_map[(d, sym)] = pdb[-1]['close']
                prev_day_data[(d, sym)] = {
                    'high': max(b['high'] for b in pdb), 'low': min(b['low'] for b in pdb),
                    'close': pdb[-1]['close'], 'open': pdb[0]['open'],
                    'bars': pdb,
                }

macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

print(f'{len(all_data)} stocks, {len(all_dates)} days\n')

def simulate(db, entry_bar, entry, direction, stop, target, exit_bar=69):
    for j in range(entry_bar+1, min(len(db), exit_bar+1)):
        if direction=='LONG':
            if db[j]['low'] <= stop: return stop, 'stop'
            if db[j]['high'] >= target: return target, 'target'
        else:
            if db[j]['high'] >= stop: return stop, 'stop'
            if db[j]['low'] <= target: return target, 'target'
    return db[min(exit_bar, len(db)-1)]['close'], 'time'

def pnl_calc(entry, exit_p, direction):
    return (exit_p-entry)/entry*100 if direction=='LONG' else (entry-exit_p)/entry*100

t0 = time.time()

# ═══════════════════════════════════════════════════════════════
# 1. FAIR VALUE GAP (FVG) — ICT concept
# A gap between bar[i-2] and bar[i] that bar[i-1] doesn't fill.
# Price tends to return to fill these gaps = high probability entry.
# ═══════════════════════════════════════════════════════════════
print('='*80)
print('1. FAIR VALUE GAP (FVG)')
print('='*80, flush=True)

fvg_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for i in range(2, min(len(db), 20)):
            # Bullish FVG: bar[i]'s low > bar[i-2]'s high (gap up that middle bar didn't fill)
            if db[i]['low'] > db[i-2]['high']:
                fvg_top = db[i]['low']; fvg_bottom = db[i-2]['high']
                fvg_size = (fvg_top - fvg_bottom) / pc * 100
                if fvg_size < 0.05: continue  # Too small

                # Wait for price to come back to FVG (pullback entry)
                for j in range(i+1, min(len(db), i+15)):
                    if db[j]['low'] <= fvg_top and db[j]['close'] > fvg_bottom:
                        # Entry at FVG midpoint, long
                        entry = (fvg_top + fvg_bottom) / 2
                        direction = 'LONG'
                        stop = fvg_bottom - (fvg_top - fvg_bottom)
                        for rr in [1.5, 2.0, 2.5, 3.0]:
                            target = entry + (entry - stop) * rr
                            for eb in [36, 69]:
                                ep, et = simulate(db, j, entry, direction, stop, target, eb)
                                pnl = pnl_calc(entry, ep, direction)
                                fvg_results.append({
                                    'date': date, 'sym': sym, 'dir': 'LONG',
                                    'fvg_bar': i, 'fill_bar': j, 'fvg_size': round(fvg_size, 3),
                                    'rr': rr, 'eb': eb,
                                    'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                                })
                        break

            # Bearish FVG: bar[i]'s high < bar[i-2]'s low
            if db[i]['high'] < db[i-2]['low']:
                fvg_top = db[i-2]['low']; fvg_bottom = db[i]['high']
                fvg_size = (fvg_top - fvg_bottom) / pc * 100
                if fvg_size < 0.05: continue

                for j in range(i+1, min(len(db), i+15)):
                    if db[j]['high'] >= fvg_bottom and db[j]['close'] < fvg_top:
                        entry = (fvg_top + fvg_bottom) / 2
                        direction = 'SHORT'
                        stop = fvg_top + (fvg_top - fvg_bottom)
                        for rr in [1.5, 2.0, 2.5, 3.0]:
                            target = entry - (stop - entry) * rr
                            for eb in [36, 69]:
                                ep, et = simulate(db, j, entry, direction, stop, target, eb)
                                pnl = pnl_calc(entry, ep, direction)
                                fvg_results.append({
                                    'date': date, 'sym': sym, 'dir': 'SHORT',
                                    'fvg_bar': i, 'fill_bar': j, 'fvg_size': round(fvg_size, 3),
                                    'rr': rr, 'eb': eb,
                                    'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                                })
                        break

print(f'FVG signals: {len(fvg_results)}')
for rr in [1.5, 2.0, 2.5, 3.0]:
    for eb in [36, 69]:
        sub = [r for r in fvg_results if r['rr']==rr and r['eb']==eb]
        if len(sub) < 10: continue
        seen = set()
        unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        w = sum(r['win'] for r in unique)
        wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique)
        days = len(set(r['date'] for r in unique))
        if wr >= 45:
            print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')

# FVG with size filter
for min_size in [0.1, 0.15, 0.2, 0.3]:
    sub = [r for r in fvg_results if r['rr']==2.0 and r['eb']==36 and r['fvg_size']>=min_size]
    if len(sub) < 10: continue
    seen = set()
    unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
    w = sum(r['win'] for r in unique); wr = w/len(unique)*100
    pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
    if wr >= 45:
        print(f'  FVG>={min_size}% R:2.0 exit@36: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# 2. LIQUIDITY SWEEP (Stop Hunt) — ICT concept
# Price takes out a clear high/low (stops), then reverses.
# Smart money hunts retail stops then enters.
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('2. LIQUIDITY SWEEP (Stop Hunt)')
print('='*80, flush=True)

sweep_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        pd = prev_day_data.get((date, sym))
        if pc is None or pd is None or len(db) < 30: continue

        prev_high = pd['high']; prev_low = pd['low']

        for j in range(1, min(len(db), 24)):
            # Sweep of previous day high then reversal (SHORT)
            if db[j]['high'] > prev_high and db[j]['close'] < prev_high:
                sweep_size = (db[j]['high'] - prev_high) / pc * 100
                if sweep_size < 0.05: continue
                entry = db[j]['close']; direction = 'SHORT'
                stop = db[j]['high'] + (db[j]['high'] - prev_high)
                for rr in [1.5, 2.0, 2.5, 3.0]:
                    target = entry - (stop - entry) * rr
                    for eb in [36, 48, 69]:
                        ep, _ = simulate(db, j, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        sweep_results.append({
                            'date': date, 'sym': sym, 'dir': 'SHORT', 'type': 'high_sweep',
                            'sweep_bar': j, 'sweep_size': round(sweep_size, 3),
                            'rr': rr, 'eb': eb,
                            'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })
                break

            # Sweep of previous day low then reversal (LONG)
            if db[j]['low'] < prev_low and db[j]['close'] > prev_low:
                sweep_size = (prev_low - db[j]['low']) / pc * 100
                if sweep_size < 0.05: continue
                entry = db[j]['close']; direction = 'LONG'
                stop = db[j]['low'] - (prev_low - db[j]['low'])
                for rr in [1.5, 2.0, 2.5, 3.0]:
                    target = entry + (entry - stop) * rr
                    for eb in [36, 48, 69]:
                        ep, _ = simulate(db, j, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        sweep_results.append({
                            'date': date, 'sym': sym, 'dir': 'LONG', 'type': 'low_sweep',
                            'sweep_bar': j, 'sweep_size': round(sweep_size, 3),
                            'rr': rr, 'eb': eb,
                            'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })
                break

print(f'Liquidity sweep signals: {len(sweep_results)}')
for sweep_type in ['high_sweep', 'low_sweep']:
    for rr in [1.5, 2.0, 2.5, 3.0]:
        for eb in [36, 48, 69]:
            sub = [r for r in sweep_results if r['type']==sweep_type and r['rr']==rr and r['eb']==eb]
            if len(sub) < 10: continue
            seen = set()
            unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            if wr >= 45:
                print(f'  {sweep_type} R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# 3. ORDER BLOCK — ICT concept
# Last down candle before a strong up move (or vice versa).
# Institutional order flow leaves a "block" that acts as S/R.
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('3. ORDER BLOCK')
print('='*80, flush=True)

ob_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for i in range(3, min(len(db), 20)):
            # Bullish OB: a red candle followed by strong green move
            if (db[i-1]['close'] < db[i-1]['open'] and  # Red candle
                db[i]['close'] > db[i]['open'] and       # Green candle
                (db[i]['close'] - db[i]['open']) > (db[i-1]['open'] - db[i-1]['close']) * 1.5 and  # Green > 1.5x red
                db[i]['volume'] > db[i-1]['volume'] * 1.3):  # Volume confirms

                ob_high = db[i-1]['open']; ob_low = db[i-1]['low']
                # Wait for price to pull back to OB
                for j in range(i+1, min(len(db), i+12)):
                    if db[j]['low'] <= ob_high and db[j]['close'] > ob_low:
                        entry = db[j]['close']; direction = 'LONG'
                        stop = ob_low - (ob_high - ob_low) * 0.5
                        for rr in [1.5, 2.0, 3.0]:
                            target = entry + (entry - stop) * rr
                            for eb in [36, 69]:
                                ep, _ = simulate(db, j, entry, direction, stop, target, eb)
                                pnl = pnl_calc(entry, ep, direction)
                                ob_results.append({
                                    'date': date, 'sym': sym, 'dir': 'LONG',
                                    'ob_bar': i-1, 'entry_bar': j, 'rr': rr, 'eb': eb,
                                    'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                                })
                        break

            # Bearish OB: green candle followed by strong red move
            if (db[i-1]['close'] > db[i-1]['open'] and
                db[i]['close'] < db[i]['open'] and
                (db[i]['open'] - db[i]['close']) > (db[i-1]['close'] - db[i-1]['open']) * 1.5 and
                db[i]['volume'] > db[i-1]['volume'] * 1.3):

                ob_high = db[i-1]['high']; ob_low = db[i-1]['open']
                for j in range(i+1, min(len(db), i+12)):
                    if db[j]['high'] >= ob_low and db[j]['close'] < ob_high:
                        entry = db[j]['close']; direction = 'SHORT'
                        stop = ob_high + (ob_high - ob_low) * 0.5
                        for rr in [1.5, 2.0, 3.0]:
                            target = entry - (stop - entry) * rr
                            for eb in [36, 69]:
                                ep, _ = simulate(db, j, entry, direction, stop, target, eb)
                                pnl = pnl_calc(entry, ep, direction)
                                ob_results.append({
                                    'date': date, 'sym': sym, 'dir': 'SHORT',
                                    'ob_bar': i-1, 'entry_bar': j, 'rr': rr, 'eb': eb,
                                    'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                                })
                        break

print(f'Order block signals: {len(ob_results)}')
for rr in [1.5, 2.0, 3.0]:
    for eb in [36, 69]:
        sub = [r for r in ob_results if r['rr']==rr and r['eb']==eb]
        if len(sub) < 5: continue
        seen = set()
        unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# 4. HEIKIN ASHI TREND CONFIRMATION
# HA candles smooth noise. Enter when HA turns green/red after pullback.
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('4. HEIKIN ASHI TREND')
print('='*80, flush=True)

ha_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        # Compute HA candles
        ha = []
        for i, b in enumerate(db):
            ha_close = (b['open']+b['high']+b['low']+b['close'])/4
            if i == 0:
                ha_open = (b['open']+b['close'])/2
            else:
                ha_open = (ha[-1]['open']+ha[-1]['close'])/2
            ha.append({'open': ha_open, 'close': ha_close,
                       'high': max(b['high'], ha_open, ha_close),
                       'low': min(b['low'], ha_open, ha_close)})

        # Look for HA trend reversal in first 20 bars
        for i in range(3, min(len(ha), 20)):
            # Bullish reversal: previous HA was red, current is green with no lower wick
            if (ha[i-1]['close'] < ha[i-1]['open'] and  # Prev red
                ha[i]['close'] > ha[i]['open'] and       # Current green
                ha[i]['low'] == ha[i]['open']):           # No lower wick = strong

                entry = db[i]['close']; direction = 'LONG'
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,i-5), i+1)) / min(6,i+1)
                stop = entry - atr * 1.5
                for rr in [1.5, 2.0, 2.5]:
                    target = entry + (entry - stop) * rr
                    for eb in [36, 69]:
                        ep, _ = simulate(db, i, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        ha_results.append({
                            'date': date, 'sym': sym, 'dir': 'LONG', 'ha_bar': i,
                            'rr': rr, 'eb': eb,
                            'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })
                break

            # Bearish reversal
            if (ha[i-1]['close'] > ha[i-1]['open'] and
                ha[i]['close'] < ha[i]['open'] and
                ha[i]['high'] == ha[i]['open']):

                entry = db[i]['close']; direction = 'SHORT'
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,i-5), i+1)) / min(6,i+1)
                stop = entry + atr * 1.5
                for rr in [1.5, 2.0, 2.5]:
                    target = entry - (stop - entry) * rr
                    for eb in [36, 69]:
                        ep, _ = simulate(db, i, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        ha_results.append({
                            'date': date, 'sym': sym, 'dir': 'SHORT', 'ha_bar': i,
                            'rr': rr, 'eb': eb,
                            'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })
                break

print(f'Heikin Ashi signals: {len(ha_results)}')
for rr in [1.5, 2.0, 2.5]:
    for eb in [36, 69]:
        sub = [r for r in ha_results if r['rr']==rr and r['eb']==eb]
        if len(sub) < 5: continue
        seen = set()
        unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# 5. ABSORPTION — High volume, tiny range = institutional accumulation
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('5. ABSORPTION CANDLES (high vol, small range)')
print('='*80, flush=True)

abs_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        avg_vol = sum(b['volume'] for b in db[:6]) / 6 if len(db) >= 6 else 1
        avg_range = sum(b['high']-b['low'] for b in db[:6]) / 6 if len(db) >= 6 else 1

        for i in range(3, min(len(db), 20)):
            rng = db[i]['high'] - db[i]['low']
            vol = db[i]['volume']

            # Absorption: volume > 2x avg but range < 0.5x avg
            if vol > avg_vol * 2 and rng < avg_range * 0.5 and rng > 0:
                # Direction from next bar's confirmation
                if i + 1 >= len(db): continue
                if db[i+1]['close'] > db[i+1]['open']:
                    direction = 'LONG'
                elif db[i+1]['close'] < db[i+1]['open']:
                    direction = 'SHORT'
                else: continue

                entry = db[i+1]['close']
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,i-5), i+1)) / min(6,i+1)
                stop = entry - atr * 1.5 if direction=='LONG' else entry + atr * 1.5
                for rr in [1.5, 2.0, 3.0]:
                    target = entry + (entry-stop)*rr if direction=='LONG' else entry - (stop-entry)*rr
                    for eb in [36, 69]:
                        ep, _ = simulate(db, i+1, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        abs_results.append({
                            'date': date, 'sym': sym, 'dir': direction, 'abs_bar': i,
                            'vol_ratio': round(vol/avg_vol, 1), 'range_ratio': round(rng/avg_range, 2),
                            'rr': rr, 'eb': eb,
                            'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })
                break

print(f'Absorption signals: {len(abs_results)}')
for rr in [1.5, 2.0, 3.0]:
    for eb in [36, 69]:
        sub = [r for r in abs_results if r['rr']==rr and r['eb']==eb]
        if len(sub) < 5: continue
        seen = set()
        unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# 6. WYCKOFF SPRING / UPTHRUST
# Spring: price breaks below support, then immediately reverses up
# Upthrust: price breaks above resistance, then reverses down
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('6. WYCKOFF SPRING/UPTHRUST')
print('='*80, flush=True)

wyckoff_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        pd = prev_day_data.get((date, sym))
        if pc is None or pd is None or len(db) < 30: continue

        # Use prev day's range as the "trading range"
        tr_high = pd['high']; tr_low = pd['low']

        for j in range(1, min(len(db), 20)):
            # Spring: wick below trading range low, close back above
            if db[j]['low'] < tr_low and db[j]['close'] > tr_low:
                spring_depth = (tr_low - db[j]['low']) / pc * 100
                if spring_depth < 0.05: continue

                # Confirmation: next bar is green
                if j+1 < len(db) and db[j+1]['close'] > db[j+1]['open']:
                    entry = db[j+1]['close']; direction = 'LONG'
                    stop = db[j]['low'] - spring_depth * pc / 100
                    for rr in [1.5, 2.0, 2.5, 3.0]:
                        target = entry + (entry - stop) * rr
                        for eb in [36, 48, 69]:
                            ep, _ = simulate(db, j+1, entry, direction, stop, target, eb)
                            pnl = pnl_calc(entry, ep, direction)
                            wyckoff_results.append({
                                'date': date, 'sym': sym, 'dir': 'LONG', 'type': 'spring',
                                'bar': j, 'depth': round(spring_depth, 3),
                                'rr': rr, 'eb': eb,
                                'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                            })
                break

            # Upthrust: wick above trading range high, close back below
            if db[j]['high'] > tr_high and db[j]['close'] < tr_high:
                ut_depth = (db[j]['high'] - tr_high) / pc * 100
                if ut_depth < 0.05: continue

                if j+1 < len(db) and db[j+1]['close'] < db[j+1]['open']:
                    entry = db[j+1]['close']; direction = 'SHORT'
                    stop = db[j]['high'] + ut_depth * pc / 100
                    for rr in [1.5, 2.0, 2.5, 3.0]:
                        target = entry - (stop - entry) * rr
                        for eb in [36, 48, 69]:
                            ep, _ = simulate(db, j+1, entry, direction, stop, target, eb)
                            pnl = pnl_calc(entry, ep, direction)
                            wyckoff_results.append({
                                'date': date, 'sym': sym, 'dir': 'SHORT', 'type': 'upthrust',
                                'bar': j, 'depth': round(ut_depth, 3),
                                'rr': rr, 'eb': eb,
                                'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                            })
                break

print(f'Wyckoff signals: {len(wyckoff_results)}')
for wtype in ['spring', 'upthrust']:
    for rr in [1.5, 2.0, 2.5, 3.0]:
        for eb in [36, 48, 69]:
            sub = [r for r in wyckoff_results if r['type']==wtype and r['rr']==rr and r['eb']==eb]
            if len(sub) < 10: continue
            seen = set()
            unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            if wr >= 45:
                print(f'  {wtype} R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# 7. VOLUME PROFILE — Point of Control (POC)
# POC = price level with most volume. Acts as magnet.
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('7. VOLUME PROFILE (POC)')
print('='*80, flush=True)

poc_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pd = prev_day_data.get((date, sym))
        if pd is None or len(db) < 30: continue
        prev_bars = pd.get('bars', [])
        if not prev_bars: continue

        # Compute POC from previous day
        # Discretize price into bins
        ph = pd['high']; pl = pd['low']
        if ph == pl: continue
        n_bins = 20
        bin_size = (ph - pl) / n_bins
        vol_profile = [0] * n_bins
        for b in prev_bars:
            mid = (b['high'] + b['low']) / 2
            bin_idx = min(int((mid - pl) / bin_size), n_bins - 1)
            vol_profile[bin_idx] += b['volume']

        poc_bin = vol_profile.index(max(vol_profile))
        poc_price = pl + (poc_bin + 0.5) * bin_size

        # Value area (70% of volume)
        total_vol = sum(vol_profile)
        sorted_bins = sorted(range(n_bins), key=lambda x: -vol_profile[x])
        va_vol = 0; va_bins = set()
        for bi in sorted_bins:
            va_vol += vol_profile[bi]
            va_bins.add(bi)
            if va_vol >= total_vol * 0.7: break
        va_high = pl + (max(va_bins) + 1) * bin_size
        va_low = pl + min(va_bins) * bin_size

        # Strategy: if price opens outside value area, trade toward POC
        if len(db) < 5: continue
        open_price = db[0]['open']

        if open_price > va_high:
            # Opened above value area → short toward POC
            for j in range(1, min(len(db), 10)):
                if db[j]['close'] < db[j]['open']:  # Red confirmation
                    entry = db[j]['close']; direction = 'SHORT'
                    stop = db[j]['high'] + (db[j]['high'] - db[j]['low'])
                    target = poc_price
                    for eb in [36, 48, 69]:
                        ep, _ = simulate(db, j, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        poc_results.append({
                            'date': date, 'sym': sym, 'dir': 'SHORT', 'type': 'above_va',
                            'eb': eb, 'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })
                    break

        elif open_price < va_low:
            # Opened below value area → long toward POC
            for j in range(1, min(len(db), 10)):
                if db[j]['close'] > db[j]['open']:
                    entry = db[j]['close']; direction = 'LONG'
                    stop = db[j]['low'] - (db[j]['high'] - db[j]['low'])
                    target = poc_price
                    for eb in [36, 48, 69]:
                        ep, _ = simulate(db, j, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        poc_results.append({
                            'date': date, 'sym': sym, 'dir': 'LONG', 'type': 'below_va',
                            'eb': eb, 'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                        })
                    break

print(f'Volume Profile POC signals: {len(poc_results)}')
for ptype in ['above_va', 'below_va']:
    for eb in [36, 48, 69]:
        sub = [r for r in poc_results if r['type']==ptype and r['eb']==eb]
        if len(sub) < 5: continue
        seen = set()
        unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        print(f'  {ptype} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# 8. INITIAL BALANCE (IB) — Market Profile concept
# First 30-60 min defines the IB. 80% of the time price stays within
# 1.5x IB. Breakout of IB = strong directional move.
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('8. INITIAL BALANCE BREAKOUT (Market Profile)')
print('='*80, flush=True)

ib_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 40: continue

        for ib_bars in [6, 12]:  # 30min or 60min IB
            if len(db) <= ib_bars + 10: continue
            ib_high = max(b['high'] for b in db[:ib_bars])
            ib_low = min(b['low'] for b in db[:ib_bars])
            ib_range = ib_high - ib_low
            if ib_range == 0: continue

            # Wait for IB breakout
            for j in range(ib_bars, min(len(db), ib_bars + 18)):
                if db[j]['close'] > ib_high:
                    entry = db[j]['close']; direction = 'LONG'
                    stop = ib_high - ib_range * 0.3  # Stop inside IB
                    # Target: IB extension (1.5x IB range from IB high)
                    for target_mult in [1.0, 1.5, 2.0]:
                        target = ib_high + ib_range * target_mult
                        for eb in [36, 48, 69]:
                            ep, _ = simulate(db, j, entry, direction, stop, target, eb)
                            pnl = pnl_calc(entry, ep, direction)
                            ib_results.append({
                                'date': date, 'sym': sym, 'dir': 'LONG',
                                'ib_bars': ib_bars, 'target_mult': target_mult, 'eb': eb,
                                'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                            })
                    break

                if db[j]['close'] < ib_low:
                    entry = db[j]['close']; direction = 'SHORT'
                    stop = ib_low + ib_range * 0.3
                    for target_mult in [1.0, 1.5, 2.0]:
                        target = ib_low - ib_range * target_mult
                        for eb in [36, 48, 69]:
                            ep, _ = simulate(db, j, entry, direction, stop, target, eb)
                            pnl = pnl_calc(entry, ep, direction)
                            ib_results.append({
                                'date': date, 'sym': sym, 'dir': 'SHORT',
                                'ib_bars': ib_bars, 'target_mult': target_mult, 'eb': eb,
                                'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                            })
                    break

print(f'IB breakout signals: {len(ib_results)}')
for ib_bars in [6, 12]:
    for tm in [1.0, 1.5, 2.0]:
        for eb in [36, 48, 69]:
            sub = [r for r in ib_results if r['ib_bars']==ib_bars and r['target_mult']==tm and r['eb']==eb]
            if len(sub) < 10: continue
            seen = set()
            unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            if wr >= 48:
                print(f'  IB-{ib_bars*5}min T:{tm}x exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# 9. POWER OF 3 (ICT) — Accumulation → Manipulation → Distribution
# First 30min = accumulation. Then manipulation (fake breakout).
# Then distribution (real move opposite to manipulation).
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('9. POWER OF 3 (ICT — AMD cycle)')
print('='*80, flush=True)

po3_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 40: continue

        # Phase 1: Accumulation (first 6 bars = 30min) — tight range
        accum = db[:6]
        accum_range = max(b['high'] for b in accum) - min(b['low'] for b in accum)
        accum_pct = accum_range / pc * 100
        if accum_pct > 0.5: continue  # Not tight enough for accumulation

        accum_high = max(b['high'] for b in accum)
        accum_low = min(b['low'] for b in accum)

        # Phase 2: Manipulation (bars 6-12) — fake breakout
        for j in range(6, min(len(db), 15)):
            # Fake breakout up → real move down
            if db[j]['high'] > accum_high and db[j]['close'] < accum_high:
                # Phase 3: Distribution — enter SHORT
                if j+1 < len(db) and db[j+1]['close'] < db[j+1]['open']:
                    entry = db[j+1]['close']; direction = 'SHORT'
                    stop = db[j]['high'] + accum_range * 0.3
                    for rr in [1.5, 2.0, 3.0]:
                        target = entry - (stop-entry)*rr
                        for eb in [36, 48, 69]:
                            ep, _ = simulate(db, j+1, entry, direction, stop, target, eb)
                            pnl = pnl_calc(entry, ep, direction)
                            po3_results.append({
                                'date': date, 'sym': sym, 'dir': 'SHORT',
                                'manip_bar': j, 'accum_pct': round(accum_pct, 3),
                                'rr': rr, 'eb': eb,
                                'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                            })
                break

            # Fake breakout down → real move up
            if db[j]['low'] < accum_low and db[j]['close'] > accum_low:
                if j+1 < len(db) and db[j+1]['close'] > db[j+1]['open']:
                    entry = db[j+1]['close']; direction = 'LONG'
                    stop = db[j]['low'] - accum_range * 0.3
                    for rr in [1.5, 2.0, 3.0]:
                        target = entry + (entry-stop)*rr
                        for eb in [36, 48, 69]:
                            ep, _ = simulate(db, j+1, entry, direction, stop, target, eb)
                            pnl = pnl_calc(entry, ep, direction)
                            po3_results.append({
                                'date': date, 'sym': sym, 'dir': 'LONG',
                                'manip_bar': j, 'accum_pct': round(accum_pct, 3),
                                'rr': rr, 'eb': eb,
                                'pnl': round(pnl, 4), 'win': 1 if pnl > 0 else 0,
                            })
                break

print(f'Power of 3 signals: {len(po3_results)}')
for rr in [1.5, 2.0, 3.0]:
    for eb in [36, 48, 69]:
        sub = [r for r in po3_results if r['rr']==rr and r['eb']==eb]
        if len(sub) < 5: continue
        seen = set()
        unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══════════════════════════════════════════════════════════════
# GRAND SUMMARY
# ═══════════════════════════════════════════════════════════════
print('\n' + '='*80)
print('GRAND SUMMARY — Best from each advanced strategy')
print('='*80)

all_strats = {
    'FVG': fvg_results,
    'Liquidity Sweep': sweep_results,
    'Order Block': ob_results,
    'Heikin Ashi': ha_results,
    'Absorption': abs_results,
    'Wyckoff': wyckoff_results,
    'Volume Profile': poc_results,
    'IB Breakout': ib_results,
    'Power of 3': po3_results,
}

for name, results in all_strats.items():
    if not results: print(f'  {name}: no signals'); continue
    # Find best setup
    best_wr = 0; best_info = None
    # Group by params
    param_keys = [k for k in results[0] if k not in ('date','sym','pnl','win','dir')]
    groups = defaultdict(list)
    for r in results:
        key = tuple(r.get(k,'') for k in param_keys)
        groups[key].append(r)

    for key, group in groups.items():
        seen = set()
        unique = [r for r in group if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        if len(unique) < 5: continue
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        if wr > best_wr:
            best_wr = wr
            pnl = sum(r['pnl'] for r in unique)
            days = len(set(r['date'] for r in unique))
            best_info = f'{len(unique)} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%'
            best_info += f' | params: {dict(zip(param_keys, key))}'

    if best_info:
        print(f'\n  {name}: {best_info}')
    else:
        print(f'\n  {name}: no qualifying setup (all < 5 trades)')

# Coverage: how many unique days have at least one winning trade?
all_winning_dates = set()
for results in all_strats.values():
    for r in results:
        if r['win']: all_winning_dates.add(r['date'])
print(f'\nTotal days with at least one winner across ALL advanced strategies: {len(all_winning_dates)}/{len(all_dates)}')

print(f'\nTotal time: {time.time()-t0:.1f}s')
