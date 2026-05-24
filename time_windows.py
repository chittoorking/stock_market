"""
TIME WINDOW STRATEGY: Split the day into 1-hour windows.
In each window, find the stock making the biggest move.
Ride it for the next window. Multiple entries per day.
"""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

data_dir=Path('data/5min'); all_data={}; date_bars=defaultdict(dict)
print('Loading...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym=f.stem.split('_')[0].upper()
    if sym in ('NIFTY_50','NIFTY_BANK'): continue
    with open(f) as fh: rows=list(csv.DictReader(fh))
    bars=[{'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
           'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))} for r in rows]
    all_data[sym]=bars
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in by_d.items(): date_bars[d][sym]=bs

all_dates=sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))
print('Done.\n', flush=True)

windows = [
    ('W1 9:30-10:30', 3, 15),    # First hour
    ('W2 10:30-11:30', 15, 27),   # Second hour
    ('W3 11:30-12:30', 27, 39),   # Third hour
    ('W4 12:30-1:30', 39, 51),    # Fourth hour
    ('W5 1:30-2:30', 51, 63),     # Fifth hour
]

print('TIME WINDOW STRATEGY')
print('For each 1-hour window: pick the stock moving most, ride it next hour')
print('='*100)

for wname, scan_start, scan_end in windows:
    # How much runway is left?
    exit_bar = min(scan_end + 12, 69)  # Trade for next 1 hour or till 3PM

    results = []
    for date in all_dates[-200:]:
        candidates = []
        for sym in date_bars[date]:
            db = date_bars[date][sym]
            if len(db) <= exit_bar: continue

            # Move within this window
            wm = (db[scan_end]['close'] - db[scan_start]['close']) / db[scan_start]['close'] * 100
            if abs(wm) < 0.3: continue  # Skip tiny

            # Acceleration
            mid = scan_start + (scan_end-scan_start)//2
            first = abs(db[mid]['close'] - db[scan_start]['close'])
            second = abs(db[scan_end]['close'] - db[mid]['close'])
            accel = second > first * 1.2

            # Volume trend in window
            vol_first = sum(b['volume'] for b in db[scan_start:mid+1])
            vol_second = sum(b['volume'] for b in db[mid+1:scan_end+1])
            vol_rising = vol_second > vol_first

            candidates.append({
                'sym': sym, 'wm': wm, 'accel': accel, 'vol_rising': vol_rising,
                'dir': 'LONG' if wm > 0 else 'SHORT',
            })

        if not candidates: continue

        # Pick top mover
        candidates.sort(key=lambda x: -abs(x['wm']))
        pick = candidates[0]

        sym = pick['sym']; d = pick['dir']
        db = date_bars[date][sym]
        entry = db[scan_end]['close']
        atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,scan_end-5),scan_end+1))/min(6,scan_end+1)
        stop = entry - atr*2 if d=='LONG' else entry + atr*2
        target = entry + atr*2*2.5 if d=='LONG' else entry - atr*2*2.5

        ep = db[min(exit_bar,len(db)-1)]['close']
        mfe = 0
        for j in range(scan_end+1, min(len(db), exit_bar+1)):
            fav = (db[j]['high']-entry)/entry*100 if d=='LONG' else (entry-db[j]['low'])/entry*100
            mfe = max(mfe, fav)
            if d=='LONG' and db[j]['low']<=stop: ep=stop; break
            if d=='SHORT' and db[j]['high']>=stop: ep=stop; break
            if d=='LONG' and db[j]['high']>=target: ep=target; break
            if d=='SHORT' and db[j]['low']<=target: ep=target; break

        pnl = (ep-entry)/entry*100 if d=='LONG' else (entry-ep)/entry*100
        results.append({'pnl': pnl, 'win': pnl>0, 'mfe': mfe, 'accel': pick['accel'], 'vol_rising': pick['vol_rising']})

    if not results: continue

    w=sum(r['win'] for r in results); n=len(results)
    gross=sum(r['pnl'] for r in results); charges=0.0835*n; net=gross-charges
    avg_mfe=sum(r['mfe'] for r in results)/n

    # With acceleration filter
    acc=[r for r in results if r['accel']]
    if acc:
        aw=sum(r['win'] for r in acc); an=len(acc)
        ag=sum(r['pnl'] for r in acc); anet=ag-0.0835*an
    else:
        aw=an=0; ag=anet=0

    # With vol+accel
    va=[r for r in results if r['accel'] and r['vol_rising']]
    if va:
        vw=sum(r['win'] for r in va); vn=len(va)
        vg=sum(r['pnl'] for r in va); vnet=vg-0.0835*vn
    else:
        vw=vn=0; vg=vnet=0

    print(f'\n{wname}:')
    print(f'  ALL:      {n:>3} trades, WR={w/n*100:.0f}%, gross={gross:+.1f}%, net={net:+.1f}%, avg MFE={avg_mfe:.2f}%')
    if an>=5:
        print(f'  +ACCEL:   {an:>3} trades, WR={aw/an*100:.0f}%, gross={ag:+.1f}%, net={anet:+.1f}%')
    if vn>=5:
        print(f'  +ACCEL+VOL:{vn:>3} trades, WR={vw/vn*100:.0f}%, gross={vg:+.1f}%, net={vnet:+.1f}%')


