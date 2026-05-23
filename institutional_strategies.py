"""
INSTITUTIONAL/DOCUMENTED STRATEGIES from famous traders.
All of these are publicly documented and in training data.

1. RSI(2) Mean Reversion — Larry Connors
2. Holy Grail — Linda Raschke (ADX>30 + EMA pullback)
3. Turtle Soup — Linda Raschke (fade breakout)
4. VCP (Volatility Contraction) — Mark Minervini
5. ATR Channel Breakout — Turtle Traders
6. 3-Bar Play — Jeff Cooper
7. Momentum Ignition — prop desk pattern
8. Opening Drive — prop desk pattern
9. Fade the Gap — professional gap trading
10. Institutional Candle — large body + volume = smart money
"""
import sys; sys.path.insert(0, '.')
import csv, math, time
from pathlib import Path
from collections import defaultdict

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
                    'close': pdb[-1]['close'], 'open': pdb[0]['open'], 'bars': pdb,
                }
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

def pnl_calc(entry, exit_p, direction):
    return (exit_p-entry)/entry*100 if direction=='LONG' else (entry-exit_p)/entry*100

def rsi_2(closes):
    """RSI with period 2 — Connors style"""
    if len(closes) < 3: return 50
    gains = [max(0, closes[i]-closes[i-1]) for i in range(-2, 0)]
    losses = [max(0, closes[i-1]-closes[i]) for i in range(-2, 0)]
    ag = sum(gains)/2; al = sum(losses)/2
    return 100 - 100/(1+ag/al) if al > 0 else (100 if ag > 0 else 50)

def adx_calc(highs, lows, closes, period=7):
    if len(closes) < period+1: return 0
    plus_dm = minus_dm = tr_sum = 0
    for i in range(len(closes)-period, len(closes)):
        hd = highs[i]-highs[i-1]; ld = lows[i-1]-lows[i]
        plus_dm += max(0, hd) if hd > ld else 0
        minus_dm += max(0, ld) if ld > hd else 0
        tr_sum += max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
    if tr_sum == 0: return 0
    pdi = plus_dm/tr_sum*100; mdi = minus_dm/tr_sum*100
    return abs(pdi-mdi)/(pdi+mdi)*100 if (pdi+mdi) > 0 else 0

t0 = time.time()

