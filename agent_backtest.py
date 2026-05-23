"""
I (Claude) AM the trading agent. I read data, reason, decide, execute.
No external LLM calls. Pure coded logic from my analysis.
30-day backtest using the scripts I built.
"""
import sys, io, csv, json
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
from pathlib import Path
from collections import defaultdict

data_dir = Path('data/5min')
all_data = {}; date_bars = defaultdict(dict)
print('Loading...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    bars = [{'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
             'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))} for r in rows]
    all_data[sym] = bars
    by_date = defaultdict(list)
    for b in bars: by_date[b['timestamp'][:10]].append(b)
    for d, bs in by_date.items(): date_bars[d][sym] = bs

all_dates = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))

# Daily context
prev_close = {}; daily_ctx = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    dc = []
    for i, d in enumerate(dates_for_sym):
        db = date_bars[d].get(sym,[])
        if not db: continue
        c = db[-1]['close']; dc.append(c)
        if i > 0:
            pdb = date_bars[dates_for_sym[i-1]].get(sym,[])
            if pdb: prev_close[(d,sym)] = pdb[-1]['close']
        if len(dc) >= 5:
            up = sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            trend = 'UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'
            rsi = 50
            if len(dc)>=15:
                g=[max(0,dc[j]-dc[j-1]) for j in range(-14,0)]
                lo=[max(0,dc[j-1]-dc[j]) for j in range(-14,0)]
                ag=sum(g)/14; al=sum(lo)/14
                rsi=100-100/(1+ag/al) if al>0 else 50
            lb=min(250,len(dc))
            daily_ctx[(d,sym)] = {'trend':trend,'rsi':round(rsi),'from_52h':round((c-max(dc[-lb:]))/max(dc[-lb:])*100,1)}

print('Done.\n', flush=True)

import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--days', type=int, default=30)
args, _ = parser.parse_known_args()

test_dates = all_dates[-args.days:]
scan_bar = 10
results = []
capital = 100000

print(f'AGENT BACKTEST | {len(test_dates)} days | Rs {capital:,}')
print(f'Rules: CAM_R3 + ORB | daily trend filter | 5-lot management | 3PM close')
print('='*100, flush=True)

