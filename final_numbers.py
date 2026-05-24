"""Final setup full numbers."""
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

trades=[];daily_pnl=defaultdict(float)
yearly=defaultdict(lambda:{'n':0,'w':0,'pnl':0})

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
        prev_d=sd2[di-1]; pc=daily_ohlc[sym][prev_d]
        yd_range_pct=pc['range']/pc['close']*100 if pc['close']>0 else 0
        yd_body_ratio=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
        if yd_range_pct<2.0 or yd_body_ratio<0.2: continue

        entry=db[SB]['close']
        tp=entry*(1-T/100);sp=entry*(1+S/100)
        lp=entry*(1-0.075/100)
        ep=db[min(69,len(db)-1)]['close'];exit_r='eod';mfe=0;ta=False
        for k in range(SB+1,min(len(db),70)):
            fav=(entry-db[k]['low'])/entry*100
            mfe=max(mfe,fav)
            if mfe>=1.00: ta=True
            if db[k]['low']<=tp: ep=tp;exit_r='target';break
            if ta and db[k]['high']>=lp: ep=lp;exit_r='trail';break
            if not ta and db[k]['high']>=sp: ep=sp;exit_r='stop';break
        pnl=(entry-ep)/entry*100/100*POS-CHARGES
        trades.append({'pnl':pnl,'win':pnl>0,'date':date,'sym':sym,'exit':exit_r})
        daily_pnl[date]+=pnl
        y=date[:4]
        yearly[y]['n']+=1; yearly[y]['pnl']+=pnl
        if pnl>0: yearly[y]['w']+=1

n=len(trades);w=sum(1 for t in trades if t['win']);total=sum(t['pnl'] for t in trades)
losses=[t for t in trades if not t['win']]
wins_list=[t for t in trades if t['win']]
daily_vals=[v for v in daily_pnl.values() if v!=0]
cum=0;peak=0;dd=0
for d in sorted(daily_pnl.keys()):
    cum+=daily_pnl[d];peak=max(peak,cum);dd=max(dd,peak-cum)

print('='*70)
print('FINAL SETUP')
print('='*70)
print()
print('  RULES:')
print('    1. Stock trending DOWN (5-day)')
print('    2. ConsecDown 0-2 (not exhausted)')
print('    3. Yesterday range > 2% AND body ratio > 0.2')
print('    4. Price touches Camarilla R3 in first 50 min')
print('    5. SHORT at bar 10 (10:15 AM)')
print('    6. Target: 1.75% | Stop: 1.50%')
print('    7. Trail: at +1.00% MFE, lock +0.075%')
print()
print(f'  RESULTS:')
print(f'    Trades:        {n}')
print(f'    Wins:          {w} ({w/n*100:.1f}%)')
print(f'    Losses:        {n-w} ({(n-w)/n*100:.1f}%)')
print(f'    Per trade:     Rs {total/n:+,.0f}')
print(f'    Total (4yr):   Rs {total:+,.0f}')
print(f'    Per year:      Rs {total/4:+,.0f}')
print(f'    Per day:       Rs {sum(daily_vals)/len(daily_vals):+,.0f}')
print(f'    Trades/day:    {n/len(daily_vals):.1f}')
print(f'    Trading days:  {len(daily_vals)}')
print(f'    Green days:    {sum(1 for v in daily_vals if v>0)}/{len(daily_vals)} ({sum(1 for v in daily_vals if v>0)/len(daily_vals)*100:.0f}%)')
print(f'    Worst day:     Rs {min(daily_vals):+,.0f}')
print(f'    Best day:      Rs {max(daily_vals):+,.0f}')
print(f'    Max drawdown:  Rs {dd:,.0f}')
print(f'    Avg win:       Rs {sum(t["pnl"] for t in wins_list)/len(wins_list):+,.0f}')
print(f'    Avg loss:      Rs {sum(t["pnl"] for t in losses)/len(losses):+,.0f}')
print(f'    Win/Loss:      {sum(t["pnl"] for t in wins_list)/abs(sum(t["pnl"] for t in losses)):.0f}x')
print(f'    Capital:       Rs 10,00,000')

print(f'\n  YEARLY:')
print(f'    {"Year":>6} {"Trades":>7} {"WR":>5} {"Per trade":>10} {"Total P&L":>14}')
for y in sorted(yearly):
    m=yearly[y]
    wr=m['w']/m['n']*100
    per=m['pnl']/m['n']
    print(f'    {y:>6} {m["n"]:>7} {wr:>4.0f}% Rs{per:>+8,.0f} Rs{m["pnl"]:>+12,.0f}')

train=[t for t in trades if t['date']<'2025-01-01']
test=[t for t in trades if t['date']>='2025-01-01']
tr_w=sum(1 for t in train if t['win'])
te_w=sum(1 for t in test if t['win'])
print(f'\n  WALK-FORWARD:')
print(f'    Train (2022-2024): {len(train)} trades, WR={tr_w/len(train)*100:.1f}%, Rs {sum(t["pnl"] for t in train)/len(train):+,.0f}/trade')
print(f'    Test  (2025-2026): {len(test)} trades, WR={te_w/len(test)*100:.1f}%, Rs {sum(t["pnl"] for t in test)/len(test):+,.0f}/trade')

print(f'\n  COMPOUND (20% capital per trade, 5x leverage):')
for start in [500000, 1000000]:
    cap=float(start);peak_c=cap;max_dd_c=0
    for t in trades:
        pos=min(cap*0.20*5, POS)
        scale=pos/POS
        cap+=t['pnl']*scale
        cap=max(cap,10000)
        peak_c=max(peak_c,cap)
        max_dd_c=max(max_dd_c,(peak_c-cap)/peak_c*100)
    print(f'    Rs {start/100000:.0f}L -> Rs {cap:>12,.0f} ({(cap/start-1)*100:>+,.0f}%) | Max DD: {max_dd_c:.1f}%')
