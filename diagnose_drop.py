"""
DIAGNOSE: Why did strategies drop from 70% to 48% on 4-year data?
Possible causes:
1. Whitelist overfit to 6 months
2. Market regime changed (2022-2023 was bear/choppy)
3. Strategy parameters tuned to specific conditions
4. Not enough volume data pre-2025

Find: which stocks, which periods, which conditions cause losses.
"""
import sys; sys.path.insert(0, '.')
import csv, math, time
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

print(f'{len(all_data)} stocks, {len(all_dates)} days\n')

def simulate(db, eb, entry, d, stop, target, exit_bar=36):
    for j in range(eb+1, min(len(db), exit_bar+1)):
        if d=='LONG':
            if db[j]['low']<=stop: return stop
            if db[j]['high']>=target: return target
        else:
            if db[j]['high']>=stop: return stop
            if db[j]['low']<=target: return target
    return db[min(exit_bar,len(db)-1)]['close']

def pnl_calc(e,x,d): return (x-e)/e*100 if d=='LONG' else (e-x)/e*100

WHITELIST_OLD = {'ULTRACEMCO','EICHERMOT','M&M','TATASTEEL','TECHM','SBILIFE','NTPC',
                 'ADANIPORTS','ICICIBANK','SUNPHARMA','TITAN','WIPRO','DIVISLAB','COALINDIA'}

# ═══ 1. RUN ORB ON ALL STOCKS (not just whitelist) and find per-stock WR across all 4 years ═══
print('='*80)
print('1. PER-STOCK ORB PERFORMANCE (4 years, vol confirmed)')
print('='*80, flush=True)

stock_trades = defaultdict(list)
all_orb = []

for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date, sym))
        if pc is None or len(db) < 40: continue
        rh=db[0]['high']; rl=db[0]['low']; rs=rh-rl
        if rs==0: continue
        for j in range(1, min(len(db), 12)):
            d=None
            if db[j]['close']>rh: d='LONG'
            elif db[j]['close']<rl: d='SHORT'
            if not d: continue
            if db[0]['volume']>0 and db[j]['volume']<db[0]['volume']*1.2: break
            entry=db[j]['close']; stop=rl if d=='LONG' else rh
            risk=abs(entry-stop)
            if risk==0: break
            target=entry+risk*2 if d=='LONG' else entry-risk*2
            ep=simulate(db,j,entry,d,stop,target,36)
            pnl=pnl_calc(entry,ep,d)
            trade = {'date':date,'sym':sym,'pnl':round(pnl,4),'win':pnl>0,'dir':d}
            stock_trades[sym].append(trade)
            all_orb.append(trade)
            break

# Sort stocks by 4-year WR
stock_stats = []
for sym in sorted(all_data.keys()):
    trades = stock_trades[sym]
    if len(trades) < 10: continue
    w = sum(t['win'] for t in trades)
    wr = w/len(trades)*100
    pnl = sum(t['pnl'] for t in trades)
    # Also check per-year consistency
    yearly = defaultdict(lambda: {'w':0,'t':0})
    for t in trades:
        y = t['date'][:4]
        yearly[y]['t'] += 1
        if t['win']: yearly[y]['w'] += 1
    consistent = sum(1 for y in yearly if yearly[y]['t']>=5 and yearly[y]['w']/yearly[y]['t']>=0.5)
    years_with_data = len([y for y in yearly if yearly[y]['t']>=5])
    stock_stats.append({
        'sym':sym, 'n':len(trades), 'wr':wr, 'pnl':pnl,
        'consistent':consistent, 'years':years_with_data,
        'in_old_wl': sym in WHITELIST_OLD,
        'yearly': {y: round(v['w']/v['t']*100) if v['t']>=5 else -1 for y,v in yearly.items()},
    })

stock_stats.sort(key=lambda x: -x['wr'])
print(f"\n{'Sym':>12} {'N':>4} {'WR':>5} {'P&L':>7} {'Con':>4} {'OldWL':>6} | {'2022':>5} {'2023':>5} {'2024':>5} {'2025':>5} {'2026':>5}")
print('-'*85)
for s in stock_stats:
    wl = 'YES' if s['in_old_wl'] else ''
    y22 = f"{s['yearly'].get('2022',-1):>4}%" if s['yearly'].get('2022',-1)>=0 else '   -'
    y23 = f"{s['yearly'].get('2023',-1):>4}%" if s['yearly'].get('2023',-1)>=0 else '   -'
    y24 = f"{s['yearly'].get('2024',-1):>4}%" if s['yearly'].get('2024',-1)>=0 else '   -'
    y25 = f"{s['yearly'].get('2025',-1):>4}%" if s['yearly'].get('2025',-1)>=0 else '   -'
    y26 = f"{s['yearly'].get('2026',-1):>4}%" if s['yearly'].get('2026',-1)>=0 else '   -'
    marker = ' <<<' if s['wr']>=55 and s['consistent']>=3 else ''
    print(f"  {s['sym']:>12} {s['n']:>4} {s['wr']:>4.0f}% {s['pnl']:>+6.1f}% {s['consistent']:>4}/{s['years']} {wl:>6} | {y22} {y23} {y24} {y25} {y26}{marker}")

