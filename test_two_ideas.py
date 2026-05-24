"""Test: 1) Trail at 1.0% instead of 1.25%  2) Yesterday choppy detection"""
import sys,io,csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

data_dir=Path('data/5min'); all_data={}; date_bars=defaultdict(dict)
for f in sorted(data_dir.glob('*.csv')):
    sym=f.stem.split('_')[0].upper()
    with open(f) as fh: rows=list(csv.DictReader(fh))
    bars=[{'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
           'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))} for r in rows]
    all_data[sym]=bars
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in by_d.items(): date_bars[d][sym]=bs
all_dates=sorted(date_bars.keys())
daily_ohlc=defaultdict(dict)
for sym,bars in all_data.items():
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in sorted(by_d.items()):
        daily_ohlc[sym][d]={'open':bs[0]['open'],'close':bs[-1]['close'],
            'high':max(b['high'] for b in bs),'low':min(b['low'] for b in bs),
            'volume':sum(b['volume'] for b in bs),'range':max(b['high'] for b in bs)-min(b['low'] for b in bs)}
prev_day={}; daily_trend={}
for sym in all_data:
    sd=sorted(daily_ohlc[sym].keys()); dc=[]
    for i,d in enumerate(sd):
        c=daily_ohlc[sym][d]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[sd[i-1]].get(sym,[])
            if pdb: prev_day[(d,sym)]={'high':max(b['high'] for b in pdb),'low':min(b['low'] for b in pdb),'close':pdb[-1]['close']}
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_trend[(d,sym)]='UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'
POS=1000000;CHARGES=386;SB=10;T=1.75;S=1.50

def run(trail_act, lock, yd_filter=None):
    trades=[]
    for date in all_dates:
        for sym in date_bars[date]:
            if sym in ('NIFTY_50','NIFTY_BANK'): continue
            db=date_bars[date][sym]
            if len(db)<=SB+20: continue
            if daily_trend.get((date,sym))!='DOWN': continue
            pd=prev_day.get((date,sym))
            if not pd: continue
            rng=pd['high']-pd['low']
            if rng<=0: continue
            r3=pd['close']+rng*1.1/4
            triggered=False
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                    triggered=True; break
            if not triggered: continue
            sd2=sorted(daily_ohlc[sym].keys())
            di=sd2.index(date) if date in sd2 else -1
            if di<7: continue
            cd=0
            for back in range(1,20):
                if di-back<1: break
                if daily_ohlc[sym][sd2[di-back]]['close']<daily_ohlc[sym][sd2[di-back-1]]['close']:
                    cd+=1
                else: break
            if cd>2: continue

            # Yesterday features
            prev_d=sd2[di-1]; pc=daily_ohlc[sym][prev_d]
            yd_range_pct=pc['range']/pc['close']*100 if pc['close']>0 else 0
            yd_body_ratio=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
            yd_vol=pc.get('volume',0)
            vols=[daily_ohlc[sym][sd2[di-k]].get('volume',0) for k in range(1,4) if di-k>=0]
            vol_avg=sum(vols)/len(vols) if vols else 1
            vol_ratio=yd_vol/max(1,vol_avg)

            if yd_filter and not yd_filter(yd_range_pct, yd_body_ratio, vol_ratio):
                continue

            entry=db[SB]['close']
            tp=entry*(1-T/100);sp=entry*(1+S/100)
            lp=entry*(1-lock/100)
            ep=db[min(69,len(db)-1)]['close'];exit_r='eod';mfe=0;ta=False
            for k in range(SB+1,min(len(db),70)):
                fav=(entry-db[k]['low'])/entry*100
                mfe=max(mfe,fav)
                if mfe>=trail_act: ta=True
                if db[k]['low']<=tp: ep=tp;exit_r='target';break
                if ta and db[k]['high']>=lp: ep=lp;exit_r='trail';break
                if not ta and db[k]['high']>=sp: ep=sp;exit_r='stop';break
            pnl=(entry-ep)/entry*100/100*POS-CHARGES
            trades.append({'pnl':pnl,'win':pnl>0,'date':date})
    return trades

def report(name, trades):
    n=len(trades);w=sum(1 for t in trades if t['win']);total=sum(t['pnl'] for t in trades)
    # Walk forward
    train=[t for t in trades if t['date']<'2025-01-01']
    test=[t for t in trades if t['date']>='2025-01-01']
    tr_per=sum(t['pnl'] for t in train)/len(train) if train else 0
    te_per=sum(t['pnl'] for t in test)/len(test) if test else 0
    te_wr=sum(1 for t in test if t['win'])/len(test)*100 if test else 0
    drop=(te_per-tr_per)/tr_per*100 if tr_per else 0
    print(f'  {name:>45}: {n:>5} WR={w/n*100:.1f}% Rs{total/n:>+7,.0f}/tr | Test WR={te_wr:.1f}% Rs{te_per:>+7,.0f} ({drop:>+.0f}%)')

# ═══ Q1: Trail activation level ═══
print('='*90)
print('Q1: TRAIL AT 1.00% vs 1.25% (lock 0.075%)')
print('='*90)
report('No trail', run(999, 0.075))
report('Trail at 1.25%, lock 0.075%', run(1.25, 0.075))
report('Trail at 1.00%, lock 0.075%', run(1.00, 0.075))
report('Trail at 0.90%, lock 0.075%', run(0.90, 0.075))
report('Trail at 0.80%, lock 0.075%', run(0.80, 0.075))
report('Trail at 0.75%, lock 0.075%', run(0.75, 0.075))

# Count L->W and W->L for trail at 1.0
base=run(999, 0.075)
t10=run(1.00, 0.075)
l2w=sum(1 for a,b in zip(base,t10) if a['pnl']<=0 and b['pnl']>0)
w2l=sum(1 for a,b in zip(base,t10) if a['pnl']>0 and b['pnl']<=0)
print(f'\n  Trail at 1.00%: Loss->Win = {l2w} | Win->Loss = {w2l}')

t125=run(1.25, 0.075)
l2w2=sum(1 for a,b in zip(base,t125) if a['pnl']<=0 and b['pnl']>0)
print(f'  Trail at 1.25%: Loss->Win = {l2w2} | Win->Loss = 0')
print(f'  Trail at 1.00% catches {l2w-l2w2} MORE almost-won trades')

# ═══ Q2: Yesterday choppy detection ═══
print(f'\n{"="*90}')
print('Q2: YESTERDAY CHOPPY DETECTION')
print('Skip if yesterday was a doji/small-body day (choppy breeds choppy)')
print('='*90)

report('No yesterday filter', run(1.00, 0.075))
report('Skip if yd body_ratio < 0.15', run(1.00, 0.075, lambda r,b,v: b>=0.15))
report('Skip if yd body_ratio < 0.20', run(1.00, 0.075, lambda r,b,v: b>=0.20))
report('Skip if yd body_ratio < 0.25', run(1.00, 0.075, lambda r,b,v: b>=0.25))
report('Skip if yd range < 1.0%', run(1.00, 0.075, lambda r,b,v: r>=1.0))
report('Skip if yd range < 1.5%', run(1.00, 0.075, lambda r,b,v: r>=1.5))
report('Skip if yd range < 2.0%', run(1.00, 0.075, lambda r,b,v: r>=2.0))
report('Skip if yd vol_ratio < 0.6', run(1.00, 0.075, lambda r,b,v: v>=0.6))
report('Only if yd range > 2% AND body > 0.2', run(1.00, 0.075, lambda r,b,v: r>2.0 and b>0.2))
