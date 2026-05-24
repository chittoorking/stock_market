"""
COMPREHENSIVE TUNING — Test every parameter combination.
Current: Target 1.50%, Stop 1.00%, ScanBar 10, Exit EOD, All days, All stocks.
"""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict
from datetime import datetime as dt

# Load
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

all_dates=sorted(date_bars.keys())
prev_day={}; daily_trend={}
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars)); dc=[]
    for i,d in enumerate(dfs):
        db=date_bars[d].get(sym,[])
        if not db: continue
        c=db[-1]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[dfs[i-1]].get(sym,[])
            if pdb: prev_day[(d,sym)]={'high':max(b['high'] for b in pdb),'low':min(b['low'] for b in pdb),'close':pdb[-1]['close']}
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_trend[(d,sym)]='UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'

# Load VIX
vix_data={}
vf=Path('data/vix_daily.csv')
if vf.exists():
    with open(vf) as f:
        for r in csv.DictReader(f): vix_data[r['date']]=float(r['close'])

# Pre-compute day of week for each date
date_dow={}
for d in all_dates:
    date_dow[d]=dt.strptime(d,'%Y-%m-%d').weekday()

POS=1000000; CHARGES=386
print(f'{len(all_data)} stocks, {len(all_dates)} days\n')

def get_signals(scan_bar):
    """Pre-compute all signals for a given scan_bar."""
    signals=[]
    for date in all_dates:
        for sym in date_bars[date]:
            if sym in ('NIFTY_50','NIFTY_BANK'): continue
            db=date_bars[date][sym]
            if len(db)<=scan_bar+20: continue
            if daily_trend.get((date,sym))!='DOWN': continue
            pd=prev_day.get((date,sym))
            if not pd: continue
            rng=pd['high']-pd['low']
            if rng<=0: continue
            r3=pd['close']+rng*1.1/4
            triggered=False
            for j in range(1,scan_bar+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                    triggered=True; break
            if not triggered: continue
            signals.append({'date':date,'sym':sym,'entry':db[scan_bar]['close'],'scan_bar':scan_bar})
    return signals

def test_config(signals, target, stop, exit_bar, day_filter=None, stock_filter=None,
                max_per_day=99, vix_lo=0, vix_hi=99):
    """Test a parameter combination."""
    trades=[]; daily_count=defaultdict(int)
    for s in signals:
        date=s['date']; sym=s['sym']
        if day_filter and date_dow[date] not in day_filter: continue
        if stock_filter and sym not in stock_filter: continue
        if daily_count[date]>=max_per_day: continue
        if vix_data:
            vix=vix_data.get(date,15)
            if vix<vix_lo or vix>vix_hi: continue

        db=date_bars[date][sym]; entry=s['entry']; sb=s['scan_bar']
        tp=entry*(1-target/100); sp=entry*(1+stop/100)
        eb=min(exit_bar, len(db)-1)
        ep=db[eb]['close']
        for k in range(sb+1, eb+1):
            if db[k]['low']<=tp: ep=tp; break
            if db[k]['high']>=sp: ep=sp; break
        pnl=(entry-ep)/entry*100/100*POS - CHARGES
        trades.append(pnl)
        daily_count[date]+=1

    if len(trades)<50: return None
    n=len(trades); w=sum(1 for t in trades if t>0)
    total=sum(trades)
    # Max drawdown
    cum=0;peak=0;dd=0
    for t in trades: cum+=t; peak=max(peak,cum); dd=max(dd,peak-cum)
    return {'n':n,'w':w,'wr':w/n*100,'total':total,'per':total/n,'dd':dd}

# Pre-compute signals for different scan bars
print('Pre-computing signals...', flush=True)
sig_cache={}
for sb in [5, 8, 10, 12, 15]:
    sig_cache[sb]=get_signals(sb)
    print(f'  ScanBar {sb}: {len(sig_cache[sb])} signals')

# ═══════════════════════════════════════════════════════════════
# TEST 1: TARGET + STOP grid (most important)
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST 1: TARGET × STOP grid (scan_bar=10, exit=EOD)')
print('Current: T=1.50% S=1.00%')
print('='*90)

results=[]
for t in [0.75, 1.00, 1.25, 1.50, 1.75, 2.00, 2.50]:
    for s in [0.50, 0.75, 1.00, 1.25, 1.50, 2.00]:
        r=test_config(sig_cache[10], t, s, 69)
        if r:
            rr=t/s  # Risk-reward ratio
            marker=' <<<' if r['per']>3500 else ' <' if r['per']>3000 else ''
            results.append((t,s,r))
            print(f'  T={t:.2f}% S={s:.2f}% R:R={rr:.1f}: {r["n"]:>5} trades, WR={r["wr"]:.0f}%, '
                  f'Rs {r["per"]:>+7,.0f}/trade, Total Rs {r["total"]:>+12,.0f}, DD Rs {r["dd"]:>8,.0f}{marker}')

# Find best
best=max(results, key=lambda x: x[2]['per'])
print(f'\n  BEST: T={best[0]}% S={best[1]}% → Rs {best[2]["per"]:+,.0f}/trade')

# ═══════════════════════════════════════════════════════════════
# TEST 2: SCAN BAR
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST 2: SCAN BAR (using best T/S from above)')
print('='*90)

bt, bs = best[0], best[1]
for sb in [5, 8, 10, 12, 15]:
    r=test_config(sig_cache[sb], bt, bs, 69)
    if r:
        marker=' <<<' if r['per']>3500 else ''
        print(f'  ScanBar {sb:>2} ({sb*5} min): {r["n"]:>5} trades, WR={r["wr"]:.0f}%, '
              f'Rs {r["per"]:>+7,.0f}/trade, Total Rs {r["total"]:>+12,.0f}{marker}')

# ═══════════════════════════════════════════════════════════════
# TEST 3: EXIT TIME
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST 3: EXIT TIME (when to close if no target/stop hit)')
print('='*90)

for eb in [30, 35, 40, 45, 50, 55, 60, 65, 69]:
    h=9+(eb*5)//60; m=(eb*5)%60+15
    if m>=60: h+=1; m-=60
    r=test_config(sig_cache[10], bt, bs, eb)
    if r:
        marker=' <<<' if r['per']>3500 else ''
        print(f'  Exit {h}:{m:02d} (bar {eb:>2}): {r["n"]:>5} trades, WR={r["wr"]:.0f}%, '
              f'Rs {r["per"]:>+7,.0f}/trade, Total Rs {r["total"]:>+12,.0f}{marker}')

# ═══════════════════════════════════════════════════════════════
# TEST 4: DAY OF WEEK
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST 4: DAY OF WEEK filter')
print('='*90)

day_combos=[
    (None, 'All days'),
    ({0,1,2,3}, 'Mon-Thu (skip Fri)'),
    ({0,1,2,3,4}, 'Mon-Fri (all)'),
    ({1,2,3}, 'Tue-Thu'),
    ({0,1,2}, 'Mon-Wed'),
    ({1,2,3,4}, 'Tue-Fri (skip Mon)'),
]
for df, label in day_combos:
    r=test_config(sig_cache[10], bt, bs, 69, day_filter=df)
    if r:
        marker=' <<<' if r['per']>3500 else ''
        print(f'  {label:>25}: {r["n"]:>5} trades, WR={r["wr"]:.0f}%, '
              f'Rs {r["per"]:>+7,.0f}/trade, Total Rs {r["total"]:>+12,.0f}{marker}')

# Per-day performance
print(f'\n  Individual day performance:')
for day_idx, day_name in enumerate(['Mon','Tue','Wed','Thu','Fri']):
    r=test_config(sig_cache[10], bt, bs, 69, day_filter={day_idx})
    if r:
        print(f'    {day_name}: {r["n"]:>5} trades, WR={r["wr"]:.0f}%, Rs {r["per"]:>+7,.0f}/trade')

# ═══════════════════════════════════════════════════════════════
# TEST 5: MAX TRADES PER DAY
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST 5: MAX TRADES PER DAY')
print('='*90)

for mpd in [1, 2, 3, 5, 10, 99]:
    r=test_config(sig_cache[10], bt, bs, 69, max_per_day=mpd)
    if r:
        marker=' <<<' if r['per']>3500 else ''
        print(f'  Max {mpd:>2}/day: {r["n"]:>5} trades, WR={r["wr"]:.0f}%, '
              f'Rs {r["per"]:>+7,.0f}/trade, Total Rs {r["total"]:>+12,.0f}, DD Rs {r["dd"]:>8,.0f}{marker}')

# ═══════════════════════════════════════════════════════════════
# TEST 6: VIX FILTER
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST 6: VIX FILTER (trade only in certain VIX ranges)')
print('='*90)

if vix_data:
    for vlo, vhi in [(0,99),(10,20),(12,18),(10,15),(15,20),(15,25),(20,30),(0,15),(15,99)]:
        r=test_config(sig_cache[10], bt, bs, 69, vix_lo=vlo, vix_hi=vhi)
        if r:
            marker=' <<<' if r['per']>3500 else ''
            print(f'  VIX {vlo:>2}-{vhi:>2}: {r["n"]:>5} trades, WR={r["wr"]:.0f}%, '
                  f'Rs {r["per"]:>+7,.0f}/trade, Total Rs {r["total"]:>+12,.0f}{marker}')

# ═══════════════════════════════════════════════════════════════
# TEST 7: STOCK FILTER (per-stock WR)
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST 7: PER-STOCK PERFORMANCE')
print('='*90)

stock_stats=defaultdict(lambda:{'n':0,'w':0,'pnl':0})
for s in sig_cache[10]:
    date=s['date']; sym=s['sym']
    db=date_bars[date][sym]; entry=s['entry']
    tp=entry*(1-bt/100); sp=entry*(1+bs/100)
    ep=db[min(69,len(db)-1)]['close']
    for k in range(11, min(len(db),70)):
        if db[k]['low']<=tp: ep=tp; break
        if db[k]['high']>=sp: ep=sp; break
    pnl=(entry-ep)/entry*100/100*POS - CHARGES
    stock_stats[sym]['n']+=1
    stock_stats[sym]['pnl']+=pnl
    if pnl>0: stock_stats[sym]['w']+=1

print(f'\n  {"Stock":>12} {"Trades":>7} {"WR":>5} {"Total":>12} {"/trade":>8}')
print(f'  {"-"*50}')
sorted_stocks=sorted(stock_stats.items(), key=lambda x: x[1]['pnl']/x[1]['n'] if x[1]['n']>20 else -9999, reverse=True)
good_stocks=set(); bad_stocks=set()
for sym, st in sorted_stocks:
    if st['n']<20: continue
    wr=st['w']/st['n']*100
    per=st['pnl']/st['n']
    marker=' <<<' if per>5000 else ' BAD' if per<0 else ''
    if per>3000: good_stocks.add(sym)
    if per<0: bad_stocks.add(sym)
    print(f'  {sym:>12} {st["n"]:>7} {wr:>4.0f}% Rs {st["pnl"]:>+10,.0f} Rs {per:>+7,.0f}{marker}')

# Test with good stocks only
print(f'\n  Good stocks ({len(good_stocks)}): {", ".join(sorted(good_stocks)[:10])}...')
r_good=test_config(sig_cache[10], bt, bs, 69, stock_filter=good_stocks)
r_no_bad=test_config(sig_cache[10], bt, bs, 69, stock_filter=set(stock_stats.keys())-bad_stocks)
r_all=test_config(sig_cache[10], bt, bs, 69)

if r_good: print(f'  Good stocks only: {r_good["n"]:>5} trades, WR={r_good["wr"]:.0f}%, Rs {r_good["per"]:>+7,.0f}/trade, Total Rs {r_good["total"]:>+12,.0f}')
if r_no_bad: print(f'  Remove bad stocks: {r_no_bad["n"]:>5} trades, WR={r_no_bad["wr"]:.0f}%, Rs {r_no_bad["per"]:>+7,.0f}/trade, Total Rs {r_no_bad["total"]:>+12,.0f}')
if r_all: print(f'  All stocks:       {r_all["n"]:>5} trades, WR={r_all["wr"]:.0f}%, Rs {r_all["per"]:>+7,.0f}/trade, Total Rs {r_all["total"]:>+12,.0f}')

# ═══════════════════════════════════════════════════════════════
# FINAL: BEST COMBOS
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('BEST COMBINATIONS')
print('='*90)

combos=[
    (10, bt, bs, 69, None, None, 99, 0, 99, f'CURRENT BEST (T={bt} S={bs})'),
    (10, 1.50, 1.00, 69, None, None, 99, 0, 99, 'ORIGINAL (T=1.50 S=1.00)'),
    (10, bt, bs, 69, {0,1,2,3}, None, 99, 0, 99, f'Best T/S + Skip Fri'),
    (10, bt, bs, 69, None, None, 5, 0, 99, f'Best T/S + Max 5/day'),
    (10, bt, bs, 69, None, set(stock_stats.keys())-bad_stocks, 99, 0, 99, f'Best T/S + Remove bad stocks'),
    (10, bt, bs, 69, {0,1,2,3}, set(stock_stats.keys())-bad_stocks, 5, 0, 99, 'Best T/S + Skip Fri + No bad + Max 5'),
    (10, bt, bs, 69, None, None, 99, 12, 20, f'Best T/S + VIX 12-20'),
    (10, bt, bs, 69, {0,1,2,3}, set(stock_stats.keys())-bad_stocks, 99, 0, 99, 'Best T/S + Skip Fri + No bad'),
]

results_final=[]
for sb, t, s, eb, df, sf, mpd, vlo, vhi, label in combos:
    r=test_config(sig_cache[sb], t, s, eb, day_filter=df, stock_filter=sf, max_per_day=mpd, vix_lo=vlo, vix_hi=vhi)
    if r:
        results_final.append((label, r))

results_final.sort(key=lambda x: x[1]['per'], reverse=True)
print(f'\n{"#":>3} {"Config":>55} {"N":>6} {"WR":>4} {"Total":>14} {"/trade":>8} {"DD":>10}')
print('-'*105)
for i,(label,r) in enumerate(results_final,1):
    marker=' <<<' if i<=3 else ''
    print(f'{i:>3} {label:>55} {r["n"]:>6} {r["wr"]:>3.0f}% Rs {r["total"]:>+12,.0f} Rs {r["per"]:>+7,.0f} Rs {r["dd"]:>8,.0f}{marker}')