# ═══ 2. BUILD NEW WHITELIST from 4 years ═══
print(f'\n{"="*80}')
print('2. NEW WHITELIST (WR >= 55% AND consistent >= 3 years)')
print('='*80)
new_wl = [s['sym'] for s in stock_stats if s['wr'] >= 55 and s['consistent'] >= 3]
old_wl_performance = [s for s in stock_stats if s['in_old_wl']]
print(f'\nOld whitelist stocks on 4 years:')
for s in old_wl_performance:
    verdict = 'KEEP' if s['wr']>=55 and s['consistent']>=3 else 'DROP'
    print(f"  {s['sym']:>12}: WR={s['wr']:.0f}%, consistent {s['consistent']}/{s['years']} years -> {verdict}")

print(f'\nNEW whitelist ({len(new_wl)} stocks): {new_wl}')
dropped = [s['sym'] for s in old_wl_performance if s['wr']<55 or s['consistent']<3]
added = [s for s in new_wl if s not in WHITELIST_OLD]
print(f'Dropped from old: {dropped}')
print(f'New additions: {added}')

# ═══ 3. TEST ORB with NEW whitelist ═══
print(f'\n{"="*80}')
print('3. ORB + NEW WHITELIST performance')
print('='*80)
new_wl_set = set(new_wl)
orb_new_wl = [t for t in all_orb if t['sym'] in new_wl_set]
# Dedup by date+sym
seen = set()
orb_new_unique = [t for t in orb_new_wl if (t['date'],t['sym']) not in seen and not seen.add((t['date'],t['sym']))]
w = sum(t['win'] for t in orb_new_unique)
wr = w/len(orb_new_unique)*100
pnl = sum(t['pnl'] for t in orb_new_unique)
days = len(set(t['date'] for t in orb_new_unique))
print(f'New WL: {len(orb_new_unique)} trades, {days} days, WR={wr:.1f}%, P&L={pnl:+.1f}%')

# Yearly
yearly = defaultdict(lambda:{'w':0,'l':0,'pnl':0})
for t in orb_new_unique:
    y=t['date'][:4]
    if t['win']: yearly[y]['w']+=1
    else: yearly[y]['l']+=1
    yearly[y]['pnl']+=t['pnl']
for y in sorted(yearly):
    m=yearly[y]; tot=m['w']+m['l']
    print(f"  {y}: {tot} trades, WR={m['w']/tot*100:.0f}%, P&L={m['pnl']:+.1f}%")

# Compound
capital=100000; bal=capital
for t in sorted(orb_new_unique, key=lambda x:x['date']):
    bal *= (1+t['pnl']/100)
print(f'  1 Lakh compound: Rs {bal:,.0f} ({(bal/capital-1)*100:+.1f}%)')

# ═══ 4. WHAT MAKES CAMARILLA SO ROBUST? ═══
print(f'\n{"="*80}')
print('4. WHY CAMARILLA R3 WORKS EVERYWHERE')
print('='*80)

# Check Cam R3 per stock
cam_stock = defaultdict(list)
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pd_data = prev_day_data.get((date,sym))
        if pd_data is None or len(db)<30: continue
        h=pd_data['high'];l=pd_data['low'];c=pd_data['close']
        rng=h-l
        if rng==0: continue
        r3=c+rng*1.1/4; r4=c+rng*1.1/2
        s3=c-rng*1.1/4; s4=c-rng*1.1/2
        for j in range(1, min(len(db), 20)):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            tol=atr*0.3
            if abs(db[j]['high']-r3)<tol and db[j]['close']<r3:
                entry=db[j]['close']; stop=r4; target=c
                ep=simulate(db,j,entry,'SHORT',stop,target,69)
                pnl=pnl_calc(entry,ep,'SHORT')
                cam_stock[sym].append({'date':date,'pnl':pnl,'win':pnl>0,'level':'R3'})
                break
            if abs(db[j]['low']-s3)<tol and db[j]['close']>s3:
                entry=db[j]['close']; stop=s4; target=c
                ep=simulate(db,j,entry,'LONG',stop,target,69)
                pnl=pnl_calc(entry,ep,'LONG')
                cam_stock[sym].append({'date':date,'pnl':pnl,'win':pnl>0,'level':'S3'})
                break

