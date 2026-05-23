"""
EXHAUSTIVE v4 — Everything remaining from training.
Pairs trading, DeMark TD9, Squeeze, Donchian, Camarilla,
triangles, flags, time-of-day, round numbers, expiry effects,
simple ML ensemble, and more.
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

def pnl_calc(e, x, d): return (x-e)/e*100 if d=='LONG' else (e-x)/e*100

def dedup(results):
    seen = set()
    return [r for r in results if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]

def report(results, label):
    if not results: print(f'  {label}: 0 signals'); return
    unique = dedup(results)
    w = sum(r['win'] for r in unique); wr = w/len(unique)*100 if unique else 0
    pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
    print(f'  {label}: {len(unique)} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')

t0 = time.time()

# ═══ 1. PAIRS TRADING — Statistical Arbitrage ═══
print('='*80)
print('1. PAIRS TRADING (cointegrated stocks)')
print('='*80, flush=True)

PAIRS = [
    ('TCS', 'INFY'), ('HDFCBANK', 'ICICIBANK'), ('SBIN', 'AXISBANK'),
    ('TATASTEEL', 'JSWSTEEL'), ('HINDALCO', 'TATASTEEL'),
    ('CIPLA', 'SUNPHARMA'), ('TITAN', 'ASIANPAINT'),
    ('RELIANCE', 'ONGC'), ('BPCL', 'ONGC'),
    ('HCLTECH', 'TECHM'), ('WIPRO', 'TECHM'),
    ('MARUTI', 'M&M'), ('BAJAJ-AUTO', 'EICHERMOT'),
]

pairs_results = []
for date in all_dates:
    for sym_a, sym_b in PAIRS:
        db_a = date_bars[date].get(sym_a, [])
        db_b = date_bars[date].get(sym_b, [])
        pc_a = prev_close_map.get((date, sym_a))
        pc_b = prev_close_map.get((date, sym_b))
        if not db_a or not db_b or pc_a is None or pc_b is None: continue
        if len(db_a) < 30 or len(db_b) < 30: continue

        # Compute spread (ratio) over first N bars
        for check_bar in [6, 10]:
            if len(db_a) <= check_bar or len(db_b) <= check_bar: continue

            # Opening ratio
            ratio_open = db_a[0]['open'] / db_b[0]['open'] if db_b[0]['open'] > 0 else 1
            # Current ratio
            ratio_now = db_a[check_bar]['close'] / db_b[check_bar]['close'] if db_b[check_bar]['close'] > 0 else 1

            # Spread divergence
            spread_change = (ratio_now / ratio_open - 1) * 100

            # If spread diverged significantly, trade the convergence
            for min_spread in [0.3, 0.5, 0.8]:
                if abs(spread_change) < min_spread: continue

                if spread_change > 0:
                    # A outperformed B → short A, long B
                    long_sym = sym_b; short_sym = sym_a
                    long_db = db_b; short_db = db_a
                else:
                    long_sym = sym_a; short_sym = sym_b
                    long_db = db_a; short_db = db_b

                # Enter both legs
                long_entry = long_db[check_bar]['close']
                short_entry = short_db[check_bar]['close']
                atr_l = sum(long_db[k]['high']-long_db[k]['low'] for k in range(max(0,check_bar-5),check_bar+1))/min(6,check_bar+1)
                atr_s = sum(short_db[k]['high']-short_db[k]['low'] for k in range(max(0,check_bar-5),check_bar+1))/min(6,check_bar+1)

                for eb in [36, 48, 69]:
                    long_exit = long_db[min(eb, len(long_db)-1)]['close']
                    short_exit = short_db[min(eb, len(short_db)-1)]['close']

                    long_pnl = (long_exit - long_entry) / long_entry * 100
                    short_pnl = (short_entry - short_exit) / short_entry * 100
                    combined_pnl = (long_pnl + short_pnl) / 2  # Market neutral

                    pairs_results.append({
                        'date': date, 'sym': f'{long_sym}+{short_sym}',
                        'spread': round(spread_change, 2), 'min_spread': min_spread,
                        'check_bar': check_bar, 'eb': eb,
                        'pnl': round(combined_pnl, 4), 'win': 1 if combined_pnl > 0 else 0,
                    })

print(f'Pairs signals: {len(pairs_results)}')
for min_spread in [0.3, 0.5, 0.8]:
    for check_bar in [6, 10]:
        for eb in [36, 48, 69]:
            sub = [r for r in pairs_results if r['min_spread']==min_spread and r['check_bar']==check_bar and r['eb']==eb]
            unique = dedup(sub)
            if len(unique) < 10: continue
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            if wr >= 45:
                print(f'  spread>={min_spread}% @bar{check_bar} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 2. DEMARK TD SEQUENTIAL (TD9) ═══
print('\n' + '='*80)
print('2. DEMARK TD SEQUENTIAL')
print('='*80, flush=True)

td_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        # TD Sequential: count consecutive closes higher/lower than close 4 bars ago
        for j in range(8, min(len(db), 25)):
            # Count up (bearish setup): close > close[i-4] for 9 consecutive
            up_count = 0
            for k in range(j, max(j-9, 3), -1):
                if k-4 >= 0 and db[k]['close'] > db[k-4]['close']:
                    up_count += 1
                else: break

            # TD9 sell setup
            if up_count >= 9:
                entry = db[j]['close']; direction = 'SHORT'
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-5),j+1))/min(6,j+1)
                stop = entry + atr * 2
                for rr in [1.0, 1.5, 2.0]:
                    target = entry - (stop-entry)*rr
                    for eb in [36, 69]:
                        ep = simulate(db, j, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        td_results.append({'date':date,'sym':sym,'dir':'SHORT','count':up_count,
                                          'bar':j,'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                break

            # Count down (bullish setup)
            dn_count = 0
            for k in range(j, max(j-9, 3), -1):
                if k-4 >= 0 and db[k]['close'] < db[k-4]['close']:
                    dn_count += 1
                else: break

            if dn_count >= 9:
                entry = db[j]['close']; direction = 'LONG'
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-5),j+1))/min(6,j+1)
                stop = entry - atr * 2
                for rr in [1.0, 1.5, 2.0]:
                    target = entry + (entry-stop)*rr
                    for eb in [36, 69]:
                        ep = simulate(db, j, entry, direction, stop, target, eb)
                        pnl = pnl_calc(entry, ep, direction)
                        td_results.append({'date':date,'sym':sym,'dir':'LONG','count':dn_count,
                                          'bar':j,'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                break

print(f'TD Sequential signals: {len(td_results)}')
for rr in [1.0, 1.5, 2.0]:
    for eb in [36, 69]:
        sub = [r for r in td_results if r['rr']==rr and r['eb']==eb]
        unique = dedup(sub)
        if len(unique) < 5: continue
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 3. SQUEEZE MOMENTUM (BB inside Keltner) ═══
print('\n' + '='*80)
print('3. SQUEEZE MOMENTUM (BB inside Keltner = coiled spring)')
print('='*80, flush=True)

squeeze_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for j in range(10, min(len(db), 25)):
            closes = [b['close'] for b in db[:j+1]]
            highs = [b['high'] for b in db[:j+1]]
            lows = [b['low'] for b in db[:j+1]]
            n = min(10, len(closes))
            if n < 7: continue

            sma = sum(closes[-n:]) / n
            std = math.sqrt(sum((c-sma)**2 for c in closes[-n:])/n)
            atr = sum(highs[i]-lows[i] for i in range(len(closes)-n, len(closes)))/n

            bb_upper = sma + 2*std; bb_lower = sma - 2*std
            kc_upper = sma + 1.5*atr; kc_lower = sma - 1.5*atr

            # Squeeze: BB is INSIDE Keltner Channel
            in_squeeze = bb_lower > kc_lower and bb_upper < kc_upper

            if not in_squeeze: continue

            # Check if squeeze just fired (was in squeeze, momentum crosses zero)
            # Momentum = close - midline of Donchian
            don_mid = (max(highs[-n:]) + min(lows[-n:])) / 2
            mom = closes[-1] - (sma + don_mid) / 2
            prev_mom = closes[-2] - (sum(closes[-n-1:-1])/n + (max(highs[-n-1:-1])+min(lows[-n-1:-1]))/2)/2 if len(closes) > n else 0

            # Momentum crosses above zero = LONG, below = SHORT
            if mom > 0 and prev_mom <= 0:
                direction = 'LONG'
            elif mom < 0 and prev_mom >= 0:
                direction = 'SHORT'
            else: continue

            entry = closes[-1]
            stop = entry - atr*1.5 if direction=='LONG' else entry + atr*1.5
            for rr in [1.5, 2.0, 2.5]:
                target = entry + (entry-stop)*rr if direction=='LONG' else entry - (stop-entry)*rr
                for eb in [36, 48, 69]:
                    ep = simulate(db, j, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    squeeze_results.append({'date':date,'sym':sym,'dir':direction,'bar':j,
                                           'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
            break

print(f'Squeeze signals: {len(squeeze_results)}')
for rr in [1.5, 2.0, 2.5]:
    for eb in [36, 48, 69]:
        sub = [r for r in squeeze_results if r['rr']==rr and r['eb']==eb]
        unique = dedup(sub)
        if len(unique) < 5: continue
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        print(f'  R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 4. CAMARILLA PIVOTS ═══
print('\n' + '='*80)
print('4. CAMARILLA PIVOT LEVELS')
print('='*80, flush=True)

cam_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pd = prev_day_data.get((date, sym))
        if pd is None or len(db) < 30: continue

        h = pd['high']; l = pd['low']; c = pd['close']
        rng = h - l
        # Camarilla levels
        r4 = c + rng * 1.1/2; r3 = c + rng * 1.1/4
        s4 = c - rng * 1.1/2; s3 = c - rng * 1.1/4

        for j in range(1, min(len(db), 20)):
            price = db[j]['close']
            atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)

            # Bounce off S3 → LONG
            if abs(db[j]['low'] - s3) < atr * 0.3 and price > s3:
                entry = price; direction = 'LONG'
                stop = s4; target = c  # Target = prev close (pivot)
                for eb in [36, 48, 69]:
                    ep = simulate(db, j, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    cam_results.append({'date':date,'sym':sym,'dir':'LONG','level':'S3',
                                       'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                break

            # Bounce off R3 → SHORT
            if abs(db[j]['high'] - r3) < atr * 0.3 and price < r3:
                entry = price; direction = 'SHORT'
                stop = r4; target = c
                for eb in [36, 48, 69]:
                    ep = simulate(db, j, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    cam_results.append({'date':date,'sym':sym,'dir':'SHORT','level':'R3',
                                       'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                break

            # Breakout above R4 → LONG (strong trend)
            if price > r4:
                entry = price; direction = 'LONG'
                stop = r3; target = entry + (entry - r3) * 2
                for eb in [36, 48, 69]:
                    ep = simulate(db, j, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    cam_results.append({'date':date,'sym':sym,'dir':'LONG','level':'R4_break',
                                       'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                break

            # Breakdown below S4 → SHORT
            if price < s4:
                entry = price; direction = 'SHORT'
                stop = s3; target = entry - (s3 - entry) * 2
                for eb in [36, 48, 69]:
                    ep = simulate(db, j, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    cam_results.append({'date':date,'sym':sym,'dir':'SHORT','level':'S4_break',
                                       'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                break

print(f'Camarilla signals: {len(cam_results)}')
for level in ['S3', 'R3', 'R4_break', 'S4_break']:
    for eb in [36, 48, 69]:
        sub = [r for r in cam_results if r['level']==level and r['eb']==eb]
        unique = dedup(sub)
        if len(unique) < 5: continue
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        if wr >= 40:
            print(f'  {level} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 5. TIME-OF-DAY WINDOWS ═══
print('\n' + '='*80)
print('5. TIME-OF-DAY PATTERNS')
print('='*80, flush=True)

tod_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 50: continue

        # Lunch reversal: 11:30-12:30 (bars 24-36) often reverses morning trend
        morning_move = (db[18]['close'] - db[0]['open']) / db[0]['open'] * 100 if len(db) > 18 else 0

        if abs(morning_move) > 0.3:
            # Fade the morning move at lunch
            lunch_bar = 24
            if len(db) > lunch_bar:
                direction = 'SHORT' if morning_move > 0 else 'LONG'
                entry = db[lunch_bar]['close']
                atr = sum(db[k]['high']-db[k]['low'] for k in range(lunch_bar-5, lunch_bar+1))/6
                stop = entry + atr*1.5 if direction=='SHORT' else entry - atr*1.5
                for rr in [1.0, 1.5, 2.0]:
                    target = entry - (stop-entry)*rr if direction=='SHORT' else entry + (entry-stop)*rr
                    ep = simulate(db, lunch_bar, entry, direction, stop, target, 69)
                    pnl = pnl_calc(entry, ep, direction)
                    tod_results.append({'date':date,'sym':sym,'dir':direction,'pattern':'lunch_reversal',
                                       'morning_move':round(morning_move,2),'rr':rr,
                                       'pnl':round(pnl,4),'win':1 if pnl>0 else 0})

        # Last hour momentum: 2PM trend continues to close
        if len(db) > 54:
            move_2pm = (db[54]['close'] - db[48]['close']) / db[48]['close'] * 100
            if abs(move_2pm) > 0.2:
                direction = 'LONG' if move_2pm > 0 else 'SHORT'
                entry = db[54]['close']
                atr = sum(db[k]['high']-db[k]['low'] for k in range(49, 55))/6
                stop = entry - atr*1.5 if direction=='LONG' else entry + atr*1.5
                target = entry + atr*2 if direction=='LONG' else entry - atr*2
                ep = simulate(db, 54, entry, direction, stop, target, 69)
                pnl = pnl_calc(entry, ep, direction)
                tod_results.append({'date':date,'sym':sym,'dir':direction,'pattern':'last_hour_momentum',
                                   'rr':1.3,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})

print(f'Time-of-day signals: {len(tod_results)}')
for pattern in ['lunch_reversal', 'last_hour_momentum']:
    sub = [r for r in tod_results if r['pattern']==pattern]
    if pattern == 'lunch_reversal':
        for rr in [1.0, 1.5, 2.0]:
            s = [r for r in sub if r['rr']==rr]
            unique = dedup(s)
            if len(unique) < 10: continue
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            if wr >= 40:
                print(f'  {pattern} R:{rr}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')

        # Strong morning move filter
        for min_move in [0.5, 0.8, 1.0]:
            s = [r for r in sub if abs(r['morning_move'])>=min_move and r['rr']==1.5]
            unique = dedup(s)
            if len(unique) < 5: continue
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            if wr >= 45:
                print(f'  {pattern} move>={min_move}% R:1.5: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')
    else:
        unique = dedup(sub)
        if unique:
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            print(f'  {pattern}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 6. ROUND NUMBER LEVELS ═══
print('\n' + '='*80)
print('6. ROUND NUMBER SUPPORT/RESISTANCE')
print('='*80, flush=True)

round_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        # Find nearest round number (multiples of 50 for stocks > 500, 10 for < 500)
        step = 50 if pc > 500 else (10 if pc > 100 else 5)
        round_above = math.ceil(pc / step) * step
        round_below = math.floor(pc / step) * step

        for j in range(1, min(len(db), 20)):
            atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            tolerance = atr * 0.3

            # Bounce off round number above (resistance → short)
            if abs(db[j]['high'] - round_above) < tolerance and db[j]['close'] < round_above:
                entry = db[j]['close']; direction = 'SHORT'
                stop = round_above + step * 0.3
                target = entry - (stop-entry)*1.5
                for eb in [36, 69]:
                    ep = simulate(db, j, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    round_results.append({'date':date,'sym':sym,'dir':'SHORT','type':'resist',
                                         'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                break

            # Bounce off round number below (support → long)
            if abs(db[j]['low'] - round_below) < tolerance and db[j]['close'] > round_below:
                entry = db[j]['close']; direction = 'LONG'
                stop = round_below - step * 0.3
                target = entry + (entry-stop)*1.5
                for eb in [36, 69]:
                    ep = simulate(db, j, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    round_results.append({'date':date,'sym':sym,'dir':'LONG','type':'support',
                                         'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                break

print(f'Round number signals: {len(round_results)}')
for rtype in ['support', 'resist']:
    for eb in [36, 69]:
        sub = [r for r in round_results if r['type']==rtype and r['eb']==eb]
        unique = dedup(sub)
        if len(unique) < 10: continue
        w = sum(r['win'] for r in unique); wr = w/len(unique)*100
        pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
        print(f'  {rtype} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 7. DONCHIAN CHANNEL BREAKOUT (Turtle Trading) ═══
print('\n' + '='*80)
print('7. DONCHIAN CHANNEL (Turtle Trading adapted for intraday)')
print('='*80, flush=True)

don_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 30: continue

        for period in [6, 10, 15]:
            if len(db) <= period + 10: continue
            don_high = max(b['high'] for b in db[:period])
            don_low = min(b['low'] for b in db[:period])
            don_range = don_high - don_low
            if don_range == 0: continue

            for j in range(period, min(len(db), period + 15)):
                if db[j]['close'] > don_high:
                    entry = db[j]['close']; direction = 'LONG'
                    stop = don_low  # Full channel stop (Turtle style)
                    for rr in [1.0, 1.5, 2.0]:
                        target = entry + (entry-stop)*rr
                        for eb in [36, 69]:
                            ep = simulate(db, j, entry, direction, stop, target, eb)
                            pnl = pnl_calc(entry, ep, direction)
                            don_results.append({'date':date,'sym':sym,'dir':'LONG','period':period,
                                               'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                    break
                if db[j]['close'] < don_low:
                    entry = db[j]['close']; direction = 'SHORT'
                    stop = don_high
                    for rr in [1.0, 1.5, 2.0]:
                        target = entry - (stop-entry)*rr
                        for eb in [36, 69]:
                            ep = simulate(db, j, entry, direction, stop, target, eb)
                            pnl = pnl_calc(entry, ep, direction)
                            don_results.append({'date':date,'sym':sym,'dir':'SHORT','period':period,
                                               'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})
                    break

print(f'Donchian signals: {len(don_results)}')
for period in [6, 10, 15]:
    for rr in [1.0, 1.5, 2.0]:
        for eb in [36, 69]:
            sub = [r for r in don_results if r['period']==period and r['rr']==rr and r['eb']==eb]
            unique = dedup(sub)
            if len(unique) < 10: continue
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            if wr >= 48:
                print(f'  DC-{period} R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ 8. SIMPLE ML ENSEMBLE (majority vote) ═══
print('\n' + '='*80)
print('8. SIMPLE ML ENSEMBLE (majority vote of indicators)')
print('='*80, flush=True)

# For each stock each day, compute 10 simple signals and take majority vote
ml_results = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        pd = prev_day_data.get((date, sym))
        if pc is None or pd is None or len(db) < 20: continue

        check_bar = 6
        if len(db) <= check_bar: continue
        bsf = db[:check_bar+1]
        closes = [b['close'] for b in bsf]
        price = closes[-1]

        votes_long = 0; votes_short = 0

        # 1. Price vs VWAP
        tp_vol = sum((b['high']+b['low']+b['close'])/3*b['volume'] for b in bsf)
        cum_vol = sum(b['volume'] for b in bsf)
        vwap = tp_vol/cum_vol if cum_vol > 0 else price
        if price > vwap: votes_long += 1
        else: votes_short += 1

        # 2. Morning direction
        if price > bsf[0]['open']: votes_long += 1
        else: votes_short += 1

        # 3. Gap direction
        gap = bsf[0]['open'] - pc
        if gap > 0: votes_long += 1
        else: votes_short += 1

        # 4. EMA trend
        ema = sum(closes[-min(5,len(closes)):]) / min(5,len(closes))
        if price > ema: votes_long += 1
        else: votes_short += 1

        # 5. Volume increasing
        if bsf[-1]['volume'] > bsf[0]['volume']: votes_long += 1
        else: votes_short += 1

        # 6. Higher lows
        if bsf[-1]['low'] > bsf[0]['low']: votes_long += 1
        else: votes_short += 1

        # 7. Prev day direction
        if pd['close'] > pd['open']: votes_long += 1
        else: votes_short += 1

        # 8. Body strength
        body = bsf[-1]['close'] - bsf[-1]['open']
        if body > 0: votes_long += 1
        else: votes_short += 1

        # 9. Close near high of range
        day_rng = max(b['high'] for b in bsf) - min(b['low'] for b in bsf)
        if day_rng > 0:
            pos = (price - min(b['low'] for b in bsf)) / day_rng
            if pos > 0.6: votes_long += 1
            else: votes_short += 1

        # 10. Consecutive direction
        consec_up = sum(1 for i in range(1,len(bsf)) if bsf[i]['close'] > bsf[i]['open'])
        if consec_up >= 4: votes_long += 1
        elif consec_up <= 2: votes_short += 1

        total_votes = votes_long + votes_short
        for min_majority in [7, 8, 9]:
            direction = None
            if votes_long >= min_majority: direction = 'LONG'
            elif votes_short >= min_majority: direction = 'SHORT'
            if not direction: continue

            entry = price
            atr = sum(b['high']-b['low'] for b in bsf)/len(bsf)
            stop = entry - atr*1.5 if direction=='LONG' else entry + atr*1.5
            for rr in [1.5, 2.0]:
                target = entry + (entry-stop)*rr if direction=='LONG' else entry - (stop-entry)*rr
                for eb in [36, 69]:
                    ep = simulate(db, check_bar, entry, direction, stop, target, eb)
                    pnl = pnl_calc(entry, ep, direction)
                    ml_results.append({'date':date,'sym':sym,'dir':direction,
                                      'votes':max(votes_long,votes_short),'min_majority':min_majority,
                                      'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0})

print(f'ML ensemble signals: {len(ml_results)}')
for min_maj in [7, 8, 9]:
    for rr in [1.5, 2.0]:
        for eb in [36, 69]:
            sub = [r for r in ml_results if r['min_majority']==min_maj and r['rr']==rr and r['eb']==eb]
            unique = dedup(sub)
            if len(unique) < 5: continue
            w = sum(r['win'] for r in unique); wr = w/len(unique)*100
            pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
            if wr >= 40:
                print(f'  {min_maj}/10 votes R:{rr} exit@{eb}: {len(unique):>4} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')


# ═══ GRAND SUMMARY ═══
print('\n' + '='*80)
print('GRAND SUMMARY — All Remaining Strategies')
print('='*80)
all_strats = {
    'Pairs Trading': pairs_results,
    'DeMark TD9': td_results,
    'Squeeze Momentum': squeeze_results,
    'Camarilla Pivots': cam_results,
    'Time-of-Day': tod_results,
    'Round Numbers': round_results,
    'Donchian Channel': don_results,
    'ML Ensemble': ml_results,
}

for name, results in all_strats.items():
    if not results: print(f'  {name}: 0 signals'); continue
    unique = dedup(results)
    w = sum(r['win'] for r in unique); wr = w/len(unique)*100
    pnl = sum(r['pnl'] for r in unique); days = len(set(r['date'] for r in unique))
    print(f'  {name}: {len(unique)} trades, {days} days, WR={wr:.0f}%, P&L={pnl:+.1f}%')

print(f'\nTime: {time.time()-t0:.1f}s')
