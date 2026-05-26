"""Hunt for strategy #5 — different time windows, different setups."""
import sys,io,csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

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
        daily_ohlc[sym][d]={'open':bs[0]['open'],'close':bs[-1]['close'],'high':max(b['high'] for b in bs),'low':min(b['low'] for b in bs),'range':max(b['high'] for b in bs)-min(b['low'] for b in bs),'volume':sum(b['volume'] for b in bs)}
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
POS=1000000;CHARGES=386;SB=10

def report(name, trades):
    if len(trades)<20: return
    w=sum(1 for t in trades if t['pnl']>0);n=len(trades)
    total=sum(t['pnl'] for t in trades)
    train=[t for t in trades if t['date']<'2025-01-01']
    test=[t for t in trades if t['date']>='2025-01-01']
    tr_wr=sum(1 for t in train if t['pnl']>0)/len(train)*100 if train else 0
    te_wr=sum(1 for t in test if t['pnl']>0)/len(test)*100 if test else 0
    marker=' <<<' if w/n*100>=85 else ' <' if w/n*100>=75 else ''
    print(f'  {name:>50}: {n:>4} tr, WR={w/n*100:.0f}%, Rs{total/n:>+6,.0f} Te={te_wr:.0f}%{marker}')

# ═══ 1. EOD MOMENTUM with our filters ═══
print('1. EOD MOMENTUM — 2:30 PM entry, trend + CD + yd filter')
print('='*70)
for min_move in [0.5, 1.0, 1.5, 2.0]:
    for target in [0.15, 0.25, 0.50]:
        trades=[]
        for date in all_dates:
            for sym in date_bars[date]:
                db=date_bars[date][sym]
                if len(db)<=65: continue
                trend=daily_trend.get((date,sym))
                if trend not in ('DOWN','UP'): continue
                sd2=sorted(daily_ohlc[sym].keys()); di=sd2.index(date) if date in sd2 else -1
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
                day_move=(db[63]['close']-db[0]['open'])/db[0]['open']*100
                if trend=='DOWN' and day_move>-min_move: continue
                if trend=='UP' and day_move<min_move: continue
                entry=db[63]['close']
                if trend=='DOWN':
                    tp=entry*(1-target/100);sp=entry*(1+0.50/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(64,min(len(db),70)):
                        if db[k]['low']<=tp: ep=tp;break
                        if db[k]['high']>=sp: ep=sp;break
                    trades.append({'pnl':(entry-ep)/entry*100/100*POS-CHARGES,'date':date})
                else:
                    tp=entry*(1+target/100);sp=entry*(1-0.50/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(64,min(len(db),70)):
                        if db[k]['high']>=tp: ep=tp;break
                        if db[k]['low']<=sp: ep=sp;break
                    trades.append({'pnl':(ep-entry)/entry*100/100*POS-CHARGES,'date':date})
        report(f'DayMove>{min_move}% T={target}', trades)

# ═══ 2. DOUBLE TOUCH R3/S3 ═══
print(f'\n2. DOUBLE TOUCH — R3/S3 touched 2+ times = confirmed level')
print('='*70)
for min_touches in [2, 3]:
    for target in [0.50, 0.75, 1.00, 1.75]:
        for stop in [1.0, 1.5]:
            trades=[]
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
                    sd2=sorted(daily_ohlc[sym].keys()); di=sd2.index(date) if date in sd2 else -1
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
                    r3=pd['close']+rng*1.1/4; s3=pd['close']-rng*1.1/4
                    if trend=='DOWN':
                        touches=0
                        for j in range(1,SB+1):
                            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                            if abs(db[j]['high']-r3)<atr*0.4: touches+=1
                        if touches<min_touches: continue
                        entry=db[SB]['close']
                        tp=entry*(1-target/100);sp=entry*(1+stop/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['low']<=tp: ep=tp;break
                            if db[k]['high']>=sp: ep=sp;break
                        trades.append({'pnl':(entry-ep)/entry*100/100*POS-CHARGES,'date':date})
                    else:
                        touches=0
                        for j in range(1,SB+1):
                            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                            if abs(db[j]['low']-s3)<atr*0.4: touches+=1
                        if touches<min_touches: continue
                        entry=db[SB]['close']
                        tp=entry*(1+target/100);sp=entry*(1-stop/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['high']>=tp: ep=tp;break
                            if db[k]['low']<=sp: ep=sp;break
                        trades.append({'pnl':(ep-entry)/entry*100/100*POS-CHARGES,'date':date})
            report(f'Touch>={min_touches} T={target} S={stop}', trades)

# ═══ 3. LAST HOUR REVERSAL ═══
print(f'\n3. LAST HOUR REVERSAL — Stock fell all day, buy at 2 PM')
print('='*70)
for min_drop in [1.0, 1.5, 2.0, 2.5]:
    for target in [0.25, 0.50]:
        trades=[]
        for date in all_dates:
            for sym in date_bars[date]:
                db=date_bars[date][sym]
                if len(db)<=60: continue
                day_drop=(db[54]['close']-db[0]['open'])/db[0]['open']*100
                if day_drop>-min_drop: continue
                # Also check: stock must be in UP 5-day trend (counter-trend day)
                trend=daily_trend.get((date,sym))
                if trend!='UP': continue
                entry=db[54]['close']
                tp=entry*(1+target/100);sp=entry*(1-0.75/100)
                ep=db[min(69,len(db)-1)]['close']
                for k in range(55,min(len(db),70)):
                    if db[k]['high']>=tp: ep=tp;break
                    if db[k]['low']<=sp: ep=sp;break
                trades.append({'pnl':(ep-entry)/entry*100/100*POS-CHARGES,'date':date})
        report(f'Drop>{min_drop}%+UP_trend T={target}', trades)

# ═══ 4. FIRST 30 MIN RANGE HOLD ═══
print(f'\n4. FIRST 30-MIN RANGE HOLD — Range holds for 30 more min then break')
print('='*70)
for target in [0.50, 0.75]:
    trades=[]
    for date in all_dates:
        for sym in date_bars[date]:
            db=date_bars[date][sym]
            if len(db)<=30: continue
            trend=daily_trend.get((date,sym))
            if trend not in ('DOWN','UP'): continue
            # First 6 bars range (30 min)
            fh=max(db[j]['high'] for j in range(6))
            fl=min(db[j]['low'] for j in range(6))
            # Check if range holds for next 6 bars (30 min)
            held=True
            for j in range(6,12):
                if j>=len(db): held=False;break
                if db[j]['high']>fh*1.002 or db[j]['low']<fl*0.998: held=False;break
            if not held: continue
            # Breakout after bar 12
            if len(db)<=12: continue
            entry=db[12]['close']
            if trend=='UP' and entry>fh:
                tp=entry*(1+target/100);sp=entry*(1-0.50/100)
                ep=db[min(69,len(db)-1)]['close']
                for k in range(13,min(len(db),70)):
                    if db[k]['high']>=tp: ep=tp;break
                    if db[k]['low']<=sp: ep=sp;break
                trades.append({'pnl':(ep-entry)/entry*100/100*POS-CHARGES,'date':date})
            elif trend=='DOWN' and entry<fl:
                tp=entry*(1-target/100);sp=entry*(1+0.50/100)
                ep=db[min(69,len(db)-1)]['close']
                for k in range(13,min(len(db),70)):
                    if db[k]['low']<=tp: ep=tp;break
                    if db[k]['high']>=sp: ep=sp;break
                trades.append({'pnl':(entry-ep)/entry*100/100*POS-CHARGES,'date':date})
    report(f'30min hold breakout T={target}', trades)

# ═══ 5. VOLUME CLIMAX + REVERSAL ═══
print(f'\n5. VOLUME CLIMAX — Huge volume bar in first hour + reversal')
print('='*70)
for vol_mult in [3.0, 4.0, 5.0]:
    for target in [0.25, 0.50]:
        trades=[]
        for date in all_dates:
            for sym in date_bars[date]:
                db=date_bars[date][sym]
                if len(db)<=SB+20: continue
                avg_vol=sum(db[k]['volume'] for k in range(SB))/SB if SB>0 else 1
                if avg_vol==0: continue
                # Find climax bar in first hour
                climax_bar=None
                for j in range(1,SB+1):
                    if db[j]['volume']>avg_vol*vol_mult:
                        climax_bar=j;break
                if climax_bar is None: continue
                # Climax bar must close in OPPOSITE of its range (reversal)
                cb=db[climax_bar]
                bar_up=cb['close']>cb['open']
                entry=db[SB]['close']
                if bar_up:  # Climax buy → SHORT reversal
                    tp=entry*(1-target/100);sp=entry*(1+0.75/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(SB+1,min(len(db),70)):
                        if db[k]['low']<=tp: ep=tp;break
                        if db[k]['high']>=sp: ep=sp;break
                    trades.append({'pnl':(entry-ep)/entry*100/100*POS-CHARGES,'date':date})
                else:  # Climax sell → LONG reversal
                    tp=entry*(1+target/100);sp=entry*(1-0.75/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(SB+1,min(len(db),70)):
                        if db[k]['high']>=tp: ep=tp;break
                        if db[k]['low']<=sp: ep=sp;break
                    trades.append({'pnl':(ep-entry)/entry*100/100*POS-CHARGES,'date':date})
        report(f'VolClimax>{vol_mult}x T={target}', trades)

print('\nDONE')
