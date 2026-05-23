"""
Camarilla R3 Daily Picker — we get ~6 signals per day from whitelist.
Which ONE should we pick? Test different selection criteria.
"""
import sys; sys.path.insert(0, '.')
import csv, math
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
                    'close': pdb[-1]['close'],
                }

CAM_WL = {'HDFCBANK','HCLTECH','TITAN','SBIN','NESTLEIND','HEROMOTOCO','WIPRO','UPL','ASIANPAINT','HINDUNILVR'}

def simulate(db, eb, entry, d, stop, target, exit_bar=69):
    for j in range(eb+1, min(len(db), exit_bar+1)):
        if d=='LONG':
            if db[j]['low']<=stop: return stop
            if db[j]['high']>=target: return target
        else:
            if db[j]['high']>=stop: return stop
            if db[j]['low']<=target: return target
    return db[min(exit_bar,len(db)-1)]['close']

def pnl_calc(e,x,d): return (x-e)/e*100 if d=='LONG' else (e-x)/e*100

print(f'{len(all_dates)} days\n')

# Generate ALL Cam signals per day with enrichment
daily_signals = defaultdict(list)

for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pd_data = prev_day_data.get((date, sym))
        pc = prev_close_map.get((date, sym))
        if pd_data is None or len(db) < 30: continue

        h=pd_data['high']; l=pd_data['low']; c=pd_data['close']
        rng=h-l
        if rng==0: continue
        r3=c+rng*1.1/4; r4=c+rng*1.1/2
        s3=c-rng*1.1/4; s4=c-rng*1.1/2

        for j in range(1, min(len(db), 20)):
            atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            tol = atr * 0.3
            entry=None; d=None; stop=None; target=None; level=None

            if abs(db[j]['high']-r3)<tol and db[j]['close']<r3:
                entry=db[j]['close']; d='SHORT'; stop=r4; target=c; level='R3'
            elif abs(db[j]['low']-s3)<tol and db[j]['close']>s3:
                entry=db[j]['close']; d='LONG'; stop=s4; target=c; level='S3'
            if entry is None: continue

            ep = simulate(db, j, entry, d, stop, target, 69)
            pnl = pnl_calc(entry, ep, d)

            # Enrichment for ranking
            gap = (db[0]['open']-pc)/pc*100 if pc else 0
            dir_mult = 1 if d=='LONG' else -1
            morning_move = (entry-db[0]['open'])/db[0]['open']*100
            vol_ratio = db[j]['volume'] / (db[0]['volume']+1)

            # How close to R3/S3 (tighter = better signal)
            if level=='R3':
                proximity = abs(db[j]['high']-r3)/rng*100
            else:
                proximity = abs(db[j]['low']-s3)/rng*100

            # Rejection strength: wick beyond level vs body
            body = abs(db[j]['close']-db[j]['open'])
            wick_beyond = abs(db[j]['high']-r3) if level=='R3' else abs(db[j]['low']-s3)
            rejection = wick_beyond/(body+0.01)

            daily_signals[date].append({
                'sym':sym, 'dir':d, 'level':level, 'entry_bar':j,
                'pnl':round(pnl,4), 'win':pnl>0,
                'is_wl': sym in CAM_WL,
                'gap_adj': round(gap*dir_mult,2),
                'morning_adj': round(morning_move*dir_mult,2),
                'vol_ratio': round(vol_ratio,2),
                'proximity': round(proximity,2),
                'rejection': round(rejection,2),
                'entry_bar_num': j,
                'rng_pct': round(rng/c*100,2),
            })
            break

# Stats
total_sigs = sum(len(v) for v in daily_signals.values())
days_with_sigs = len(daily_signals)
print(f'Total signals: {total_sigs} across {days_with_sigs} days')
print(f'Avg signals/day: {total_sigs/days_with_sigs:.1f}')

# WL only
wl_sigs = {d: [s for s in sigs if s['is_wl']] for d, sigs in daily_signals.items()}
wl_total = sum(len(v) for v in wl_sigs.values())
wl_days = sum(1 for v in wl_sigs.values() if v)
print(f'Whitelist signals: {wl_total} across {wl_days} days, avg {wl_total/wl_days:.1f}/day')

# ═══ DAILY PICKERS ═══
print(f'\n{"="*80}')
print('DAILY PICKERS: Pick 1 trade per day, test different criteria')
print('='*80)

