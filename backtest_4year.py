"""
4-YEAR BACKTEST — Test our top strategies on 1087 trading days.
This is the ultimate validation. If it holds, we deploy.

Tests:
1. ORB + whitelist + vol (was 70% WR on 121 days)
2. ORB + vol + Mkt>=6 + candle<=0 (was 67% WR)
3. Camarilla R3 (was 65% WR on 118 days)
4. Combined system (Tier 1/2/3)

Walk-forward: train on first 3 years, test on last year.
"""
import sys; sys.path.insert(0, '.')
import csv, json, math, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from app.agents.coded_moe import score_price, score_momentum
from app.signals.base import get_sector, SECTOR_MAP
from app.agents.data_providers import SectorAnalyzer

data_dir = Path('data/5min')
all_data = {}; date_bars = defaultdict(dict)
print('Loading 4 years of data...', flush=True)
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

macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

print(f'{len(all_data)} stocks, {len(all_dates)} days ({all_dates[0]} to {all_dates[-1]})')

WHITELIST = {'ULTRACEMCO','EICHERMOT','M&M','TATASTEEL','TECHM','SBILIFE','NTPC',
             'ADANIPORTS','ICICIBANK','SUNPHARMA','TITAN','WIPRO','DIVISLAB','COALINDIA'}

# Pre-compute sector/market for scan_bar=3 (lightweight — no SectorAnalyzer for speed)
print('Pre-computing market breadth...', flush=True)
market_breadth = {}
for date in all_dates:
    up=0; dn=0; tot=0
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        if len(db) <= 6: continue
        tot += 1
        mv = (db[6]['close']-db[0]['open'])/db[0]['open']*100
        if mv > 0.15: up += 1
        elif mv < -0.15: dn += 1
    market_breadth[date] = (up, dn, tot)

def mkt_score(date, direction):
    up, dn, tot = market_breadth.get(date, (0,0,0))
    if tot == 0: return 5
    s = 5
    up_p = up/tot*100; dn_p = dn/tot*100
    if direction=="LONG" and up_p>65: s+=2
    elif direction=="LONG" and up_p>55: s+=1
    elif direction=="LONG" and up_p<35: s-=2
    elif direction=="SHORT" and dn_p>65: s+=2
    elif direction=="SHORT" and dn_p>55: s+=1
    elif direction=="SHORT" and dn_p<35: s-=2
    return max(0, min(10, s))

def simulate(db, entry_bar, entry, d, stop, target, exit_bar=36):
    for j in range(entry_bar+1, min(len(db), exit_bar+1)):
        if d=='LONG':
            if db[j]['low']<=stop: return stop
            if db[j]['high']>=target: return target
        else:
            if db[j]['high']>=stop: return stop
            if db[j]['low']<=target: return target
    return db[min(exit_bar,len(db)-1)]['close']

def pnl_calc(e,x,d): return (x-e)/e*100 if d=='LONG' else (e-x)/e*100

def candle_sc(bar):
    body=abs(bar['close']-bar['open']); rng=bar['high']-bar['low']
    if rng==0: return 0
    uw=bar['high']-max(bar['open'],bar['close']); lw=min(bar['open'],bar['close'])-bar['low']
    if body/rng>0.8: return 2 if bar['close']>bar['open'] else -2
    if lw>body*2 and uw<body*0.5: return 1
    if uw>body*2 and lw<body*0.5: return -1
    return 0

t0 = time.time()
print(f'\nRunning backtests on {len(all_dates)} days...\n', flush=True)

# ═══ STRATEGY 1: ORB + Whitelist + Vol ═══
orb_wl = []
for di, date in enumerate(all_dates):
    for sym in WHITELIST:
        if sym not in date_bars[date]: continue
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
            orb_wl.append({'date':date,'sym':sym,'pnl':round(pnl,4),'win':pnl>0})
            break
    if (di+1)%100==0:
        print(f'  {di+1}/{len(all_dates)} | ORB_WL: {len(orb_wl)} signals', flush=True)

# ═══ STRATEGY 2: ORB + Vol + Mkt>=6 + candle<=0 (any stock) ═══
orb_mkt = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date,sym))
        if pc is None or len(db)<40: continue
        rh=db[0]['high']; rl=db[0]['low']; rs=rh-rl
        if rs==0: continue
        for j in range(1, min(len(db), 12)):
            d=None
            if db[j]['close']>rh: d='LONG'
            elif db[j]['close']<rl: d='SHORT'
            if not d: continue
            if db[0]['volume']>0 and db[j]['volume']<db[0]['volume']*1.2: break
            Mkt = mkt_score(date, d)
            c = candle_sc(db[j])
            if Mkt < 6 or c > 0: break
            entry=db[j]['close']; stop=rl if d=='LONG' else rh
            risk=abs(entry-stop)
            if risk==0: break
            target=entry+risk*1.5 if d=='LONG' else entry-risk*1.5
            ep=simulate(db,j,entry,d,stop,target,36)
            pnl=pnl_calc(entry,ep,d)
            orb_mkt.append({'date':date,'sym':sym,'pnl':round(pnl,4),'win':pnl>0})
            break

# ═══ STRATEGY 3: Camarilla R3 bounce ═══
cam_r3 = []
for date in all_dates:
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pd_data = prev_day_data.get((date,sym))
        if pd_data is None or len(db)<30: continue
        h=pd_data['high']; l=pd_data['low']; c=pd_data['close']
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
                cam_r3.append({'date':date,'sym':sym,'pnl':round(pnl,4),'win':pnl>0,'level':'R3'})
                break
            if abs(db[j]['low']-s3)<tol and db[j]['close']>s3:
                entry=db[j]['close']; stop=s4; target=c
                ep=simulate(db,j,entry,'LONG',stop,target,69)
                pnl=pnl_calc(entry,ep,'LONG')
                cam_r3.append({'date':date,'sym':sym,'pnl':round(pnl,4),'win':pnl>0,'level':'S3'})
                break