for date in test_dates:
    # Market breadth
    up=dn=tot=0
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=scan_bar: continue
        tot+=1
        mv=(db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100
        if mv>0.15: up+=1
        elif mv<-0.15: dn+=1
    if tot == 0: continue
    regime = 'UP' if up/tot>0.6 else 'DOWN' if dn/tot>0.6 else 'CHOPPY'

    # Scan signals
    signals = []
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close.get((date,sym))
        ctx = daily_ctx.get((date,sym),{})
        if pc is None or len(db)<=scan_bar+10 or not ctx: continue

        # ORB
        rh=db[0]['high']; rl=db[0]['low']; rs=rh-rl
        if rs > 0:
            for j in range(1, scan_bar+1):
                if db[j]['close']>rh and db[j]['volume']>db[0]['volume']:
                    signals.append({'sym':sym,'type':'ORB','dir':'LONG','entry':db[j]['close'],
                        'stop':rl,'risk':round((db[j]['close']-rl)/db[j]['close']*100,2),
                        'trend':ctx.get('trend','?'),'rsi':ctx.get('rsi',50)})
                    break
                if db[j]['close']<rl and db[j]['volume']>db[0]['volume']:
                    signals.append({'sym':sym,'type':'ORB','dir':'SHORT','entry':db[j]['close'],
                        'stop':rh,'risk':round((rh-db[j]['close'])/db[j]['close']*100,2),
                        'trend':ctx.get('trend','?'),'rsi':ctx.get('rsi',50)})
                    break

        # CAM_R3 only
        pd_dates = sorted(set(b['timestamp'][:10] for b in all_data[sym] if b['timestamp'][:10]<date))
        if pd_dates:
            last_pd = date_bars[pd_dates[-1]].get(sym,[])
            if last_pd:
                ph=max(b['high'] for b in last_pd); pl=min(b['low'] for b in last_pd); pcc=last_pd[-1]['close']
                rng=ph-pl
                if rng>0:
                    r3=pcc+rng*1.1/4; r4=pcc+rng*1.1/2
                    for j in range(1, scan_bar+1):
                        atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                        tol=atr*0.3
                        if abs(db[j]['high']-r3)<tol and db[j]['close']<r3:
                            signals.append({'sym':sym,'type':'CAM_R3','dir':'SHORT','entry':db[j]['close'],
                                'stop':r4,'risk':round((r4-db[j]['close'])/db[j]['close']*100,2),
                                'trend':ctx.get('trend','?'),'rsi':ctx.get('rsi',50)})
                            break

    # Dedup
    seen=set(); unique=[]
    for s in signals:
        if s['sym'] not in seen: seen.add(s['sym']); unique.append(s)
    signals = unique

    # === MY TRADING DECISION ===
    best = None; best_score = -999
    for s in signals:
        score = 0
        if s['type'] == 'CAM_R3': score += 3
        if s['dir']=='SHORT' and s['trend']=='DOWN': score += 3
        elif s['dir']=='LONG' and s['trend']=='UP': score += 3
        elif s['trend']=='SIDE': score += 0  # Neutral
        else: score -= 2  # Against daily trend
        if s['dir']=='SHORT' and s['rsi']<40: score += 2
        elif s['dir']=='LONG' and s['rsi']>60: score += 2
        if s['risk'] < 1.0: score += 1
        elif s['risk'] > 2.5: score -= 1
        if regime=='UP' and s['dir']=='LONG': score += 1
        elif regime=='DOWN' and s['dir']=='SHORT': score += 1
        elif regime != 'CHOPPY' and ((regime=='UP' and s['dir']=='SHORT') or (regime=='DOWN' and s['dir']=='LONG')): score -= 1
        if score > best_score: best_score = score; best = s

    if not best or best_score < 2: continue

    # Execute
    sym = best['sym']; direction = best['dir']
    db = date_bars[date][sym]
    entry = db[scan_bar]['close']
    stop = best['stop']
    risk = abs(entry-stop)
    if risk == 0: continue
    target = entry + risk*2.5 if direction=='LONG' else entry - risk*2.5

    # 5-lot management
    lots = {'L1':0.30,'L2':0.25,'L3':0.20,'L4':0.15,'L5':0.10}
    booked = 0; cur_stop = stop; mfe = 0; exit_reason = 'eod'; lot_log = []

    for j in range(scan_bar+1, min(len(db), 70)):
        b = db[j]
        pnl_now = (b['close']-entry)/entry*100 if direction=='LONG' else (entry-b['close'])/entry*100
        fav = (b['high']-entry)/entry*100 if direction=='LONG' else (entry-b['low'])/entry*100
        mfe = max(mfe, fav)
        remaining = sum(lots.values())
        if remaining <= 0: break

        if direction=='LONG' and b['low']<=cur_stop:
            booked+=((cur_stop-entry)/entry*100)*remaining; lots={}; exit_reason='stop'; break
        if direction=='SHORT' and b['high']>=cur_stop:
            booked+=((entry-cur_stop)/entry*100)*remaining; lots={}; exit_reason='stop'; break
        if direction=='LONG' and b['high']>=target:
            booked+=((target-entry)/entry*100)*remaining; lots={}; exit_reason='target'; break
        if direction=='SHORT' and b['low']<=target:
            booked+=((entry-target)/entry*100)*remaining; lots={}; exit_reason='target'; break

        if (j-scan_bar) % 3 != 0 or j==scan_bar+1: continue
        if pnl_now < 0.3: continue
        if pnl_now>=0.3 and ((direction=='LONG' and cur_stop<entry) or (direction=='SHORT' and cur_stop>entry)):
            cur_stop = entry
        if pnl_now>=0.5 and 'L1' in lots:
            booked+=pnl_now*lots['L1']; lot_log.append(f'L1@{pnl_now:.1f}'); del lots['L1']
            cur_stop = entry+risk*0.3 if direction=='LONG' else entry-risk*0.3
        if pnl_now>=1.0 and 'L2' in lots:
            booked+=pnl_now*lots['L2']; lot_log.append(f'L2@{pnl_now:.1f}'); del lots['L2']
            cur_stop = entry+risk*0.7 if direction=='LONG' else entry-risk*0.7
        if pnl_now>=1.5 and 'L3' in lots:
            booked+=pnl_now*lots['L3']; lot_log.append(f'L3@{pnl_now:.1f}'); del lots['L3']
        if pnl_now>=2.0 and 'L4' in lots:
            booked+=pnl_now*lots['L4']; lot_log.append(f'L4@{pnl_now:.1f}'); del lots['L4']

    if lots:
        ep=db[min(69,len(db)-1)]['close']
        eod_pnl=(ep-entry)/entry*100 if direction=='LONG' else (entry-ep)/entry*100
        booked+=eod_pnl*sum(lots.values())

    pnl=booked; win=pnl>0; w='W' if win else 'L'
    cap=pnl/mfe*100 if mfe>0 else 0
    capital *= (1+(pnl-0.0835)/100)
    lots_str=' '.join(lot_log) if lot_log else '-'
    print(f'{date} {regime:>6} | {best["type"]:>6} {direction:>5} {sym:>12} sc={best_score:>2} | {w} {pnl:+6.3f}% MFE={mfe:.2f}% {exit_reason:>6} | {lots_str:>30} | Rs {capital:>9,.0f}', flush=True)
    results.append({'date':date,'sym':sym,'dir':direction,'type':best['type'],'pnl':round(pnl,4),'win':win,'mfe':round(mfe,3),'exit':exit_reason})

print(f'\n{"="*100}')
print(f'SUMMARY ({len(test_dates)} days)')
print(f'{"="*100}')
if results:
    w=sum(1 for r in results if r['win']); n=len(results)
    gross=sum(r['pnl'] for r in results); charges=0.0835*n; net=gross-charges
    print(f'Traded: {n}/{len(test_dates)} days | WR: {w}/{n} = {w/n*100:.0f}%')
    print(f'Gross: {gross:+.2f}% | Charges: {charges:.2f}% | Net: {net:+.2f}%')
    print(f'Final: Rs {capital:,.0f} (started Rs 100,000)')
    for t in sorted(set(r['type'] for r in results)):
        sub=[r for r in results if r['type']==t]
        tw=sum(1 for r in sub if r['win']); tp=sum(r['pnl'] for r in sub)
        print(f'  {t}: {len(sub)} trades, WR={tw}/{len(sub)}={tw/len(sub)*100:.0f}%, PnL={tp:+.2f}%')
else:
    print('No trades taken.')
