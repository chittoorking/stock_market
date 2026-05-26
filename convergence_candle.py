"""Can candle shape at entry push MA convergence to 100%?"""
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

def avg(lst): return sum(lst)/len(lst) if lst else 0

wins=[]; losses=[]
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
        if abs(sma20-ema20_val)/sma20*100>0.2: continue
        vols=[daily_ohlc[sym][sd2[di-k]].get('volume',0) for k in range(1,21) if di-k>=0]
        if not vols or vols[0]==0: continue
        vr=vols[0]/max(1,sum(vols)/len(vols))
        if vr<1.0 or vr>=1.5: continue
        zone=(sma20+ema20_val)/2

        for j in range(6, min(15, len(db))):
            price=db[j]['close']
            if abs(price-zone)/price*100>1.0: continue
            bar=db[j]
            br=bar['high']-bar['low']
            if br==0: continue
            body=abs(bar['close']-bar['open'])
            body_ratio=body/br
            bar_size=br/price*100
            is_green=bar['close']>bar['open']
            upper_wick=(bar['high']-max(bar['close'],bar['open']))/br
            lower_wick=(min(bar['close'],bar['open'])-bar['low'])/br
            avg_vol=sum(db[k]['volume'] for k in range(max(0,j-5),j))/min(5,j) if j>0 else 1
            bvr=bar['volume']/max(1,avg_vol)

            entry=price
            if trend=='DOWN' and price>zone:
                bounce=not is_green  # RED bar = confirms down
                tp=entry*(1-0.50/100);sp=entry*(1+1.0/100)
                ep=db[min(69,len(db)-1)]['close']
                for k in range(j+1,min(len(db),70)):
                    if db[k]['low']<=tp: ep=tp;break
                    if db[k]['high']>=sp: ep=sp;break
                pnl=(entry-ep)/entry*100/100*POS-CHARGES
            elif trend=='UP' and price<zone:
                bounce=is_green  # GREEN bar = confirms up
                tp=entry*(1+0.50/100);sp=entry*(1-1.0/100)
                ep=db[min(69,len(db)-1)]['close']
                for k in range(j+1,min(len(db),70)):
                    if db[k]['high']>=tp: ep=tp;break
                    if db[k]['low']<=sp: ep=sp;break
                pnl=(ep-entry)/entry*100/100*POS-CHARGES
            else: continue

            t={'pnl':pnl,'win':pnl>0,'bounce':bounce,'body_ratio':round(body_ratio,2),
               'bar_size':round(bar_size,3),'upper_wick':round(upper_wick,2),
               'lower_wick':round(lower_wick,2),'bvr':round(bvr,2),'date':date,'sym':sym}
            if pnl>0: wins.append(t)
            else: losses.append(t)
            break

n=len(wins)+len(losses)
print(f'MA<0.2% + vol_above, window 9:45-10:45: {n} trades, {len(wins)} wins, {len(losses)} losses')
print()

print(f'{"Feature":>20} {"WINS":>8} {"LOSSES":>8} {"Diff":>6}')
print('-'*45)
for name,key in [('Body ratio','body_ratio'),('Bar size %','bar_size'),('Upper wick','upper_wick'),('Lower wick','lower_wick'),('Bar vol ratio','bvr')]:
    w=avg([t[key] for t in wins]); l=avg([t[key] for t in losses])
    diff=abs(w-l)/max(abs(w),0.001)*100
    marker=' <<<' if diff>30 else ''
    print(f'{name:>20} {w:>8.3f} {l:>8.3f} {diff:>5.0f}%{marker}')
wbc=sum(1 for t in wins if t['bounce'])/len(wins)*100 if wins else 0
lbc=sum(1 for t in losses if t['bounce'])/len(losses)*100 if losses else 0
print(f'{"Bounce confirms %":>20} {wbc:>7.1f}% {lbc:>7.1f}%')

print(f'\nEVERY LOSS:')
for t in losses:
    print(f'  {t["date"]} {t["sym"]:>12} bounce={t["bounce"]} body={t["body_ratio"]} size={t["bar_size"]}% vol={t["bvr"]}x')

print(f'\nFILTER TESTS:')
all_t=wins+losses
for fname, filt in [
    ('No filter', lambda t: True),
    ('Bounce confirms', lambda t: t['bounce']),
    ('Body > 0.4', lambda t: t['body_ratio']>0.4),
    ('Body > 0.5', lambda t: t['body_ratio']>0.5),
    ('Size > 0.2%', lambda t: t['bar_size']>0.2),
    ('Size > 0.3%', lambda t: t['bar_size']>0.3),
    ('Vol > 1.0x', lambda t: t['bvr']>1.0),
    ('Bounce + body>0.4', lambda t: t['bounce'] and t['body_ratio']>0.4),
    ('Bounce + body>0.5', lambda t: t['bounce'] and t['body_ratio']>0.5),
    ('Bounce + size>0.2', lambda t: t['bounce'] and t['bar_size']>0.2),
    ('Bounce + vol>1.0', lambda t: t['bounce'] and t['bvr']>1.0),
    ('Bounce+body>0.4+size>0.2', lambda t: t['bounce'] and t['body_ratio']>0.4 and t['bar_size']>0.2),
    ('Bounce+body>0.4+vol>1', lambda t: t['bounce'] and t['body_ratio']>0.4 and t['bvr']>1.0),
    ('Bounce+body>0.3+size>0.2+vol>1', lambda t: t['bounce'] and t['body_ratio']>0.3 and t['bar_size']>0.2 and t['bvr']>1.0),
    ('Body>0.4+size>0.2', lambda t: t['body_ratio']>0.4 and t['bar_size']>0.2),
    ('Body>0.4+vol>1', lambda t: t['body_ratio']>0.4 and t['bvr']>1.0),
]:
    sub=[t for t in all_t if filt(t)]
    if len(sub)<5: continue
    sw=sum(1 for t in sub if t['win'])
    wr=sw/len(sub)*100
    marker=' ***' if sw==len(sub) else ' <<<' if wr>=95 else ''
    print(f'  {fname:>35}: {len(sub):>3} trades, WR={wr:.0f}%{marker}')
