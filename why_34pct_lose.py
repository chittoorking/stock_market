"""Why do 34% of 0.75%/0.75% CAM_R3+trendDOWN trades lose? Fix it."""
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
prev_day_bars={}; daily_ctx={}
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars)); dc=[]
    for i,d in enumerate(dfs):
        db=date_bars[d].get(sym,[])
        if not db: continue
        c=db[-1]['close']; dc.append(c)
        if i>0: prev_day_bars[(d,sym)]=date_bars[dfs[i-1]].get(sym,[])
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_ctx[(d,sym)]={'trend':'UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'}
print('Done.\n', flush=True)

scan_bar=10; TARGET=0.75; STOP=0.75
trades=[]
for date in all_dates[-500:]:
    up_cnt=dn_cnt=tot_cnt=0
    for s2 in date_bars[date]:
        d2=date_bars[date][s2]
        if len(d2)<=scan_bar: continue
        tot_cnt+=1; mv=(d2[scan_bar]['close']-d2[0]['open'])/d2[0]['open']*100
        if mv>0.15: up_cnt+=1
        elif mv<-0.15: dn_cnt+=1
    regime='UP' if tot_cnt>0 and up_cnt/tot_cnt>0.6 else 'DOWN' if tot_cnt>0 and dn_cnt/tot_cnt>0.6 else 'CHOPPY'

    for sym in date_bars[date]:
        db=date_bars[date][sym]; ctx=daily_ctx.get((date,sym),{})
        if len(db)<=scan_bar+20 or ctx.get('trend')!='DOWN': continue
        lp=prev_day_bars.get((date,sym),[])
        if not lp: continue
        ph=max(b['high'] for b in lp);pl=min(b['low'] for b in lp);pcc=lp[-1]['close'];rng=ph-pl
        if rng<=0: continue
        r3=pcc+rng*1.1/4
        cam_r3=False
        for j in range(1,scan_bar+1):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3: cam_r3=True; break
        if not cam_r3: continue

        entry=db[scan_bar]['close']
        tp=entry*(1-TARGET/100); sp=entry*(1+STOP/100)
        ep=db[min(69,len(db)-1)]['close']; exit_r='eod'; mfe=0; mae=0
        for k in range(scan_bar+1,min(len(db),70)):
            fav=(entry-db[k]['low'])/entry*100; mfe=max(mfe,fav)
            adv=(db[k]['high']-entry)/entry*100; mae=max(mae,adv)
            if db[k]['low']<=tp: ep=tp; exit_r='target'; break
            if db[k]['high']>=sp: ep=sp; exit_r='stop'; break
        pnl=(entry-ep)/entry*100

        morning=(db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100
        b0_body=abs(db[0]['close']-db[0]['open'])/(db[0]['high']-db[0]['low'])*100 if db[0]['high']!=db[0]['low'] else 0
        b0_green=db[0]['close']>db[0]['open']
        vol=sum(b['volume'] for b in db[:scan_bar+1])/(scan_bar+1)
        consec_red=0
        for k in range(scan_bar,max(scan_bar-5,0),-1):
            if db[k]['close']<db[k]['open']: consec_red+=1
            else: break

        trades.append({'date':date,'sym':sym,'pnl':round(pnl,3),'win':pnl>0,'exit':exit_r,
            'mfe':round(mfe,3),'mae':round(mae,3),
            'morning':round(morning,2),'b0_body':round(b0_body,0),'b0_green':b0_green,
            'vol':round(vol,0),'consec_red':consec_red,'regime':regime})

winners=[t for t in trades if t['win']]; losers=[t for t in trades if not t['win']]
print(f'TOTAL: {len(trades)} | W:{len(winners)} L:{len(losers)} | WR:{len(winners)/len(trades)*100:.0f}%\n')

# WHY LOSERS LOSE
print('EXIT REASON OF LOSERS:')
for ex in ['stop','eod']:
    sub=[t for t in losers if t['exit']==ex]
    if sub: print(f'  {ex}: {len(sub)} ({len(sub)/len(losers)*100:.0f}%)')

print('\nMFE OF LOSERS (did price go in our favor at all?):')
for lo,hi in [(0,0.05),(0.05,0.15),(0.15,0.30),(0.30,0.50),(0.50,1.0)]:
    sub=[t for t in losers if lo<=t['mfe']<hi]
    if sub: print(f'  MFE {lo:.2f}-{hi:.2f}%: {len(sub)} ({len(sub)/len(losers)*100:.0f}%)')

print('\nWINNER vs LOSER:')
for f in ['morning','b0_body','vol','consec_red','mae','mfe']:
    wa=sum(t[f] for t in winners)/len(winners); la=sum(t[f] for t in losers)/len(losers)
    print(f'  {f:>12}: W={wa:>8.2f} L={la:>8.2f} delta={wa-la:+.2f}')
wg=sum(t['b0_green'] for t in winners)/len(winners)*100
lg=sum(t['b0_green'] for t in losers)/len(losers)*100
print(f'  {"b0_green%":>12}: W={wg:.0f}% L={lg:.0f}%')

print('\nBY BAR0 (we SHORT, so green bar0 = against us):')
for d,label in [(True,'GREEN'),(False,'RED')]:
    sub=[t for t in trades if t['b0_green']==d]
    if len(sub)<10: continue
    sw=sum(t['win'] for t in sub); print(f'  {label}: {len(sub)} trades, WR={sw/len(sub)*100:.0f}%')

print('\nBY MORNING MOVE:')
for lo,hi in [(-5,-1),(-1,-0.5),(-0.5,0),(0,0.5),(0.5,5)]:
    sub=[t for t in trades if lo<=t['morning']<hi]
    if len(sub)<10: continue
    sw=sum(t['win'] for t in sub); print(f'  {lo:+.1f} to {hi:+.1f}%: {len(sub)} trades, WR={sw/len(sub)*100:.0f}%')

print('\nBY REGIME:')
for r in ['UP','DOWN','CHOPPY']:
    sub=[t for t in trades if t['regime']==r]
    if len(sub)<10: continue
    sw=sum(t['win'] for t in sub); print(f'  {r}: {len(sub)} trades, WR={sw/len(sub)*100:.0f}%')

print('\nBY CONSECUTIVE RED:')
for c in range(6):
    sub=[t for t in trades if t['consec_red']==c]
    if len(sub)<10: continue
    sw=sum(t['win'] for t in sub); print(f'  consec={c}: {len(sub)} trades, WR={sw/len(sub)*100:.0f}%')

print(f'\n{"="*80}')
print('FILTERS TO PUSH WR HIGHER:')
print('='*80)
filters=[
    ('No filter (baseline)', lambda t: True),
    ('bar0 RED only', lambda t: not t['b0_green']),
    ('morning < 0', lambda t: t['morning']<0),
    ('morning < -0.5', lambda t: t['morning']<-0.5),
    ('regime DOWN', lambda t: t['regime']=='DOWN'),
    ('vol > 50K', lambda t: t['vol']>50000),
    ('consec_red >= 2', lambda t: t['consec_red']>=2),
    ('consec_red >= 3', lambda t: t['consec_red']>=3),
    ('bar0RED + morn<0', lambda t: not t['b0_green'] and t['morning']<0),
    ('bar0RED + regime DOWN', lambda t: not t['b0_green'] and t['regime']=='DOWN'),
    ('regime DOWN + morn<0', lambda t: t['regime']=='DOWN' and t['morning']<0),
    ('regime DOWN + morn<0 + consec>=2', lambda t: t['regime']=='DOWN' and t['morning']<0 and t['consec_red']>=2),
    ('regime DOWN + vol>50K + morn<0', lambda t: t['regime']=='DOWN' and t['vol']>50000 and t['morning']<0),
    ('bar0RED + regimeDOWN + morn<0', lambda t: not t['b0_green'] and t['regime']=='DOWN' and t['morning']<0),
    ('bar0RED + regimeDOWN + consec>=2', lambda t: not t['b0_green'] and t['regime']=='DOWN' and t['consec_red']>=2),
    ('ALL: b0RED+regDOWN+morn<0+consec>=2', lambda t: not t['b0_green'] and t['regime']=='DOWN' and t['morning']<0 and t['consec_red']>=2),
    ('ALL + vol>50K', lambda t: not t['b0_green'] and t['regime']=='DOWN' and t['morning']<0 and t['consec_red']>=2 and t['vol']>50000),
]

import random
print(f'\n{"Filter":>45} {"N":>5} {"WR":>5} {"Gross":>8} {"Edge/t":>8} | Compound 20%')
print('-'*95)
for name, filt in filters:
    sub=[t for t in trades if filt(t)]
    if len(sub)<20: continue
    sw=sum(t['win'] for t in sub); wr=sw/len(sub)*100
    gross=sum(t['pnl'] for t in sub); per=gross/len(sub)

    # Compound sim
    random.seed(42); bal=1000000
    for _ in range(len(sub)):
        size=bal*0.20
        if random.random()<wr/100: bal+=size*TARGET/100-83
        else: bal-=size*STOP/100+83
    print(f'  {name:>45} {len(sub):>5} {wr:>4.0f}% {gross:>+7.1f}% {per:>+7.3f}% | Rs {bal:>12,.0f}')
