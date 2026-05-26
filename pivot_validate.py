"""Validate Pivot R1/S1 strategy + analyze losses + check overlap with CAM."""
import sys,io,csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime as dt

data_dir=Path('data/5min'); date_bars=defaultdict(dict); daily_ohlc=defaultdict(dict); prev_day={}; daily_trend={}
for f in sorted(data_dir.glob('*.csv')):
    sym=f.stem.split('_')[0].upper()
    if sym in ('NIFTY_50','NIFTY_BANK'): continue
    with open(f) as fh: rows=list(csv.DictReader(fh))
    by_d=defaultdict(list)
    for r in rows:
        by_d[r['timestamp'][:10]].append({'open':float(r['open']),'high':float(r['high']),'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))})
    for d,bs in by_d.items():
        date_bars[d][sym]=bs
        daily_ohlc[sym][d]={'open':bs[0]['open'],'close':bs[-1]['close'],'high':max(b['high'] for b in bs),
            'low':min(b['low'] for b in bs),'range':max(b['high'] for b in bs)-min(b['low'] for b in bs)}
    sd=sorted(daily_ohlc[sym].keys()); dc=[]
    for i,d in enumerate(sd):
        c=daily_ohlc[sym][d]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[sd[i-1]].get(sym,[])
            if pdb: prev_day[(d,sym)]={'high':max(b['high'] for b in pdb),'low':min(b['low'] for b in pdb),'close':pdb[-1]['close']}
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_trend[(d,sym)]='UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'
all_dates=sorted(date_bars.keys())
POS=1000000;CHARGES=386;SB=10;T=0.75;S=1.5

cam_signals=set()
pivot_trades=[]

for date in all_dates:
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=SB+20: continue
        trend=daily_trend.get((date,sym))
        if trend not in ('DOWN','UP'): continue
        pd=prev_day.get((date,sym))
        if not pd: continue
        rng=pd['high']-pd['low']
        if rng<=0: continue
        sd2=sorted(daily_ohlc[sym].keys())
        di=sd2.index(date) if date in sd2 else -1
        if di<7: continue
        cd=0
        for back in range(1,20):
            if di-back<1: break
            if trend=='DOWN':
                if daily_ohlc[sym][sd2[di-back]]['close']<daily_ohlc[sym][sd2[di-back-1]]['close']: cd+=1
                else: break
            else:
                if daily_ohlc[sym][sd2[di-back]]['close']>daily_ohlc[sym][sd2[di-back-1]]['close']: cd+=1
                else: break
        if cd>2: continue
        pc=daily_ohlc[sym][sd2[di-1]]
        yr=pc['range']/pc['close']*100 if pc['close']>0 else 0
        yb=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
        if yr<2.0 or yb<0.2: continue

        # CAM check
        r3=pd['close']+rng*1.1/4; s3=pd['close']-rng*1.1/4
        if trend=='DOWN':
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                    cam_signals.add((date,sym)); break
        else:
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['low']-s3)<atr*0.3 and db[j]['close']>s3:
                    cam_signals.add((date,sym)); break

        # Pivot check
        pp=(pd['high']+pd['low']+pd['close'])/3
        r1=2*pp-pd['low']; s1=2*pp-pd['high']
        signal=None
        if trend=='DOWN':
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['high']-r1)<atr*0.3 and db[j]['close']<r1:
                    signal='SHORT'; break
        else:
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['low']-s1)<atr*0.3 and db[j]['close']>s1:
                    signal='LONG'; break
        if not signal: continue

        entry=db[SB]['close']
        if signal=='SHORT':
            tp=entry*(1-T/100);sp=entry*(1+S/100)
        else:
            tp=entry*(1+T/100);sp=entry*(1-S/100)
        ep=db[min(69,len(db)-1)]['close']; mfe=0; mae=0; exit_r='eod'
        for k in range(SB+1,min(len(db),70)):
            if signal=='SHORT':
                fav=(entry-db[k]['low'])/entry*100; adv=(db[k]['high']-entry)/entry*100
            else:
                fav=(db[k]['high']-entry)/entry*100; adv=(entry-db[k]['low'])/entry*100
            mfe=max(mfe,fav); mae=max(mae,adv)
            if signal=='SHORT':
                if db[k]['low']<=tp: ep=tp;exit_r='target';break
                if db[k]['high']>=sp: ep=sp;exit_r='stop';break
            else:
                if db[k]['high']>=tp: ep=tp;exit_r='target';break
                if db[k]['low']<=sp: ep=sp;exit_r='stop';break
        if signal=='SHORT': pnl_pct=(entry-ep)/entry*100
        else: pnl_pct=(ep-entry)/entry*100
        pnl=pnl_pct/100*POS-CHARGES
        dow=dt.strptime(date,'%Y-%m-%d').weekday()
        days=['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
        pivot_trades.append({
            'pnl':pnl,'win':pnl>0,'date':date,'sym':sym,'dir':signal,
            'mfe':round(mfe,2),'mae':round(mae,2),'exit':exit_r,
            'is_cam':(date,sym) in cam_signals,'entry':round(entry,2),
            'ep':round(ep,2),'day':days[dow],'pnl_pct':round(pnl_pct,3)
        })

n=len(pivot_trades);w=sum(1 for t in pivot_trades if t['win'])
losses=[t for t in pivot_trades if not t['win']]
only_pivot=[t for t in pivot_trades if not t['is_cam']]
both=[t for t in pivot_trades if t['is_cam']]
train=[t for t in pivot_trades if t['date']<'2025-01-01']
test=[t for t in pivot_trades if t['date']>='2025-01-01']

print('='*80)
print(f'PIVOT R1/S1 (T={T}% S={S}%)')
print('='*80)
print(f'\n  Trades: {n} | Wins: {w} ({w/n*100:.1f}%) | Losses: {n-w}')
print(f'  Per trade: Rs {sum(t["pnl"] for t in pivot_trades)/n:+,.0f}')

tr_w=sum(1 for t in train if t['win']);te_w=sum(1 for t in test if t['win'])
print(f'\n  WALK-FORWARD:')
print(f'    Train: {len(train)} trades, WR={tr_w/len(train)*100:.1f}%')
print(f'    Test:  {len(test)} trades, WR={te_w/len(test)*100:.1f}%')

print(f'\n  OVERLAP:')
print(f'    Both CAM+Pivot (same stock same day): {len(both)}')
print(f'    Pivot ONLY (NEW signals CAM missed): {len(only_pivot)}')
if only_pivot:
    op_w=sum(1 for t in only_pivot if t['win'])
    print(f'    Pivot-only WR: {op_w/len(only_pivot)*100:.1f}%')
    print(f'    Pivot-only /trade: Rs {sum(t["pnl"] for t in only_pivot)/len(only_pivot):+,.0f}')

yearly=defaultdict(lambda:{'n':0,'w':0})
for t in pivot_trades:
    y=t['date'][:4]; yearly[y]['n']+=1
    if t['win']: yearly[y]['w']+=1
print(f'\n  YEARLY:')
for y in sorted(yearly):
    m=yearly[y]
    print(f'    {y}: {m["n"]} trades, WR={m["w"]/m["n"]*100:.0f}%, losses={m["n"]-m["w"]}')

print(f'\n  ALL {len(losses)} LOSSES:')
for i,t in enumerate(sorted(losses, key=lambda x:x['pnl']),1):
    cam='CAM+PVT' if t['is_cam'] else 'PVT only'
    print(f'    {i}. {t["date"]} {t["day"]} {t["sym"]:>12} {t["dir"]:>5} '
          f'MFE={t["mfe"]:.2f}% MAE={t["mae"]:.2f}% Rs{t["pnl"]:+,.0f} {t["exit"]} [{cam}]')

stops=[t for t in losses if t['exit']=='stop']
eods=[t for t in losses if t['exit']=='eod']
print(f'\n  STOP HITS: {len(stops)} | EOD LOSSES: {len(eods)}')
