"""
RECHECK ALL STRATEGIES — Full 4-year test with charges.
For each strategy: gross P&L, net P&L after charges,
avg per-trade return, whether it survives costs.

Charges per trade (Zerodha/Upstox, Rs 1 Lakh):
- Brokerage: Rs 40 (Rs 20 x 2)
- STT: Rs 25 (0.025% sell)
- Exchange: Rs 6.90
- GST: Rs 8.44
- Stamp: Rs 3
- SEBI: Rs 0.20
- TOTAL: Rs 83.54 = 0.0835%
"""
import sys; sys.path.insert(0, '.')
import csv, math
from pathlib import Path
from collections import defaultdict

data_dir = Path('data/5min')
all_data = {}; date_bars = defaultdict(dict)
print('Loading 4 years...', flush=True)
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
prev_close_map = {}; prev_day_data = {}; multi_day = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    for i, d in enumerate(dates_for_sym):
        if i > 0:
            pdb = date_bars[dates_for_sym[i-1]].get(sym, [])
            if pdb:
                prev_close_map[(d, sym)] = pdb[-1]['close']
                prev_day_data[(d, sym)] = {'high': max(b['high'] for b in pdb), 'low': min(b['low'] for b in pdb), 'close': pdb[-1]['close'], 'open': pdb[0]['open']}
        if i >= 3:
            dc = [date_bars[dates_for_sym[j]].get(sym, [])[-1]['close'] for j in range(max(0,i-5),i) if date_bars[dates_for_sym[j]].get(sym)]
            multi_day[(d,sym)] = dc

macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in __import__('json').load(f): macro[e['date']] = e

print(f'{len(all_data)} stocks, {len(all_dates)} days\n')

# Market breadth
market_breadth = {}
for date in all_dates:
    up=0;dn=0;tot=0
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=6: continue
        tot+=1
        mv=(db[6]['close']-db[0]['open'])/db[0]['open']*100
        if mv>0.15: up+=1
        elif mv<-0.15: dn+=1
    market_breadth[date] = (up,dn,tot)

def mkt_score(date, d):
    up,dn,tot = market_breadth.get(date,(0,0,0))
    if tot==0: return 5
    s=5; up_p=up/tot*100; dn_p=dn/tot*100
    if d=="LONG" and up_p>65: s+=2
    elif d=="LONG" and up_p>55: s+=1
    elif d=="LONG" and up_p<35: s-=2
    elif d=="SHORT" and dn_p>65: s+=2
    elif d=="SHORT" and dn_p>55: s+=1
    elif d=="SHORT" and dn_p<35: s-=2
    return max(0,min(10,s))

def sim(db, eb, entry, d, stop, target, exit_bar=69):
    for j in range(eb+1, min(len(db), exit_bar+1)):
        if d=='LONG':
            if db[j]['low']<=stop: return stop
            if db[j]['high']>=target: return target
        else:
            if db[j]['high']>=stop: return stop
            if db[j]['low']<=target: return target
    return db[min(exit_bar,len(db)-1)]['close']

def pnl(e,x,d): return (x-e)/e*100 if d=='LONG' else (e-x)/e*100

def candle_sc(bar):
    body=abs(bar['close']-bar['open']); rng=bar['high']-bar['low']
    if rng==0: return 0
    uw=bar['high']-max(bar['open'],bar['close']); lw=min(bar['open'],bar['close'])-bar['low']
    if body/rng>0.8: return 2 if bar['close']>bar['open'] else -2
    if lw>body*2 and uw<body*0.5: return 1
    if uw>body*2 and lw<body*0.5: return -1
    return 0

CHARGES_PCT = 0.0835  # Per trade on Rs 1L