print(f"\n{'Sym':>12} {'N':>5} {'WR':>5} {'P&L':>7} | R3_WR  S3_WR")
print('-'*55)
cam_stock_stats = []
for sym in sorted(cam_stock.keys()):
    trades = cam_stock[sym]
    if len(trades) < 20: continue
    w=sum(t['win'] for t in trades); wr=w/len(trades)*100
    pnl=sum(t['pnl'] for t in trades)
    r3=[t for t in trades if t['level']=='R3']
    s3=[t for t in trades if t['level']=='S3']
    r3_wr = sum(t['win'] for t in r3)/len(r3)*100 if r3 else 0
    s3_wr = sum(t['win'] for t in s3)/len(s3)*100 if s3 else 0
    cam_stock_stats.append({'sym':sym,'n':len(trades),'wr':wr,'pnl':pnl,'r3_wr':r3_wr,'s3_wr':s3_wr})

cam_stock_stats.sort(key=lambda x:-x['wr'])
for s in cam_stock_stats:
    marker = ' <<<' if s['wr']>=65 else ''
    print(f"  {s['sym']:>12} {s['n']:>5} {s['wr']:>4.0f}% {s['pnl']:>+6.1f}% | {s['r3_wr']:>4.0f}%  {s['s3_wr']:>4.0f}%{marker}")

# Best Cam stocks
cam_wl = [s['sym'] for s in cam_stock_stats if s['wr'] >= 65]
print(f'\nCam whitelist (WR>=65%): {cam_wl}')

# Test Cam with its own whitelist
cam_wl_trades = []
for sym in cam_wl:
    cam_wl_trades.extend(cam_stock[sym])
seen=set()
cam_wl_unique = [t for t in cam_wl_trades if (t['date'],) not in seen and not seen.add((t['date'],))]
# Actually dedup properly
seen2=set()
cam_wl_unique2 = [t for t in cam_wl_trades if (t['date'],t.get('sym','')) not in seen2 and not seen2.add((t['date'],t.get('sym','')))]
if cam_wl_unique2:
    w=sum(t['win'] for t in cam_wl_unique2); wr=w/len(cam_wl_unique2)*100
    pnl=sum(t['pnl'] for t in cam_wl_unique2)
    days=len(set(t['date'] for t in cam_wl_unique2))
    print(f'Cam whitelist performance: {len(cam_wl_unique2)} trades, {days} days, WR={wr:.1f}%, P&L={pnl:+.1f}%')

# ═══ 5. COMPARE: Why ORB dropped but Cam didn't ═══
print(f'\n{"="*80}')
print('5. ROOT CAUSE: Why ORB dropped but Cam held')
print('='*80)

# ORB per-year
print('\nORB + vol (ALL stocks, no whitelist):')
orb_yearly = defaultdict(lambda:{'w':0,'l':0})
for t in all_orb:
    y=t['date'][:4]
    if t['win']: orb_yearly[y]['w']+=1
    else: orb_yearly[y]['l']+=1
for y in sorted(orb_yearly):
    m=orb_yearly[y]; tot=m['w']+m['l']
    print(f"  {y}: {tot} trades, WR={m['w']/tot*100:.0f}%")

# R3 per year
print('\nCam R3 only (ALL stocks):')
cam_r3_only = []
for trades in cam_stock.values():
    cam_r3_only.extend([t for t in trades if t['level']=='R3'])
cam_yearly = defaultdict(lambda:{'w':0,'l':0})
for t in cam_r3_only:
    y=t['date'][:4]
    if t['win']: cam_yearly[y]['w']+=1
    else: cam_yearly[y]['l']+=1
for y in sorted(cam_yearly):
    m=cam_yearly[y]; tot=m['w']+m['l']
    print(f"  {y}: {tot} trades, WR={m['w']/tot*100:.0f}%")

print('\nCam S3 only:')
cam_s3_only = []
for trades in cam_stock.values():
    cam_s3_only.extend([t for t in trades if t['level']=='S3'])
cs3_yearly = defaultdict(lambda:{'w':0,'l':0})
for t in cam_s3_only:
    y=t['date'][:4]
    if t['win']: cs3_yearly[y]['w']+=1
    else: cs3_yearly[y]['l']+=1
for y in sorted(cs3_yearly):
    m=cs3_yearly[y]; tot=m['w']+m['l']
    print(f"  {y}: {tot} trades, WR={m['w']/tot*100:.0f}%")

# ═══ 6. Direction analysis ═══
print(f'\n{"="*80}')
print('6. DIRECTION ANALYSIS: Are LONGs or SHORTs better?')
print('='*80)
for d in ['LONG','SHORT']:
    sub = [t for t in all_orb if t['dir']==d]
    if not sub: continue
    w=sum(t['win'] for t in sub); wr=w/len(sub)*100
    print(f'  ORB {d}: {len(sub)} trades, WR={wr:.0f}%')

    # Per year
    for y in sorted(set(t['date'][:4] for t in sub)):
        ys = [t for t in sub if t['date'][:4]==y]
        yw = sum(t['win'] for t in ys)
        print(f'    {y}: {len(ys)} trades, WR={yw/len(ys)*100:.0f}%')
