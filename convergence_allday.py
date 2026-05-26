"""Test MA convergence at different entry times — does 100% hold all day?"""
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

def run_test(ma_max, vol_lo, vol_hi, entry_start, entry_end, name):
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
            for j in range(entry_start, min(entry_end, len(db))):
                price=db[j]['close']
                if abs(price-zone)/price*100>1.0: continue
                if trend=='DOWN' and price>zone:
                    e=price;tp=e*(1-0.50/100);sp=e*(1+1.0/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(j+1,min(len(db),70)):
                        if db[k]['low']<=tp: ep=tp; break
                        if db[k]['high']>=sp: ep=sp; break
                    trades.append({'pnl':(e-ep)/e*100/100*POS-CHARGES,'win':(e-ep)/e*100/100*POS-CHARGES>0,'date':date}); break
                elif trend=='UP' and price<zone:
                    e=price;tp=e*(1+0.50/100);sp=e*(1-1.0/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(j+1,min(len(db),70)):
                        if db[k]['high']>=tp: ep=tp; break
                        if db[k]['low']<=sp: ep=sp; break
                    trades.append({'pnl':(ep-e)/e*100/100*POS-CHARGES,'win':(ep-e)/e*100/100*POS-CHARGES>0,'date':date}); break
    if not trades: return
    n=len(trades);w=sum(1 for t in trades if t['win'])
    train=[t for t in trades if t['date']<'2025-01-01']
    test=[t for t in trades if t['date']>='2025-01-01']
    tr_wr=sum(1 for t in train if t['win'])/len(train)*100 if train else 0
    te_wr=sum(1 for t in test if t['win'])/len(test)*100 if test else 0
    marker=' ***' if w==n else ' <<<' if w/n*100>=95 else ''
    print(f'  {name:>35}: {n:>3} trades, WR={w/n*100:.0f}%, Train={tr_wr:.0f}% Test={te_wr:.0f}%{marker}')

# Test Setup 1: MA<0.5% + vol_dry
print('SETUP 1: MA<0.5% + vol_dry(<0.6)')
print('='*80)
for s,e,label in [(1,10,'9:20-10:15'),(10,20,'10:15-11:15'),(20,30,'11:15-12:15'),(30,40,'12:15-1:15'),(40,50,'1:15-2:15'),(50,60,'2:15-3:15'),(1,69,'WHOLE DAY'),(1,30,'Morning'),(30,69,'Afternoon')]:
    run_test(0.5, 0.0, 0.6, s, e, label)

# Test Setup 2: MA<0.2% + vol_above
print()
print('SETUP 2: MA<0.2% + vol_above(1.0-1.5)')
print('='*80)
for s,e,label in [(1,10,'9:20-10:15'),(10,20,'10:15-11:15'),(20,30,'11:15-12:15'),(30,40,'12:15-1:15'),(40,50,'1:15-2:15'),(50,60,'2:15-3:15'),(1,69,'WHOLE DAY'),(1,30,'Morning'),(30,69,'Afternoon')]:
    run_test(0.2, 1.0, 1.5, s, e, label)

# Test Setup 3: MA<0.3% + vol_dry
print()
print('SETUP 3: MA<0.3% + vol_dry(<0.6)')
print('='*80)
for s,e,label in [(1,10,'9:20-10:15'),(10,20,'10:15-11:15'),(1,69,'WHOLE DAY'),(1,30,'Morning'),(30,69,'Afternoon')]:
    run_test(0.3, 0.0, 0.6, s, e, label)

# Combined: use widest setup across whole day
print()
print('COMBINED: MA<0.5% + (vol_dry OR vol_above_avg)')
print('='*80)
# Manual combo
trades_combo=[]
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
        if abs(sma20-ema20_val)/sma20*100>0.5: continue
        vols=[daily_ohlc[sym][sd2[di-k]].get('volume',0) for k in range(1,21) if di-k>=0]
        if not vols or vols[0]==0: continue
        vr=vols[0]/max(1,sum(vols)/len(vols))
        if not (vr<0.6 or (1.0<=vr<1.5)): continue  # vol_dry OR vol_above
        zone=(sma20+ema20_val)/2
        for j in range(1, min(69, len(db))):
            price=db[j]['close']
            if abs(price-zone)/price*100>1.0: continue
            if trend=='DOWN' and price>zone:
                e=price;tp=e*(1-0.50/100);sp=e*(1+1.0/100)
                ep=db[min(69,len(db)-1)]['close']
                for k in range(j+1,min(len(db),70)):
                    if db[k]['low']<=tp: ep=tp; break
                    if db[k]['high']>=sp: ep=sp; break
                trades_combo.append({'pnl':(e-ep)/e*100/100*POS-CHARGES,'win':(e-ep)/e*100/100*POS-CHARGES>0,'date':date}); break
            elif trend=='UP' and price<zone:
                e=price;tp=e*(1+0.50/100);sp=e*(1-1.0/100)
                ep=db[min(69,len(db)-1)]['close']
                for k in range(j+1,min(len(db),70)):
                    if db[k]['high']>=tp: ep=tp; break
                    if db[k]['low']<=sp: ep=sp; break
                trades_combo.append({'pnl':(ep-e)/e*100/100*POS-CHARGES,'win':(ep-e)/e*100/100*POS-CHARGES>0,'date':date}); break

n=len(trades_combo);w=sum(1 for t in trades_combo if t['win'])
train=[t for t in trades_combo if t['date']<'2025-01-01']
test=[t for t in trades_combo if t['date']>='2025-01-01']
tr_wr=sum(1 for t in train if t['win'])/len(train)*100 if train else 0
te_wr=sum(1 for t in test if t['win'])/len(test)*100 if test else 0
print(f'  WHOLE DAY combined: {n} trades, WR={w/n*100:.0f}%, Train={tr_wr:.0f}% Test={te_wr:.0f}%')