def report(name, trades, n_trades_per_day=1):
    if not trades:
        print(f'\n{name}: 0 trades'); return
    # Dedup
    seen=set(); unique=[]
    for t in trades:
        k=(t['date'],t.get('sym',''))
        if k not in seen: seen.add(k); unique.append(t)

    w=sum(t['win'] for t in unique); total=len(unique)
    wr=w/total*100
    gross_pnl=sum(t['pnl'] for t in unique)
    avg_trade = gross_pnl/total

    # Avg win / avg loss
    winners = [t['pnl'] for t in unique if t['win']]
    losers = [t['pnl'] for t in unique if not t['win']]
    avg_win = sum(winners)/len(winners) if winners else 0
    avg_loss = sum(losers)/len(losers) if losers else 0

    # After charges
    charges_total = CHARGES_PCT * total * n_trades_per_day
    net_pnl = gross_pnl - charges_total
    net_avg = net_pnl / total

    # Compound
    capital = 100000
    bal_gross = capital; bal_net = capital
    for t in sorted(unique, key=lambda x: x['date']):
        bal_gross *= (1 + t['pnl']/100)
        bal_net *= (1 + (t['pnl'] - CHARGES_PCT)/100)

    days = len(set(t['date'] for t in unique))

    # Yearly
    yearly = defaultdict(lambda:{'w':0,'l':0,'gross':0,'net':0})
    for t in unique:
        y=t['date'][:4]
        if t['win']: yearly[y]['w']+=1
        else: yearly[y]['l']+=1
        yearly[y]['gross']+=t['pnl']
        yearly[y]['net']+=t['pnl']-CHARGES_PCT

    survives = net_pnl > 0

    print(f'\n{"="*70}')
    print(f'{name}')
    print(f'{"="*70}')
    print(f'  Trades: {total} | Days: {days} | WR: {wr:.1f}%')
    print(f'  Avg win: {avg_win:+.3f}% | Avg loss: {avg_loss:+.3f}% | Reward:Risk = {abs(avg_win/avg_loss):.2f}' if avg_loss != 0 else f'  Avg win: {avg_win:+.3f}%')
    print(f'  Avg per trade: {avg_trade:+.4f}% | Charges: {CHARGES_PCT:.4f}% | Net: {net_avg:+.4f}%')
    print(f'  Gross P&L: {gross_pnl:+.1f}% | Net P&L: {net_pnl:+.1f}%')
    print(f'  Rs 1L gross: Rs {bal_gross:,.0f} | Rs 1L net: Rs {bal_net:,.0f}')
    print(f'  SURVIVES CHARGES: {"YES" if survives else "NO"}')
    print(f'  Yearly:')
    for y in sorted(yearly):
        m=yearly[y]; tot2=m['w']+m['l']
        status = '+' if m['net']>0 else 'NEG'
        print(f'    {y}: {tot2} trades, WR={m["w"]/tot2*100:.0f}%, gross={m["gross"]:+.1f}%, net={m["net"]:+.1f}% {status}')

# ═══ GENERATE ALL STRATEGIES ON 4 YEARS ═══
print('Running all strategies on 1087 days...', flush=True)

# 1. ORB + Vol + Mkt>=6 + candle<=0 (was 53% WR, +79.9%)
orb_mkt = []
for date in all_dates:
    for sym in date_bars[date]:
        db=date_bars[date][sym]; pc=prev_close_map.get((date,sym))
        if pc is None or len(db)<40: continue
        rh=db[0]['high'];rl=db[0]['low'];rs=rh-rl
        if rs==0: continue
        for j in range(1,min(len(db),12)):
            d=None
            if db[j]['close']>rh: d='LONG'
            elif db[j]['close']<rl: d='SHORT'
            if not d: continue
            if db[0]['volume']>0 and db[j]['volume']<db[0]['volume']*1.2: break
            Mkt=mkt_score(date,d); c=candle_sc(db[j])
            if Mkt<6 or c>0: break
            entry=db[j]['close']; stop=rl if d=='LONG' else rh
            risk=abs(entry-stop)
            if risk==0: break
            for rr in [1.5, 2.0, 2.5, 3.0]:
                target=entry+risk*rr if d=='LONG' else entry-risk*rr
                for eb in [36, 69]:
                    ep=sim(db,j,entry,d,stop,target,eb)
                    p=pnl(entry,ep,d)
                    orb_mkt.append({'date':date,'sym':sym,'pnl':round(p,4),'win':p>0,
                                   'rr':rr,'eb':eb,'label':f'ORB+Mkt+c R:{rr} E:{eb}'})
            break