pickers = [
    # From ALL stocks
    ('All stocks, first signal', daily_signals, lambda sigs: min(sigs, key=lambda s: s['entry_bar_num'])),
    ('All stocks, best proximity', daily_signals, lambda sigs: min(sigs, key=lambda s: s['proximity'])),
    ('All stocks, best rejection', daily_signals, lambda sigs: max(sigs, key=lambda s: s['rejection'])),
    ('All stocks, highest vol', daily_signals, lambda sigs: max(sigs, key=lambda s: s['vol_ratio'])),
    ('All stocks, R3 only (first)', daily_signals, lambda sigs: min([s for s in sigs if s['level']=='R3'] or [sigs[0]], key=lambda s: s['entry_bar_num'])),
    # From WHITELIST only
    ('WL only, first signal', wl_sigs, lambda sigs: min(sigs, key=lambda s: s['entry_bar_num'])),
    ('WL only, best proximity', wl_sigs, lambda sigs: min(sigs, key=lambda s: s['proximity'])),
    ('WL only, best rejection', wl_sigs, lambda sigs: max(sigs, key=lambda s: s['rejection'])),
    ('WL only, highest vol', wl_sigs, lambda sigs: max(sigs, key=lambda s: s['vol_ratio'])),
    ('WL only, R3 only (first)', wl_sigs, lambda sigs: min([s for s in sigs if s['level']=='R3'] or [sigs[0]], key=lambda s: s['entry_bar_num'])),
    ('WL only, smallest range%', wl_sigs, lambda sigs: min(sigs, key=lambda s: s['rng_pct'])),
    ('WL only, largest range%', wl_sigs, lambda sigs: max(sigs, key=lambda s: s['rng_pct'])),
    ('WL only, gap aligned', wl_sigs, lambda sigs: max(sigs, key=lambda s: s['gap_adj'])),
    ('WL only, morning aligned', wl_sigs, lambda sigs: max(sigs, key=lambda s: s['morning_adj'])),
]

# Also test: take ALL whitelist signals (portfolio)
print(f"\n{'Picker':>35} {'Days':>5} {'W':>4} {'L':>4} {'WR':>5} {'P&L':>8} {'Avg':>7}")
print('-'*75)

for name, sig_dict, picker_fn in pickers:
    wins=0; total=0; pnl_sum=0
    for date in all_dates:
        sigs = sig_dict.get(date, [])
        if not sigs: continue
        try:
            pick = picker_fn(sigs)
        except: continue
        total += 1
        if pick['win']: wins += 1
        pnl_sum += pick['pnl']
    if total < 10: continue
    wr = wins/total*100
    avg = pnl_sum/total
    marker = ' <<<' if wr >= 65 else ''
    print(f"{name:>35} {total:>5} {wins:>4} {total-wins:>4} {wr:>4.0f}% {pnl_sum:>+7.1f}% {avg:>+6.3f}%{marker}")

# Portfolio: take ALL whitelist signals
print(f'\n{"="*80}')
print('PORTFOLIO: Take ALL whitelist signals each day')
print('='*80)
port_w=0; port_t=0; port_pnl=0; port_days=0
for date in all_dates:
    sigs = wl_sigs.get(date, [])
    if not sigs: continue
    port_days += 1
    for s in sigs:
        port_t += 1
        if s['win']: port_w += 1
        port_pnl += s['pnl']

print(f'  {port_t} trades, {port_days} days, WR={port_w/port_t*100:.1f}%, P&L={port_pnl:+.1f}%')
print(f'  Avg {port_t/port_days:.1f} trades/day, avg P&L/trade={port_pnl/port_t:+.3f}%')

# Compound (equal weight across all daily trades)
capital=100000; bal=capital
for date in sorted(all_dates):
    sigs = wl_sigs.get(date, [])
    if not sigs: continue
    # Split capital equally among signals
    n = len(sigs)
    day_return = sum(s['pnl'] for s in sigs) / n  # Average return
    bal *= (1 + day_return/100)
print(f'  1 Lakh compound (equal split): Rs {bal:,.0f} ({(bal/capital-1)*100:+.1f}%)')

# Yearly for portfolio
print('\n  Yearly:')
yearly = defaultdict(lambda:{'w':0,'l':0,'pnl':0,'days':0})
for date in all_dates:
    sigs = wl_sigs.get(date, [])
    if not sigs: continue
    y = date[:4]
    yearly[y]['days'] += 1
    for s in sigs:
        if s['win']: yearly[y]['w']+=1
        else: yearly[y]['l']+=1
        yearly[y]['pnl']+=s['pnl']
for y in sorted(yearly):
    m=yearly[y]; tot=m['w']+m['l']
    print(f"    {y}: {tot} trades, {m['days']} days, WR={m['w']/tot*100:.0f}%, P&L={m['pnl']:+.1f}%")
