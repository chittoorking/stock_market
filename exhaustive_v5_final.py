"""
EXHAUSTIVE v5 — THE LAST BATCH. Every remaining concept.
Chart patterns, more indicators, statistical, Indian-specific,
advanced exits, regime detection, multi-timeframe.
After this, training is exhausted.
"""
import sys; sys.path.insert(0, '.')
import csv, json, math, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime

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
prev_close_map = {}; prev_day_data = {}; multi_day_data = {}
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
                }
        # Multi-day: last 5 days of daily OHLC for trend
        if i >= 2:
            daily_closes = []
            for j in range(max(0, i-5), i):
                pd_bars = date_bars[dates_for_sym[j]].get(sym, [])
                if pd_bars: daily_closes.append(pd_bars[-1]['close'])
            multi_day_data[(d, sym)] = daily_closes

macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

print(f'{len(all_data)} stocks, {len(all_dates)} days\n')

def simulate(db, entry_bar, entry, direction, stop, target, exit_bar=69):
    for j in range(entry_bar+1, min(len(db), exit_bar+1)):
        if direction=='LONG':
            if db[j]['low'] <= stop: return stop
            if db[j]['high'] >= target: return target
        else:
            if db[j]['high'] >= stop: return stop
            if db[j]['low'] <= target: return target
    return db[min(exit_bar, len(db)-1)]['close']

def simulate_trailing(db, entry_bar, entry, direction, initial_stop, target, exit_bar=69, trail_atr_mult=1.5):
    """Simulate with chandelier trailing stop"""
    stop = initial_stop
    best = entry
    for j in range(entry_bar+1, min(len(db), exit_bar+1)):
        if direction=='LONG':
            if db[j]['high'] > best:
                best = db[j]['high']
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-5),j+1))/min(6,j+1)
                new_stop = best - atr * trail_atr_mult
                stop = max(stop, new_stop)  # Only raise stop
            if db[j]['low'] <= stop: return stop
            if db[j]['high'] >= target: return target
        else:
            if db[j]['low'] < best:
                best = db[j]['low']
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-5),j+1))/min(6,j+1)
                new_stop = best + atr * trail_atr_mult
                stop = min(stop, new_stop)
            if db[j]['high'] >= stop: return stop
            if db[j]['low'] <= target: return target
    return db[min(exit_bar, len(db)-1)]['close']

def pnl_calc(e, x, d): return (x-e)/e*100 if d=='LONG' else (e-x)/e*100

def dedup(results):
    seen = set()
    return [r for r in results if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]

t0 = time.time()

# ═══ 1. TRIANGLE BREAKOUT ═══
print('='*80)
print('1. TRIANGLE BREAKOUT (converging highs/lows)')
print('='*80, flush=True)