# 2. ORB NTPC only (was 64.5% WR)
orb_ntpc = []
for date in all_dates:
    if 'NTPC' not in date_bars[date]: continue
    db=date_bars[date]['NTPC']; pc=prev_close_map.get((date,'NTPC'))
    if pc is None or len(db)<40: continue
    rh=db[0]['high'];rl=db[0]['low'];rs=rh-rl
    if rs==0: continue
    for j in range(1,min(len(db),12)):
        d=None
        if db[j]['close']>rh: d='LONG'
        elif db[j]['close']<rl: d='SHORT'
        if not d: continue
        if db[0]['volume']>0 and db[j]['volume']<db[0]['volume']*1.2: break
        entry=db[j]['close']; stop=rl if d=='LONG' else rh; risk=abs(entry-stop)
        if risk==0: break
        for rr in [1.5, 2.0, 2.5, 3.0]:
            target=entry+risk*rr if d=='LONG' else entry-risk*rr
            for eb in [36, 69]:
                ep=sim(db,j,entry,d,stop,target,eb)
                p=pnl(entry,ep,d)
                orb_ntpc.append({'date':date,'sym':'NTPC','pnl':round(p,4),'win':p>0,
                                'rr':rr,'eb':eb,'label':f'ORB NTPC R:{rr} E:{eb}'})
        break

# 3. Camarilla R3 whitelist (was 69.3% WR)
CAM_WL = {'HDFCBANK','HCLTECH','TITAN','SBIN','NESTLEIND','HEROMOTOCO','WIPRO','UPL','ASIANPAINT','HINDUNILVR'}
cam_wl = []
for date in all_dates:
    for sym in CAM_WL:
        if sym not in date_bars[date]: continue
        db=date_bars[date][sym]; pd_data=prev_day_data.get((date,sym))
        if pd_data is None or len(db)<30: continue
        h=pd_data['high'];l=pd_data['low'];c=pd_data['close'];rng=h-l
        if rng==0: continue
        r3=c+rng*1.1/4; r4=c+rng*1.1/2; s3=c-rng*1.1/4; s4=c-rng*1.1/2
        for j in range(1,min(len(db),20)):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            tol=atr*0.3
            entry=None; d=None
            if abs(db[j]['high']-r3)<tol and db[j]['close']<r3:
                entry=db[j]['close']; d='SHORT'; stop=r4; target=c
            elif abs(db[j]['low']-s3)<tol and db[j]['close']>s3:
                entry=db[j]['close']; d='LONG'; stop=s4; target=c
            if entry is None: continue
            ep=sim(db,j,entry,d,stop,target,69)
            p=pnl(entry,ep,d)
            cam_wl.append({'date':date,'sym':sym,'pnl':round(p,4),'win':p>0})
            break

# 4. Camarilla with WIDER targets (not just prev close)
cam_wide = []
for date in all_dates:
    for sym in CAM_WL:
        if sym not in date_bars[date]: continue
        db=date_bars[date][sym]; pd_data=prev_day_data.get((date,sym))
        if pd_data is None or len(db)<30: continue
        h=pd_data['high'];l=pd_data['low'];c=pd_data['close'];rng=h-l
        if rng==0: continue
        r3=c+rng*1.1/4; r4=c+rng*1.1/2; s3=c-rng*1.1/4; s4=c-rng*1.1/2
        for j in range(1,min(len(db),20)):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            tol=atr*0.3
            entry=None; d=None; stop=None
            if abs(db[j]['high']-r3)<tol and db[j]['close']<r3:
                entry=db[j]['close']; d='SHORT'; stop=r4
            elif abs(db[j]['low']-s3)<tol and db[j]['close']>s3:
                entry=db[j]['close']; d='LONG'; stop=s4
            if entry is None: continue
            risk=abs(entry-stop)
            if risk==0: break
            for rr in [1.5, 2.0, 2.5, 3.0]:
                target=entry+risk*rr if d=='LONG' else entry-risk*rr
                for eb in [36, 48, 69]:
                    ep=sim(db,j,entry,d,stop,target,eb)
                    p=pnl(entry,ep,d)
                    cam_wide.append({'date':date,'sym':sym,'pnl':round(p,4),'win':p>0,
                                    'rr':rr,'eb':eb,'label':f'CAM wide R:{rr} E:{eb}'})
            break

