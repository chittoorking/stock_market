"""Sweep entry bars for MA convergence — find best entry time."""
import sys,io,csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

data_dir=Path('data/5min'); date_bars=defaultdict(dict); daily_ohlc=defaultdict(dict); daily_trend={}
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
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_trend[(d,sym)]='UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'
all_dates=sorted(date_bars.keys())
POS=1000000;CHARGES=386

def ema(values, period):
    if len(values)<period: return None
    mult=2/(period+1); val=sum(values[:period])/period
    for v in values[period:]: val=v*mult+val*(1-mult)
    return val

def run(ma_max, vol_lo, vol_hi, entry_bar):
    trades=[]
    for date in all_dates:
        for sym in date_bars[date]:
            db=date_bars[date][sym]
            if len(db)<=69 or entry_bar>=len(db): continue
            trend=daily_trend.get((date,sym))
            if trend not in ('DOWN','UP'): continue
            sd2=sorted(daily_ohlc[sym].keys()); di=sd2.index(date) if date in sd2 else -1
            if di<52: continue
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
            closes=[daily_ohlc[sym][sd2[di-k]]['close'] for k in range(52,0,-1)]
            sma20=sum(closes[-20:])/20; ema20_val=ema(closes, 20)
            if not ema20_val: continue
            if abs(sma20-ema20_val)/sma20*100>ma_max: continue
            vols=[daily_ohlc[sym][sd2[di-k]].get('volume',0) for k in range(1,21) if di-k>=0]
            if not vols or vols[0]==0: continue
            vr=vols[0]/max(1,sum(vols)/len(vols))
            if vr<vol_lo or vr>=vol_hi: continue
            zone=(sma20+ema20_val)/2
            price=db[entry_bar]['close']
            if abs(price-zone)/price*100>1.0: continue
            entry=price
            if trend=='DOWN' and price>zone:
                tp=entry*(1-0.50/100);sp=entry*(1+1.0/100)
                ep=db[min(69,len(db)-1)]['close']
                for k in range(entry_bar+1,min(len(db),70)):
                    if db[k]['low']<=tp: ep=tp;break
                    if db[k]['high']>=sp: ep=sp;break
                trades.append({'pnl':(entry-ep)/entry*100/100*POS-CHARGES,'win':(entry-ep)/entry*100/100*POS-CHARGES>0,'date':date})
            elif trend=='UP' and price<zone:
                tp=entry*(1+0.50/100);sp=entry*(1-1.0/100)
                ep=db[min(69,len(db)-1)]['close']
                for k in range(entry_bar+1,min(len(db),70)):
                    if db[k]['high']>=tp: ep=tp;break
                    if db[k]['low']<=sp: ep=sp;break
                trades.append({'pnl':(ep-entry)/entry*100/100*POS-CHARGES,'win':(ep-entry)/entry*100/100*POS-CHARGES>0,'date':date})
    return trades