# ═══ 1. RSI(2) MEAN REVERSION — Larry Connors ═══
print('='*80)
print('1. RSI(2) MEAN REVERSION (Connors)')
print('='*80, flush=True)
rsi2_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for j in range(5, min(len(db), 20)):
            closes = [b['close'] for b in db[:j+1]]
            r2 = rsi_2(closes)

            # Buy when RSI(2) < 10 (extreme oversold)
            if r2 < 10:
                entry = db[j]['close']; direction = 'LONG'
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-5),j+1))/min(6,j+1)
                stop = entry - atr * 2
                for rr in [1.0, 1.5, 2.0]:
                    target = entry + (entry-stop)*rr
                    for eb in [36, 48, 69]:
                        ep = simulate(db, j, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        rsi2_results.append({'date':date,'sym':sym,'dir':'LONG','rsi2':round(r2,1),
                                            'bar':j,'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                break

            # Sell when RSI(2) > 90 (extreme overbought)
            if r2 > 90:
                entry = db[j]['close']; direction = 'SHORT'
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-5),j+1))/min(6,j+1)
                stop = entry + atr * 2
                for rr in [1.0, 1.5, 2.0]:
                    target = entry - (stop-entry)*rr
                    for eb in [36, 48, 69]:
                        ep = simulate(db, j, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        rsi2_results.append({'date':date,'sym':sym,'dir':'SHORT','rsi2':round(r2,1),
                                            'bar':j,'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                break

print(f'RSI(2) signals: {len(rsi2_results)}')
for rr in [1.0, 1.5, 2.0]:
    for eb in [36, 48, 69]:
        sub = [r for r in rsi2_results if r['rr']==rr and r['eb']==eb]
        if len(sub) < 10: continue
        seen = set()
        unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        if wr >= 45:
            print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 2. HOLY GRAIL — Linda Raschke ═══
print('\n' + '='*80)
print('2. HOLY GRAIL (Raschke — ADX>30 + EMA pullback)')
print('='*80, flush=True)
hg_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for j in range(10, min(len(db), 25)):
            closes = [b['close'] for b in db[:j+1]]
            highs = [b['high'] for b in db[:j+1]]
            lows = [b['low'] for b in db[:j+1]]

            adx = adx_calc(highs, lows, closes, min(7, j))
            if adx < 25: continue  # Need strong trend

            # EMA9
            ema9 = sum(closes[-min(9,len(closes)):]) / min(9,len(closes))
            price = closes[-1]

            # Uptrend + pullback to EMA
            if closes[-1] > closes[-3] > closes[-5] if len(closes)>=5 else closes[-1] > closes[-3]:
                # Price touched or near EMA9
                if abs(price - ema9) / price * 100 < 0.1:
                    entry = price; direction = 'LONG'
                    atr = sum(highs[i]-lows[i] for i in range(max(0,j-5),j+1))/min(6,j+1)
                    stop = ema9 - atr
                    for rr in [1.5, 2.0, 2.5]:
                        target = entry + (entry-stop)*rr
                        for eb in [36, 69]:
                            ep = simulate(db, j, entry, direction, stop, target, eb)
                            pnl = pnl_calc(entry, ep, direction)
                            hg_results.append({'date':date,'sym':sym,'dir':'LONG','adx':round(adx,1),
                                             'bar':j,'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                    break

            # Downtrend + pullback to EMA
            if closes[-1] < closes[-3]:
                if abs(price - ema9) / price * 100 < 0.1:
                    entry = price; direction = 'SHORT'
                    atr = sum(highs[i]-lows[i] for i in range(max(0,j-5),j+1))/min(6,j+1)
                    stop = ema9 + atr
                    for rr in [1.5, 2.0, 2.5]:
                        target = entry - (stop-entry)*rr
                        for eb in [36, 69]:
                            ep = simulate(db, j, entry, direction, stop, target, eb)
                            pnl = pnl_calc(entry, ep, direction)
                            hg_results.append({'date':date,'sym':sym,'dir':'SHORT','adx':round(adx,1),
                                             'bar':j,'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                    break

print(f'Holy Grail signals: {len(hg_results)}')
for rr in [1.5, 2.0, 2.5]:
    for eb in [36, 69]:
        sub = [r for r in hg_results if r['rr']==rr and r['eb']==eb]
        if len(sub) < 5: continue
        seen = set()
        unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 3. OPENING DRIVE — Prop Desk Pattern ═══
print('\n' + '='*80)
print('3. OPENING DRIVE (First bar is huge = ride the momentum)')
print('='*80, flush=True)
od_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        # First bar (5min) must be large
        bar0 = db[0]
        bar0_range = bar0['high'] - bar0['low']
        bar0_pct = bar0_range / pc * 100
        bar0_body = abs(bar0['close'] - bar0['open'])
        bar0_body_pct = bar0_body / pc * 100

        # Strong opening drive: body > 0.3% and body > 70% of range
        if bar0_body_pct < 0.3 or (bar0_body / bar0_range * 100 < 70 if bar0_range > 0 else True):
            continue

        direction = 'LONG' if bar0['close'] > bar0['open'] else 'SHORT'
        # Enter at close of bar 1 (confirmation)
        if len(db) < 3: continue
        # Bar 1 must continue direction
        if direction == 'LONG' and db[1]['close'] <= db[1]['open']: continue
        if direction == 'SHORT' and db[1]['close'] >= db[1]['open']: continue

        entry = db[1]['close']
        for rr in [1.0, 1.5, 2.0, 2.5]:
            if direction == 'LONG':
                stop = min(bar0['low'], db[1]['low'])
                target = entry + (entry - stop) * rr
            else:
                stop = max(bar0['high'], db[1]['high'])
                target = entry - (stop - entry) * rr
            for eb in [12, 24, 36, 48, 69]:
                ep = simulate(db, 1, entry, direction, stop, target, eb)
                pnl = pnl_calc(entry, ep, direction)
                od_results.append({'date':date,'sym':sym,'dir':direction,'drive_pct':round(bar0_body_pct,2),
                                  'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})

print(f'Opening Drive signals: {len(od_results)}')
for rr in [1.0, 1.5, 2.0, 2.5]:
    for eb in [12, 24, 36, 48, 69]:
        sub = [r for r in od_results if r['rr']==rr and r['eb']==eb]
        if len(sub) < 10: continue
        seen = set()
        unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        if wr >= 48:
            print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')

# Drive size filter
for min_drive in [0.3, 0.5, 0.7, 1.0]:
    sub = [r for r in od_results if r['drive_pct']>=min_drive and r['rr']==1.5 and r['eb']==36]
    if len(sub) < 5: continue
    seen = set()
    unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
    w = sum(r['win'] for r in unique); wr = w/len(unique)*100
    pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
    if wr >= 45:
        print(f'  drive>={min_drive}% R:1.5 exit@36: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 4. INSTITUTIONAL CANDLE — Big body + high volume = smart money ═══
print('\n' + '='*80)
print('4. INSTITUTIONAL CANDLE (large body + volume spike)')
print('='*80, flush=True)
ic_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        avg_vol = sum(b['volume'] for b in db[:6])/6 if len(db) >= 6 else 1
        avg_range = sum(b['high']-b['low'] for b in db[:6])/6 if len(db) >= 6 else 1

        for j in range(3, min(len(db), 20)):
            body = abs(db[j]['close'] - db[j]['open'])
            rng = db[j]['high'] - db[j]['low']
            vol = db[j]['volume']

            # Institutional candle: body > 2x avg range AND volume > 2x avg
            if body < avg_range * 1.5 or vol < avg_vol * 1.5: continue
            if rng == 0: continue
            if body / rng < 0.7: continue  # Must be mostly body, not wicks

            direction = 'LONG' if db[j]['close'] > db[j]['open'] else 'SHORT'
            entry = db[j]['close']
            atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-5),j+1))/min(6,j+1)

            for rr in [1.0, 1.5, 2.0]:
                stop = entry - atr*1.5 if direction=='LONG' else entry + atr*1.5
                target = entry + (entry-stop)*rr if direction=='LONG' else entry - (stop-entry)*rr
                for eb in [36, 48, 69]:
                    ep = simulate(db, j, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    ic_results.append({'date':date,'sym':sym,'dir':direction,'bar':j,
                                      'body_mult':round(body/avg_range,1),'vol_mult':round(vol/avg_vol,1),
                                      'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
            break

print(f'Institutional candle signals: {len(ic_results)}')
for rr in [1.0, 1.5, 2.0]:
    for eb in [36, 48, 69]:
        sub = [r for r in ic_results if r['rr']==rr and r['eb']==eb]
        if len(sub) < 10: continue
        seen = set()
        unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        if wr >= 45:
            print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')

# With size filters
for body_min in [2.0, 2.5, 3.0]:
    for vol_min in [2.0, 2.5, 3.0]:
        sub = [r for r in ic_results if r['body_mult']>=body_min and r['vol_mult']>=vol_min and r['rr']==1.5 and r['eb']==36]
        if len(sub) < 5: continue
        seen = set()
        unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        if wr >= 50:
            print(f'  body>={body_min}x vol>={vol_min}x R:1.5 exit@36: {len(unique):>3} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 5. 3-BAR PLAY — Jeff Cooper ═══
print('\n' + '='*80)
print('5. 3-BAR PLAY (Cooper — NR bar after wide bar = setup)')
print('='*80, flush=True)
tbp_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for j in range(3, min(len(db), 20)):
            # Pattern: bar[j-2] is wide range, bar[j-1] is narrow (inside/NR), bar[j] breaks out
            r2 = db[j-2]['high'] - db[j-2]['low']
            r1 = db[j-1]['high'] - db[j-1]['low']
            r0 = db[j]['high'] - db[j]['low']

            if r2 == 0 or r1 >= r2 * 0.6: continue  # Bar -1 must be significantly narrower
            if r1 == 0: continue

            # Breakout direction: bar[j] closes beyond bar[j-1]'s range
            if db[j]['close'] > db[j-1]['high']:
                direction = 'LONG'; entry = db[j]['close']
                stop = db[j-1]['low']
            elif db[j]['close'] < db[j-1]['low']:
                direction = 'SHORT'; entry = db[j]['close']
                stop = db[j-1]['high']
            else: continue

            risk = abs(entry - stop)
            if risk == 0: continue
            for rr in [1.5, 2.0, 2.5]:
                target = entry + risk*rr if direction=='LONG' else entry - risk*rr
                for eb in [36, 48, 69]:
                    ep = simulate(db, j, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    tbp_results.append({'date':date,'sym':sym,'dir':direction,'bar':j,
                                       'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
            break

print(f'3-Bar Play signals: {len(tbp_results)}')
for rr in [1.5, 2.0, 2.5]:
    for eb in [36, 48, 69]:
        sub = [r for r in tbp_results if r['rr']==rr and r['eb']==eb]
        if len(sub) < 10: continue
        seen = set()
        unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        if wr >= 45:
            print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ GRAND SUMMARY ═══
print('\n' + '='*80)
print('GRAND SUMMARY — Institutional Strategies')
print('='*80)
all_strats = {
    'RSI(2) Connors': rsi2_results,
    'Holy Grail Raschke': hg_results,
    'Opening Drive': od_results,
    'Institutional Candle': ic_results,
    '3-Bar Play Cooper': tbp_results,
}

for name, results in all_strats.items():
    if not results: print(f'  {name}: no signals'); continue
    # Best setup
    best_wr = 0; best = None
    for rr in set(r['rr'] for r in results):
        for eb in set(r['eb'] for r in results):
            sub = [r for r in results if r['rr']==rr and r['eb']==eb]
            if len(sub) < 5: continue
            seen = set()
            unique = [r for r in sub if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            if wr > best_wr:
                best_wr = wr
                best = f'{len(unique)} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%, R:R={rr} exit@{eb}'
    print(f'  {name}: {best}' if best else f'  {name}: no qualifying setup')

print(f'\nTime: {time.time()-t0:.1f}s')