# 5. Opening Drive (was 53% WR, +42.5% on 121 days)
od = []
for date in all_dates:
    for sym in date_bars[date]:
        db=date_bars[date][sym]; pc=prev_close_map.get((date,sym))
        if pc is None or len(db)<30: continue
        b0=db[0]; b0_body=abs(b0['close']-b0['open']); b0_range=b0['high']-b0['low']
        if b0_range==0: continue
        b0_pct=b0_body/pc*100; b0_br=b0_body/b0_range
        if b0_pct<0.3 or b0_br<0.7: continue
        d='LONG' if b0['close']>b0['open'] else 'SHORT'
        if len(db)<3: continue
        if d=='LONG' and db[1]['close']<=db[1]['open']: continue
        if d=='SHORT' and db[1]['close']>=db[1]['open']: continue
        entry=db[1]['close']
        for rr in [1.0, 1.5, 2.0, 2.5]:
            if d=='LONG': stop=min(b0['low'],db[1]['low']); target=entry+(entry-stop)*rr
            else: stop=max(b0['high'],db[1]['high']); target=entry-(stop-entry)*rr
            for eb in [12, 24, 36]:
                ep=sim(db,1,entry,d,stop,target,eb)
                p=pnl(entry,ep,d)
                od.append({'date':date,'sym':sym,'pnl':round(p,4),'win':p>0,
                          'rr':rr,'eb':eb,'label':f'OD R:{rr} E:{eb}'})

# 6. Gap Fill >1.5% (was 52% WR, 54 days)
gf = []
for date in all_dates:
    for sym in date_bars[date]:
        db=date_bars[date][sym]; pc=prev_close_map.get((date,sym))
        if pc is None or len(db)<30: continue
        gap=(db[0]['open']-pc)/pc*100
        if abs(gap)<1.0: continue
        d='SHORT' if gap>0 else 'LONG'
        for entry_bar in [1,2]:
            if len(db)<=entry_bar: continue
            if d=='LONG' and db[entry_bar]['close']<=db[entry_bar]['open']: continue
            if d=='SHORT' and db[entry_bar]['close']>=db[entry_bar]['open']: continue
            entry=db[entry_bar]['close']; target=pc
            gap_size=abs(db[0]['open']-pc)
            stop=entry+gap_size*0.5 if d=='SHORT' else entry-gap_size*0.5
            for eb in [36, 69]:
                ep=sim(db,entry_bar,entry,d,stop,target,eb)
                p=pnl(entry,ep,d)
                gf.append({'date':date,'sym':sym,'pnl':round(p,4),'win':p>0,
                           'entry_bar':entry_bar,'eb':eb,'label':f'GapFill e:{entry_bar} E:{eb}'})
            break

# 7. Squeeze Momentum R:2.5 (was 35% WR but +110.9%)
sq = []
for date in all_dates:
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<30: continue
        for j in range(10,min(len(db),25)):
            closes=[b['close'] for b in db[:j+1]]
            highs=[b['high'] for b in db[:j+1]]
            lows=[b['low'] for b in db[:j+1]]
            n=min(10,len(closes))
            if n<7: continue
            sma=sum(closes[-n:])/n
            std=math.sqrt(sum((c-sma)**2 for c in closes[-n:])/n)
            atr=sum(highs[i]-lows[i] for i in range(len(closes)-n,len(closes)))/n
            bb_u=sma+2*std;bb_l=sma-2*std;kc_u=sma+1.5*atr;kc_l=sma-1.5*atr
            if not(bb_l>kc_l and bb_u<kc_u): continue
            don_mid=(max(highs[-n:])+min(lows[-n:]))/2
            mom=closes[-1]-(sma+don_mid)/2
            prev_sma=sum(closes[-n-1:-1])/n if len(closes)>n else sma
            prev_don=(max(highs[-n-1:-1])+min(lows[-n-1:-1]))/2 if len(closes)>n else don_mid
            prev_mom=closes[-2]-(prev_sma+prev_don)/2 if len(closes)>1 else 0
            if mom>0 and prev_mom<=0: d='LONG'
            elif mom<0 and prev_mom>=0: d='SHORT'
            else: continue
            entry=closes[-1]; stop=entry-atr*1.5 if d=='LONG' else entry+atr*1.5
            for rr in [2.0, 2.5, 3.0]:
                target=entry+(entry-stop)*rr if d=='LONG' else entry-(stop-entry)*rr
                for eb in [48, 69]:
                    ep=sim(db,j,entry,d,stop,target,eb)
                    p=pnl(entry,ep,d)
                    sq.append({'date':date,'sym':sym,'pnl':round(p,4),'win':p>0,
                              'rr':rr,'eb':eb,'label':f'Squeeze R:{rr} E:{eb}'})
            break