# ═══ STRATEGY 4: Lunch Reversal + whitelist + noise<=3 ═══
lunch = []
for date in all_dates:
    for sym in WHITELIST:
        if sym not in date_bars[date]: continue
        db = date_bars[date][sym]
        if len(db) <= 24: continue
        morning = (db[18]['close']-db[0]['open'])/db[0]['open']*100
        if abs(morning) < 0.3: continue
        d='SHORT' if morning>0 else 'LONG'
        entry=db[24]['close']
        # Noise check
        bsf=db[19:25]
        ranges_s=sum(b['high']-b['low'] for b in bsf)
        move_abs=abs(bsf[-1]['close']-bsf[0]['close'])
        noise=ranges_s/(move_abs+0.001)
        if noise > 3: continue
        atr=sum(b['high']-b['low'] for b in bsf)/len(bsf)
        stop=entry+atr*1.5 if d=='SHORT' else entry-atr*1.5
        target=entry-atr*1 if d=='SHORT' else entry+atr*1
        ep=simulate(db,24,entry,d,stop,target,69)
        pnl=pnl_calc(entry,ep,d)
        lunch.append({'date':date,'sym':sym,'pnl':round(pnl,4),'win':pnl>0})

elapsed = time.time()-t0
print(f'\nDone in {elapsed:.1f}s\n')

# ═══ RESULTS ═══
def report(name, trades):
    if not trades: print(f'{name}: 0 trades'); return
    # Dedup by date+sym
    seen=set(); unique=[]
    for t in trades:
        key=(t['date'],t['sym'])
        if key not in seen: seen.add(key); unique.append(t)
    w=sum(t['win'] for t in unique); wr=w/len(unique)*100
    pnl=sum(t['pnl'] for t in unique)
    days=len(set(t['date'] for t in unique))

    # Walk-forward: first 3 years train, last year test
    split_idx = int(len(all_dates) * 0.75)  # 75/25 split
    split_date = all_dates[split_idx]
    train=[t for t in unique if t['date']<split_date]
    test=[t for t in unique if t['date']>=split_date]
    tw=sum(t['win'] for t in train); twr=tw/len(train)*100 if train else 0
    ew=sum(t['win'] for t in test); ewr=ew/len(test)*100 if test else 0

    print(f'\n{name}:')
    print(f'  FULL: {len(unique)} trades, {days} days, WR={wr:.1f}%, P&L={pnl:+.1f}%')
    print(f'  TRAIN ({all_dates[0]} to {split_date}): {len(train)} trades, WR={twr:.1f}%')
    print(f'  TEST  ({split_date} to {all_dates[-1]}): {len(test)} trades, WR={ewr:.1f}%')
    holds = "HOLDS" if ewr >= twr - 10 and ewr >= 50 else "FAILS"
    print(f'  Walk-forward: {holds}')

    # Yearly breakdown
    yearly = defaultdict(lambda: {'w':0,'l':0,'pnl':0})
    for t in unique:
        y = t['date'][:4]
        if t['win']: yearly[y]['w']+=1
        else: yearly[y]['l']+=1
        yearly[y]['pnl']+=t['pnl']

    print(f'  Yearly:')
    for y in sorted(yearly):
        m=yearly[y]; total=m['w']+m['l']
        wr2=m['w']/total*100 if total else 0
        print(f'    {y}: {total} trades, WR={wr2:.0f}%, P&L={m["pnl"]:+.1f}%')

    # Compound return on 1 lakh
    capital = 100000
    bal = capital
    for t in sorted(unique, key=lambda x: x['date']):
        bal *= (1 + t['pnl']/100)
    print(f'  1 Lakh compound: Rs {bal:,.0f} ({(bal/capital-1)*100:+.1f}%)')

print('='*80)
print(f'4-YEAR BACKTEST RESULTS ({len(all_dates)} trading days)')
print('='*80)

report('ORB + Whitelist + Vol', orb_wl)
report('ORB + Vol + Mkt>=6 + candle<=0', orb_mkt)
report('Camarilla R3/S3', cam_r3)
report('Lunch Reversal (WL + noise<=3)', lunch)

# Combined daily system
print(f'\n{"="*80}')
print('COMBINED DAILY SYSTEM')
print('='*80)
# For each day, pick best available strategy
combined = []
for date in all_dates:
    # Priority: ORB_WL > ORB_Mkt > Cam > Lunch
    day_orb = [t for t in orb_wl if t['date']==date]
    day_mkt = [t for t in orb_mkt if t['date']==date]
    day_cam = [t for t in cam_r3 if t['date']==date]
    day_lunch = [t for t in lunch if t['date']==date]

    if day_orb:
        combined.append({**day_orb[0], 'strat':'ORB_WL'})
    elif day_mkt:
        combined.append({**day_mkt[0], 'strat':'ORB_Mkt'})
    elif day_cam:
        combined.append({**day_cam[0], 'strat':'CAM'})
    elif day_lunch:
        combined.append({**day_lunch[0], 'strat':'LUNCH'})

report('Combined (ORB_WL > ORB_Mkt > CAM > LUNCH)', combined)
coverage = len(set(t['date'] for t in combined))
print(f'  Coverage: {coverage}/{len(all_dates)} days ({coverage/len(all_dates)*100:.0f}%)')