# NOW: Multi-window strategy — trade ALL windows, compound
print(f'\n{"="*100}')
print('MULTI-WINDOW: Trade best mover from EACH window (up to 5 trades/day)')
print('='*100)

capital = 100000
total_trades = 0; total_wins = 0; total_pnl = 0
daily_pnls = []

for date in all_dates[-200:]:
    day_pnl = 0; day_trades = 0
    for wname, scan_start, scan_end in windows:
        exit_bar = min(scan_end + 12, 69)
        candidates = []
        for sym in date_bars[date]:
            db = date_bars[date][sym]
            if len(db) <= exit_bar: continue
            wm = (db[scan_end]['close'] - db[scan_start]['close']) / db[scan_start]['close'] * 100
            if abs(wm) < 0.5: continue  # Higher threshold
            mid = scan_start + (scan_end-scan_start)//2
            first = abs(db[mid]['close'] - db[scan_start]['close'])
            second = abs(db[scan_end]['close'] - db[mid]['close'])
            accel = second > first * 1.2
            if not accel: continue  # Only accelerating
            candidates.append({'sym': sym, 'wm': wm, 'dir': 'LONG' if wm > 0 else 'SHORT'})

        if not candidates: continue
        candidates.sort(key=lambda x: -abs(x['wm']))
        pick = candidates[0]
        sym = pick['sym']; d = pick['dir']
        db = date_bars[date][sym]
        entry = db[scan_end]['close']
        atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,scan_end-5),scan_end+1))/min(6,scan_end+1)
        stop = entry - atr*2 if d=='LONG' else entry + atr*2
        target = entry + atr*2*2.5 if d=='LONG' else entry - atr*2*2.5

        ep = db[min(exit_bar,len(db)-1)]['close']
        for j in range(scan_end+1, min(len(db), exit_bar+1)):
            if d=='LONG' and db[j]['low']<=stop: ep=stop; break
            if d=='SHORT' and db[j]['high']>=stop: ep=stop; break
            if d=='LONG' and db[j]['high']>=target: ep=target; break
            if d=='SHORT' and db[j]['low']<=target: ep=target; break

        pnl = (ep-entry)/entry*100 if d=='LONG' else (entry-ep)/entry*100
        day_pnl += pnl; day_trades += 1
        total_trades += 1
        if pnl > 0: total_wins += 1
        total_pnl += pnl

    if day_trades > 0:
        daily_pnls.append(day_pnl)
        capital *= (1 + (day_pnl - 0.0835*day_trades) / 100)

print(f'Total: {total_trades} trades over {len(daily_pnls)} trading days')
print(f'WR: {total_wins}/{total_trades} = {total_wins/total_trades*100:.0f}%')
print(f'Gross: {total_pnl:+.1f}% | Charges: {0.0835*total_trades:.1f}% | Net: {total_pnl-0.0835*total_trades:+.1f}%')
print(f'Avg trades/day: {total_trades/len(daily_pnls):.1f}')
print(f'Rs 1L -> Rs {capital:,.0f}')

# Green/red days
green = sum(1 for p in daily_pnls if p > 0)
print(f'Green days: {green}/{len(daily_pnls)} ({green/len(daily_pnls)*100:.0f}%)')