tri_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        # Check bars 3-15 for converging pattern
        for end in range(8, min(len(db), 18)):
            start = max(0, end - 8)
            segment = db[start:end+1]
            if len(segment) < 6: continue

            highs = [b['high'] for b in segment]
            lows = [b['low'] for b in segment]

            # Linear regression slope of highs and lows
            n = len(highs)
            x_mean = (n-1)/2
            sx2 = sum((i-x_mean)**2 for i in range(n))
            if sx2 == 0: continue

            high_slope = sum((i-x_mean)*(highs[i]-sum(highs)/n) for i in range(n)) / sx2
            low_slope = sum((i-x_mean)*(lows[i]-sum(lows)/n) for i in range(n)) / sx2

            # Triangle: highs descending AND lows ascending (symmetric)
            # Or ascending: lows rising, highs flat
            # Or descending: highs falling, lows flat
            is_symmetric = high_slope < -0.01 and low_slope > 0.01
            is_ascending = low_slope > 0.01 and abs(high_slope) < 0.01
            is_descending = high_slope < -0.01 and abs(low_slope) < 0.01

            if not (is_symmetric or is_ascending or is_descending): continue

            # Breakout: next bars
            upper = highs[-1]; lower = lows[-1]
            for j in range(end+1, min(len(db), end+8)):
                direction = None
                if db[j]['close'] > upper: direction = 'LONG'
                elif db[j]['close'] < lower: direction = 'SHORT'
                if not direction: continue

                entry = db[j]['close']
                rng = upper - lower
                stop = lower if direction=='LONG' else upper
                for rr in [1.5, 2.0, 2.5]:
                    target = entry + rng*rr if direction=='LONG' else entry - rng*rr
                    for eb in [36, 69]:
                        ep = simulate(db, j, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        tri_results.append({'date':date,'sym':sym,'dir':direction,
                            'type':'sym' if is_symmetric else ('asc' if is_ascending else 'desc'),
                            'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                break
            break

print(f'Triangle signals: {len(tri_results)}')
for ttype in ['sym','asc','desc']:
    for rr in [1.5, 2.0, 2.5]:
        for eb in [36, 69]:
            sub = [r for r in tri_results if r['type']==ttype and r['rr']==rr and r['eb']==eb]
            unique = dedup(sub)
            if len(unique) < 5: continue
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            if wr >= 40:
                print(f'  {ttype} R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 2. DOUBLE TOP/BOTTOM ═══
print('\n' + '='*80)
print('2. DOUBLE TOP / DOUBLE BOTTOM')
print('='*80, flush=True)

dt_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for j in range(6, min(len(db), 20)):
            # Find two similar highs (double top → short)
            for k in range(j-5, j-1):
                if k < 0: continue
                tolerance = (db[k]['high'] + db[j]['high']) / 2 * 0.001  # 0.1%
                if abs(db[j]['high'] - db[k]['high']) < tolerance:
                    # There's a dip between them
                    mid_low = min(db[m]['low'] for m in range(k, j+1))
                    if db[j]['high'] - mid_low < tolerance * 2: continue  # Not enough dip

                    # Confirmation: close below mid_low
                    for m in range(j+1, min(len(db), j+5)):
                        if db[m]['close'] < mid_low:
                            entry = db[m]['close']; direction = 'SHORT'
                            stop = max(db[k]['high'], db[j]['high']) + tolerance
                            for rr in [1.5, 2.0]:
                                target = entry - (stop-entry)*rr
                                for eb in [36, 69]:
                                    ep = simulate(db, m, entry, direction, stop, target, eb)
                                    pnl = pnl_calc(entry, ep, direction)
                                    dt_results.append({'date':date,'sym':sym,'dir':'SHORT','type':'double_top',
                                        'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                            break
                    break

            # Two similar lows (double bottom → long)
            for k in range(j-5, j-1):
                if k < 0: continue
                tolerance = (db[k]['low'] + db[j]['low']) / 2 * 0.001
                if abs(db[j]['low'] - db[k]['low']) < tolerance:
                    mid_high = max(db[m]['high'] for m in range(k, j+1))
                    if mid_high - db[j]['low'] < tolerance * 2: continue

                    for m in range(j+1, min(len(db), j+5)):
                        if db[m]['close'] > mid_high:
                            entry = db[m]['close']; direction = 'LONG'
                            stop = min(db[k]['low'], db[j]['low']) - tolerance
                            for rr in [1.5, 2.0]:
                                target = entry + (entry-stop)*rr
                                for eb in [36, 69]:
                                    ep = simulate(db, m, entry, direction, stop, target, eb)
                                    pnl = pnl_calc(entry, ep, direction)
                                    dt_results.append({'date':date,'sym':sym,'dir':'LONG','type':'double_bottom',
                                        'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                            break
                    break

print(f'Double top/bottom signals: {len(dt_results)}')
for dtype in ['double_top', 'double_bottom']:
    for rr in [1.5, 2.0]:
        for eb in [36, 69]:
            sub = [r for r in dt_results if r['type']==dtype and r['rr']==rr and r['eb']==eb]
            unique = dedup(sub)
            if len(unique) < 5: continue
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            print(f'  {dtype} R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 3. Z-SCORE MEAN REVERSION ═══
print('\n' + '='*80)
print('3. Z-SCORE MEAN REVERSION')
print('='*80, flush=True)

zscore_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for j in range(10, min(len(db), 25)):
            closes = [b['close'] for b in db[:j+1]]
            mean = sum(closes) / len(closes)
            std = math.sqrt(sum((c-mean)**2 for c in closes) / len(closes))
            if std == 0: continue
            zscore = (closes[-1] - mean) / std

            for z_thresh in [1.5, 2.0, 2.5]:
                if abs(zscore) < z_thresh: continue
                direction = 'SHORT' if zscore > 0 else 'LONG'  # Mean revert
                entry = closes[-1]
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-5),j+1))/min(6,j+1)
                stop = entry + atr*2 if direction=='SHORT' else entry - atr*2
                target = mean  # Revert to mean
                for eb in [36, 48, 69]:
                    ep = simulate(db, j, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    zscore_results.append({'date':date,'sym':sym,'dir':direction,'zscore':round(zscore,1),
                        'z_thresh':z_thresh,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
            break

print(f'Z-score signals: {len(zscore_results)}')
for zt in [1.5, 2.0, 2.5]:
    for eb in [36, 48, 69]:
        sub = [r for r in zscore_results if r['z_thresh']==zt and r['eb']==eb]
        unique = dedup(sub)
        if len(unique) < 5: continue
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        if wr >= 40:
            print(f'  Z>={zt} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 4. MULTI-TIMEFRAME (Daily trend + 5min entry) ═══
print('\n' + '='*80)
print('4. MULTI-TIMEFRAME (Daily trend + 5min ORB entry)')
print('='*80, flush=True)

mtf_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        daily = multi_day_data.get((date, sym), [])
        if pc is None or len(db) < 30 or len(daily) < 3: continue

        # Daily trend: last 3 closes
        daily_trend = 'UP' if daily[-1] > daily[-3] else 'DOWN'

        # 5min ORB entry — only in direction of daily trend
        rh = db[0]['high']; rl = db[0]['low']; rs = rh - rl
        if rs == 0: continue

        for j in range(1, min(len(db), 12)):
            if daily_trend == 'UP' and db[j]['close'] > rh:
                direction = 'LONG'
            elif daily_trend == 'DOWN' and db[j]['close'] < rl:
                direction = 'SHORT'
            else: continue

            # Volume check
            if db[0]['volume'] > 0 and db[j]['volume'] < db[0]['volume'] * 1.0: continue

            entry = db[j]['close']
            stop = rl if direction=='LONG' else rh
            risk = abs(entry-stop)
            if risk == 0: continue
            for rr in [1.5, 2.0, 2.5]:
                target = entry + risk*rr if direction=='LONG' else entry - risk*rr
                for eb in [36, 48, 69]:
                    ep = simulate(db, j, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    mtf_results.append({'date':date,'sym':sym,'dir':direction,'daily_trend':daily_trend,
                        'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
            break

print(f'MTF signals: {len(mtf_results)}')
for rr in [1.5, 2.0, 2.5]:
    for eb in [36, 48, 69]:
        sub = [r for r in mtf_results if r['rr']==rr and r['eb']==eb]
        unique = dedup(sub)
        if len(unique) < 10: continue
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        if wr >= 45:
            print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 5. EXPIRY DAY PATTERNS (Indian specific) ═══
print('\n' + '='*80)
print('5. EXPIRY DAY PATTERNS (Thursday = weekly options expiry)')
print('='*80, flush=True)

# Thursday = day 3 (weekday())
expiry_results = []
for date in all_dates:
    try: dow = datetime.strptime(date, '%Y-%m-%d').weekday()
    except: continue

    is_thursday = (dow == 3)
    if not is_thursday: continue

    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        # Expiry day pattern: high volatility, mean reversion after 10:30 AM (bar 6)
        for check_bar in [6, 10]:
            if len(db) <= check_bar: continue
            morning_move = (db[check_bar]['close'] - db[0]['open']) / db[0]['open'] * 100

            if abs(morning_move) < 0.3: continue

            # Fade the morning move (expiry day reversal)
            direction = 'SHORT' if morning_move > 0 else 'LONG'
            entry = db[check_bar]['close']
            atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,check_bar-5),check_bar+1))/min(6,check_bar+1)
            stop = entry + atr*2 if direction=='SHORT' else entry - atr*2
            for rr in [1.0, 1.5, 2.0]:
                target = entry - (stop-entry)*rr if direction=='SHORT' else entry + (entry-stop)*rr
                for eb in [36, 48, 69]:
                    ep = simulate(db, check_bar, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    expiry_results.append({'date':date,'sym':sym,'dir':direction,'check_bar':check_bar,
                        'morning_move':round(morning_move,2),'rr':rr,'eb':eb,
                        'pnl':round(pnl,4),'win':1 if pnl>0 else 0})

print(f'Expiry day signals: {len(expiry_results)} (Thursdays only)')
for check_bar in [6, 10]:
    for rr in [1.0, 1.5, 2.0]:
        for eb in [36, 48, 69]:
            sub = [r for r in expiry_results if r['check_bar']==check_bar and r['rr']==rr and r['eb']==eb]
            unique = dedup(sub)
            if len(unique) < 5: continue
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            if wr >= 40:
                print(f'  @bar{check_bar} R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 6. VIX REGIME SWITCHING ═══
print('\n' + '='*80)
print('6. VIX REGIME SWITCHING (different strategy per VIX level)')
print('='*80, flush=True)

# Load all ORB results from previous analysis
# Simulate: on low VIX days use ORB, on high VIX days use mean reversion
regime_results = []
for date in all_dates:
    mc = macro.get(date, {})
    vix = mc.get('india_vix', 15)  # Default 15 if missing
    regime = 'low_vix' if vix < 15 else ('mid_vix' if vix < 20 else 'high_vix')

    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        if regime in ('low_vix', 'mid_vix'):
            # Trend following: ORB breakout
            rh = db[0]['high']; rl = db[0]['low']; rs = rh - rl
            if rs == 0: continue
            for j in range(1, min(len(db), 12)):
                d = None
                if db[j]['close'] > rh: d = 'LONG'
                elif db[j]['close'] < rl: d = 'SHORT'
                if not d: continue
                entry = db[j]['close']
                stop = rl if d=='LONG' else rh
                risk = abs(entry-stop)
                if risk == 0: break
                target = entry + risk*1.5 if d=='LONG' else entry - risk*1.5
                ep = simulate(db, j, entry, d, stop, target, 36)
                pnl = pnl_calc(entry, ep, d)
                regime_results.append({'date':date,'sym':sym,'dir':d,'regime':regime,'strat':'ORB',
                    'vix':round(vix,1),'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                break
        else:
            # High VIX: mean reversion — fade the morning move
            if len(db) > 10:
                morning = (db[10]['close'] - db[0]['open']) / db[0]['open'] * 100
                if abs(morning) < 0.5: continue
                d = 'SHORT' if morning > 0 else 'LONG'
                entry = db[10]['close']
                atr = sum(db[k]['high']-db[k]['low'] for k in range(5,11))/6
                stop = entry + atr*2 if d=='SHORT' else entry - atr*2
                target = db[0]['open']  # Revert to open
                ep = simulate(db, 10, entry, d, stop, target, 69)
                pnl = pnl_calc(entry, ep, d)
                regime_results.append({'date':date,'sym':sym,'dir':d,'regime':regime,'strat':'MeanRev',
                    'vix':round(vix,1),'pnl':round(pnl,4),'win':1 if pnl>0 else 0})

print(f'Regime signals: {len(regime_results)}')
for regime in ['low_vix', 'mid_vix', 'high_vix']:
    sub = [r for r in regime_results if r['regime']==regime]
    unique = dedup(sub)
    if len(unique) < 5: continue
    w = sum(r['win'] for r in unique); wr = w/len(unique)*100
    pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
    strats = set(r['strat'] for r in unique)
    print(f'  {regime} ({",".join(strats)}): {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 7. CHANDELIER TRAILING STOP (on ORB) ═══
print('\n' + '='*80)
print('7. CHANDELIER TRAILING STOP (applied to ORB)')
print('='*80, flush=True)

trail_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 40: continue

        rh = db[0]['high']; rl = db[0]['low']; rs = rh - rl
        if rs == 0: continue

        for j in range(1, min(len(db), 12)):
            d = None
            if db[j]['close'] > rh: d = 'LONG'
            elif db[j]['close'] < rl: d = 'SHORT'
            if not d: continue
            if db[0]['volume'] > 0 and db[j]['volume'] < db[0]['volume'] * 1.2: break

            entry = db[j]['close']
            stop = rl if d=='LONG' else rh
            risk = abs(entry-stop)
            if risk == 0: break

            # Fixed target
            for rr in [2.0, 3.0, 5.0]:  # Higher targets with trailing
                target = entry + risk*rr if d=='LONG' else entry - risk*rr
                for trail_mult in [1.0, 1.5, 2.0]:
                    ep = simulate_trailing(db, j, entry, d, stop, target, 69, trail_mult)
                    pnl = pnl_calc(entry, ep, d)
                    trail_results.append({'date':date,'sym':sym,'dir':d,'rr':rr,'trail':trail_mult,
                        'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
            break

print(f'Trailing stop signals: {len(trail_results)}')
for rr in [2.0, 3.0, 5.0]:
    for trail in [1.0, 1.5, 2.0]:
        sub = [r for r in trail_results if r['rr']==rr and r['trail']==trail]
        unique = dedup(sub)
        if len(unique) < 10: continue
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        if wr >= 40:
            print(f'  R:{rr} trail:{trail}x ATR: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 8. ELDER RAY (Bull/Bear Power) ═══
print('\n' + '='*80)
print('8. ELDER RAY (Bull/Bear Power)')
print('='*80, flush=True)

elder_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for j in range(10, min(len(db), 22)):
            closes = [b['close'] for b in db[:j+1]]
            highs = [b['high'] for b in db[:j+1]]
            lows = [b['low'] for b in db[:j+1]]
            ema13 = sum(closes[-min(13,len(closes)):]) / min(13,len(closes))

            bull_power = highs[-1] - ema13
            bear_power = lows[-1] - ema13

            # Buy: EMA rising + bear power negative but rising
            if len(closes) >= 3:
                ema_rising = ema13 > sum(closes[-min(13,len(closes)-1):-1]) / min(13,len(closes)-1) if len(closes) > 1 else False
                bear_rising = bear_power > (lows[-2] - sum(closes[-min(14,len(closes)):-1])/min(13,len(closes)-1)) if len(closes) > 2 else False

                if ema_rising and bear_power < 0 and bear_rising:
                    entry = closes[-1]; direction = 'LONG'
                    atr = sum(highs[i]-lows[i] for i in range(max(0,j-5),j+1))/min(6,j+1)
                    stop = entry - atr * 1.5
                    for rr in [1.5, 2.0]:
                        target = entry + (entry-stop)*rr
                        for eb in [36, 69]:
                            ep = simulate(db, j, entry, direction, stop, target, eb)
                            pnl = pnl_calc(entry, ep, direction)
                            elder_results.append({'date':date,'sym':sym,'dir':'LONG',
                                'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                    break

                ema_falling = not ema_rising
                bull_falling = bull_power < (highs[-2] - sum(closes[-min(14,len(closes)):-1])/min(13,len(closes)-1)) if len(closes) > 2 else False
                if ema_falling and bull_power > 0 and bull_falling:
                    entry = closes[-1]; direction = 'SHORT'
                    atr = sum(highs[i]-lows[i] for i in range(max(0,j-5),j+1))/min(6,j+1)
                    stop = entry + atr * 1.5
                    for rr in [1.5, 2.0]:
                        target = entry - (stop-entry)*rr
                        for eb in [36, 69]:
                            ep = simulate(db, j, entry, direction, stop, target, eb)
                            pnl = pnl_calc(entry, ep, direction)
                            elder_results.append({'date':date,'sym':sym,'dir':'SHORT',
                                'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                    break

print(f'Elder Ray signals: {len(elder_results)}')
for rr in [1.5, 2.0]:
    for eb in [36, 69]:
        sub = [r for r in elder_results if r['rr']==rr and r['eb']==eb]
        unique = dedup(sub)
        if len(unique) < 10: continue
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        if wr >= 40:
            print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 9. CONTRARIAN EXTREME FADE ═══
print('\n' + '='*80)
print('9. CONTRARIAN (fade extreme moves > 1.5%)')
print('='*80, flush=True)

contra_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for j in range(3, min(len(db), 15)):
            move = (db[j]['close'] - db[0]['open']) / db[0]['open'] * 100
            for min_move in [1.0, 1.5, 2.0]:
                if abs(move) < min_move: continue
                direction = 'SHORT' if move > 0 else 'LONG'
                entry = db[j]['close']
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-5),j+1))/min(6,j+1)
                stop = entry + atr*2 if direction=='SHORT' else entry - atr*2
                for rr in [1.0, 1.5]:
                    target = entry - (stop-entry)*rr if direction=='SHORT' else entry + (entry-stop)*rr
                    for eb in [36, 48, 69]:
                        ep = simulate(db, j, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        contra_results.append({'date':date,'sym':sym,'dir':direction,
                            'move':round(move,2),'min_move':min_move,'rr':rr,'eb':eb,
                            'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
            if abs(move) >= 1.0: break

print(f'Contrarian signals: {len(contra_results)}')
for min_move in [1.0, 1.5, 2.0]:
    for rr in [1.0, 1.5]:
        for eb in [36, 48, 69]:
            sub = [r for r in contra_results if r['min_move']==min_move and r['rr']==rr and r['eb']==eb]
            unique = dedup(sub)
            if len(unique) < 10: continue
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            if wr >= 45:
                print(f'  fade>={min_move}% R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 10. AROON INDICATOR ═══
print('\n' + '='*80)
print('10. AROON INDICATOR (trend timing)')
print('='*80, flush=True)

aroon_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for j in range(10, min(len(db), 22)):
            period = min(10, j)
            highs = [db[k]['high'] for k in range(j-period+1, j+1)]
            lows = [db[k]['low'] for k in range(j-period+1, j+1)]

            bars_since_high = period - 1 - highs.index(max(highs))
            bars_since_low = period - 1 - lows.index(min(lows))

            aroon_up = (period - bars_since_high) / period * 100
            aroon_down = (period - bars_since_low) / period * 100

            # Strong uptrend: aroon_up > 80, aroon_down < 30
            if aroon_up > 80 and aroon_down < 30:
                direction = 'LONG'
            elif aroon_down > 80 and aroon_up < 30:
                direction = 'SHORT'
            else: continue

            entry = db[j]['close']
            atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-5),j+1))/min(6,j+1)
            stop = entry - atr*1.5 if direction=='LONG' else entry + atr*1.5
            for rr in [1.5, 2.0]:
                target = entry + (entry-stop)*rr if direction=='LONG' else entry - (stop-entry)*rr
                for eb in [36, 69]:
                    ep = simulate(db, j, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    aroon_results.append({'date':date,'sym':sym,'dir':direction,
                        'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
            break

print(f'Aroon signals: {len(aroon_results)}')
for rr in [1.5, 2.0]:
    for eb in [36, 69]:
        sub = [r for r in aroon_results if r['rr']==rr and r['eb']==eb]
        unique = dedup(sub)
        if len(unique) < 10: continue
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        if wr >= 40:
            print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ GRAND SUMMARY ═══
print('\n' + '='*80)
print('GRAND SUMMARY — Final Batch')
print('='*80)
all_strats = {
    'Triangle': tri_results,
    'Double Top/Bottom': dt_results,
    'Z-Score': zscore_results,
    'Multi-Timeframe': mtf_results,
    'Expiry Day': expiry_results,
    'VIX Regime': regime_results,
    'Trailing Stop ORB': trail_results,
    'Elder Ray': elder_results,
    'Contrarian Fade': contra_results,
    'Aroon': aroon_results,
}

for name, results in all_strats.items():
    unique = dedup(results) if results else []
    if not unique: print(f'  {name}: 0 signals'); continue
    w = sum(r['win'] for r in unique); wr = w/len(unique)*100
    pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
    print(f'  {name}: {len(unique)} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')

print(f'\nTime: {time.time()-t0:.1f}s')
print('\n>>> TRAINING EXHAUSTED. Every strategy concept has been tested. <<<')