def run_window(ma_max, vol_lo, vol_hi, bar_start, bar_end):
    trades=[]
    for date in all_dates:
        for sym in date_bars[date]:
            db=date_bars[date][sym]
            if len(db)<=69: continue
            trend=daily_trend.get((date,sym))
            if trend not in ('DOWN','UP'): continue
            sd2=sorted(daily_ohlc[sym].keys()); di=sd2.index(date) if date in sd2 else -1
            if di<52: continue
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
            closes=[daily_ohlc[sym][sd2[di-k]]['close'] for k in range(52,0,-1)]
            sma20=sum(closes[-20:])/20; ema20_val=ema(closes, 20)
            if not ema20_val: continue
            if abs(sma20-ema20_val)/sma20*100>ma_max: continue
            vols=[daily_ohlc[sym][sd2[di-k]].get('volume',0) for k in range(1,21) if di-k>=0]
            if not vols or vols[0]==0: continue
            vr=vols[0]/max(1,sum(vols)/len(vols))
            if vr<vol_lo or vr>=vol_hi: continue
            zone=(sma20+ema20_val)/2
            for j in range(bar_start, min(bar_end, len(db))):
                price=db[j]['close']
                if abs(price-zone)/price*100>1.0: continue
                entry=price
                if trend=='DOWN' and price>zone:
                    tp=entry*(1-0.50/100);sp=entry*(1+1.0/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(j+1,min(len(db),70)):
                        if db[k]['low']<=tp: ep=tp;break
                        if db[k]['high']>=sp: ep=sp;break
                    trades.append({'pnl':(entry-ep)/entry*100/100*POS-CHARGES,'win':(entry-ep)/entry*100/100*POS-CHARGES>0,'date':date}); break
                elif trend=='UP' and price<zone:
                    tp=entry*(1+0.50/100);sp=entry*(1-1.0/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(j+1,min(len(db),70)):
                        if db[k]['high']>=tp: ep=tp;break
                        if db[k]['low']<=sp: ep=sp;break
                    trades.append({'pnl':(ep-entry)/entry*100/100*POS-CHARGES,'win':(ep-entry)/entry*100/100*POS-CHARGES>0,'date':date}); break
    return trades

def report(name, trades):
    if len(trades)<10: return
    n=len(trades);w=sum(1 for t in trades if t['win'])
    train=[t for t in trades if t['date']<'2025-01-01']
    test=[t for t in trades if t['date']>='2025-01-01']
    tr_wr=sum(1 for t in train if t['win'])/len(train)*100 if train else 0
    te_wr=sum(1 for t in test if t['win'])/len(test)*100 if test else 0
    marker=' ***' if w==n else ' <<<' if w/n*100>=95 else ' <<' if w/n*100>=90 else ''
    h=9+(entry_bar*5)//60 if 'entry_bar' in dir() else 0
    print(f'  {name:>25}: {n:>3} trades, WR={w/n*100:.0f}%, Train={tr_wr:.0f}% Test={te_wr:.0f}%{marker}')

# Per-bar sweep for all 3 setups
for sname, ma, vlo, vhi in [
    ('MA<0.5%+vol_dry', 0.5, 0.0, 0.6),
    ('MA<0.2%+vol_above', 0.2, 1.0, 1.5),
    ('MA<0.3%+vol_dry', 0.3, 0.0, 0.6),
]:
    print(f'\n{sname} — Per bar entry:')
    for b in range(1, 21):
        t=run(ma, vlo, vhi, b)
        if len(t)<10: continue
        n=len(t);w=sum(1 for x in t if x['win'])
        time_str=f'{9+(b*5)//60}:{15+(b*5)%60:02d}'
        train=[x for x in t if x['date']<'2025-01-01']
        test=[x for x in t if x['date']>='2025-01-01']
        tr_wr=sum(1 for x in train if x['win'])/len(train)*100 if train else 0
        te_wr=sum(1 for x in test if x['win'])/len(test)*100 if test else 0
        marker=' ***' if w==n else ' <<<' if w/n*100>=95 else ' <<' if w/n*100>=90 else ''
        print(f'  Bar {b:>2} ({time_str}): {n:>3} trades, WR={w/n*100:.0f}%, Tr={tr_wr:.0f}% Te={te_wr:.0f}%{marker}')

# Window sweep
print(f'\n\nWINDOW ENTRY (first touch in window):')
for sname, ma, vlo, vhi in [
    ('MA<0.5%+vol_dry', 0.5, 0.0, 0.6),
    ('MA<0.2%+vol_above', 0.2, 1.0, 1.5),
]:
    print(f'\n{sname}:')
    for s, e, label in [
        (3,6,'9:30-9:45'),(3,8,'9:30-9:55'),(3,10,'9:30-10:15'),
        (3,13,'9:30-10:30'),(3,15,'9:30-10:45'),
        (6,10,'9:45-10:15'),(6,13,'9:45-10:30'),(6,15,'9:45-10:45'),
        (8,13,'9:55-10:30'),(8,15,'9:55-10:45'),
        (10,13,'10:15-10:30'),(10,15,'10:15-10:45'),
        (10,20,'10:15-11:15'),
    ]:
        t=run_window(ma, vlo, vhi, s, e)
        if len(t)<10: continue
        n=len(t);w=sum(1 for x in t if x['win'])
        train=[x for x in t if x['date']<'2025-01-01']
        test=[x for x in t if x['date']>='2025-01-01']
        tr_wr=sum(1 for x in train if x['win'])/len(train)*100 if train else 0
        te_wr=sum(1 for x in test if x['win'])/len(test)*100 if test else 0
        marker=' ***' if w==n else ' <<<' if w/n*100>=95 else ' <<' if w/n*100>=90 else ''
        print(f'  {label:>15}: {n:>3} trades, WR={w/n*100:.0f}%, Tr={tr_wr:.0f}% Te={te_wr:.0f}%{marker}')