# 8. Multi-timeframe (daily trend + ORB)
mtf = []
for date in all_dates:
    for sym in date_bars[date]:
        db=date_bars[date][sym]; pc=prev_close_map.get((date,sym))
        daily=multi_day.get((date,sym),[])
        if pc is None or len(db)<30 or len(daily)<3: continue
        daily_trend='UP' if daily[-1]>daily[-3] else 'DOWN'
        rh=db[0]['high'];rl=db[0]['low'];rs=rh-rl
        if rs==0: continue
        for j in range(1,min(len(db),12)):
            if daily_trend=='UP' and db[j]['close']>rh: d='LONG'
            elif daily_trend=='DOWN' and db[j]['close']<rl: d='SHORT'
            else: continue
            entry=db[j]['close']; stop=rl if d=='LONG' else rh; risk=abs(entry-stop)
            if risk==0: break
            for rr in [1.5, 2.0, 2.5]:
                target=entry+risk*rr if d=='LONG' else entry-risk*rr
                for eb in [36, 48]:
                    ep=sim(db,j,entry,d,stop,target,eb)
                    p=pnl(entry,ep,d)
                    mtf.append({'date':date,'sym':sym,'pnl':round(p,4),'win':p>0,
                               'rr':rr,'eb':eb,'label':f'MTF R:{rr} E:{eb}'})
            break

print('Done generating.\n')

# ═══ REPORT ALL ═══
print('#'*80)
print('FULL 4-YEAR RECHECK — ALL STRATEGIES WITH CHARGES')
print(f'Charges: {CHARGES_PCT}% per trade (Rs 83.54 on Rs 1L)')
print('#'*80)

# Group by label and report best version
strategies = {
    'ORB+Mkt+candle': orb_mkt,
    'ORB NTPC only': orb_ntpc,
    'Cam R3 WL (prev close target)': cam_wl,
    'Cam R3 WL (wide targets)': cam_wide,
    'Opening Drive': od,
    'Gap Fill': gf,
    'Squeeze Momentum': sq,
    'Multi-Timeframe': mtf,
}

# For strategies with multiple RR/EB, find best net-profitable version
for name, trades in strategies.items():
    if not trades:
        print(f'\n{name}: 0 trades'); continue

    # Check if has label variants
    if 'label' in trades[0]:
        labels = sorted(set(t['label'] for t in trades))
        best_net = -999; best_label = ''; best_trades = []
        for label in labels:
            sub = [t for t in trades if t['label']==label]
            seen=set(); unique=[t for t in sub if (t['date'],t['sym']) not in seen and not seen.add((t['date'],t['sym']))]
            if len(unique)<20: continue
            gross=sum(t['pnl'] for t in unique)
            net=gross - CHARGES_PCT*len(unique)
            if net > best_net:
                best_net = net; best_label = label; best_trades = unique
        if best_trades:
            report(f'{name} [{best_label}]', best_trades)
        else:
            report(name, trades)
    else:
        report(name, trades)

# ═══ SUMMARY TABLE ═══
print(f'\n\n{"#"*80}')
print('SUMMARY: Which strategies SURVIVE charges?')
print(f'{"#"*80}')
print(f'{"Strategy":>40} {"Trades":>6} {"WR":>5} {"Avg/trade":>10} {"Gross":>8} {"Net":>8} {"Survive":>8}')
print('-'*95)
