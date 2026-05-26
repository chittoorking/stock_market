"""
CONVERGENCE v2 — Price MAs + Volume MA all converging.
When SMA20~EMA20 converge AND volume is at its average (not spiking, not dead),
the breakout is clean.
"""
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
        daily_ohlc[sym][d]={'open':bs[0]['open'],'close':bs[-1]['close'],'high':max(b['high'] for b in bs),'low':min(b['low'] for b in bs),
            'range':max(b['high'] for b in bs)-min(b['low'] for b in bs),'volume':sum(b['volume'] for b in bs)}
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

def ema(values, period):
    if len(values)<period: return None
    mult=2/(period+1)
    val=sum(values[:period])/period
    for v in values[period:]:
        val=v*mult+val*(1-mult)
    return val

print('='*80)
print('CONVERGENCE v2 — Price MAs + Volume conditions')
print('='*80)

for ma_spread in [0.2, 0.3, 0.5]:
    for vol_cond_name, vol_check in [
        ('any_vol', lambda vr: True),
        ('vol_normal(0.7-1.3)', lambda vr: 0.7<=vr<=1.3),
        ('vol_low(<0.8)', lambda vr: vr<0.8),
        ('vol_high(>1.2)', lambda vr: vr>1.2),
        ('vol_surge(>1.5)', lambda vr: vr>1.5),
        ('vol_dry(<0.6)', lambda vr: vr<0.6),
        ('vol_rising(yd>prev)', lambda vr: vr>1.0),
    ]:
        for target in [0.50, 0.75]:
            trades=[]
            for date in all_dates:
                for sym in date_bars[date]:
                    db=date_bars[date][sym]
                    if len(db)<=SB+20: continue
                    trend=daily_trend.get((date,sym))
                    if trend not in ('DOWN','UP'): continue
                    sd2=sorted(daily_ohlc[sym].keys()); di=sd2.index(date) if date in sd2 else -1
                    if di<52: continue
                    # CD filter
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
                    # Yd filter
                    pc=daily_ohlc[sym][sd2[di-1]]
                    yr=pc['range']/pc['close']*100 if pc['close']>0 else 0
                    yb=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
                    if yr<2.0 or yb<0.2: continue

                    closes=[daily_ohlc[sym][sd2[di-k]]['close'] for k in range(52,0,-1)]
                    sma20=sum(closes[-20:])/20
                    ema20_val=ema(closes, 20)
                    if not ema20_val: continue

                    # Price MA convergence
                    spread=abs(sma20-ema20_val)/sma20*100
                    if spread>ma_spread: continue

                    # Volume condition
                    vols=[daily_ohlc[sym][sd2[di-k]].get('volume',0) for k in range(1,21) if di-k>=0]
                    if not vols or vols[0]==0: continue
                    vol_ma20=sum(vols)/len(vols)
                    vol_ratio=vols[0]/max(1,vol_ma20)

                    # Volume EMA
                    vol_ema=ema(vols[::-1], 10) if len(vols)>=10 else vol_ma20
                    vol_ema_ratio=vols[0]/max(1,vol_ema) if vol_ema else 1

                    if not vol_check(vol_ratio): continue

                    zone=(sma20+ema20_val)/2
                    price=db[0]['open']
                    if abs(price-zone)/price*100>1.0: continue

                    entry=db[SB]['close']
                    if trend=='DOWN' and price>zone:
                        tp=entry*(1-target/100);sp=entry*(1+1.0/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['low']<=tp: ep=tp;break
                            if db[k]['high']>=sp: ep=sp;break
                        trades.append((entry-ep)/entry*100/100*POS-CHARGES)
                    elif trend=='UP' and price<zone:
                        tp=entry*(1+target/100);sp=entry*(1-1.0/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['high']>=tp: ep=tp;break
                            if db[k]['low']<=sp: ep=sp;break
                        trades.append((ep-entry)/entry*100/100*POS-CHARGES)

            if len(trades)>=15:
                w=sum(1 for t in trades if t>0);n=len(trades)
                if w/n*100>=85:
                    # Walk forward
                    split=int(n*0.75)
                    train=trades[:split]; test=trades[split:]
                    tr_w=sum(1 for t in train if t>0)
                    te_w=sum(1 for t in test if t>0)
                    tr_wr=tr_w/len(train)*100
                    te_wr=te_w/len(test)*100 if test else 0
                    marker=' <<<' if w/n*100>=90 else ''
                    print(f'  MA<{ma_spread}% {vol_cond_name:>20} T={target}: {n:>4} trades, WR={w/n*100:.0f}%, Rs{sum(trades)/n:>+7,.0f}/tr | TrWR={tr_wr:.0f}% TeWR={te_wr:.0f}%{marker}')

# ═══ BEST COMBO: Tight MA + volume drying up (compression before breakout) ═══
print(f'\n{"="*80}')
print('BEST COMBO SEARCH — MA convergence + volume pattern')
print('='*80)

for ma_spread in [0.2, 0.3]:
    for vol_lo, vol_hi, vol_name in [
        (0.0, 0.6, 'vol_dry'),
        (0.0, 0.8, 'vol_quiet'),
        (0.6, 1.0, 'vol_below_avg'),
        (0.8, 1.2, 'vol_normal'),
        (1.0, 1.5, 'vol_above_avg'),
        (1.5, 99, 'vol_surge'),
    ]:
        for target in [0.50]:
            trades=[];dates_list=[]
            for date in all_dates:
                for sym in date_bars[date]:
                    db=date_bars[date][sym]
                    if len(db)<=SB+20: continue
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
                    sma20=sum(closes[-20:])/20
                    ema20_val=ema(closes, 20)
                    if not ema20_val: continue
                    spread=abs(sma20-ema20_val)/sma20*100
                    if spread>ma_spread: continue
                    vols=[daily_ohlc[sym][sd2[di-k]].get('volume',0) for k in range(1,21) if di-k>=0]
                    if not vols or vols[0]==0: continue
                    vol_ratio=vols[0]/max(1,sum(vols)/len(vols))
                    if vol_ratio<vol_lo or vol_ratio>=vol_hi: continue
                    zone=(sma20+ema20_val)/2
                    price=db[0]['open']
                    if abs(price-zone)/price*100>1.0: continue
                    entry=db[SB]['close']
                    if trend=='DOWN' and price>zone:
                        tp=entry*(1-target/100);sp=entry*(1+1.0/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['low']<=tp: ep=tp;break
                            if db[k]['high']>=sp: ep=sp;break
                        trades.append((entry-ep)/entry*100/100*POS-CHARGES)
                        dates_list.append(date)
                    elif trend=='UP' and price<zone:
                        tp=entry*(1+target/100);sp=entry*(1-1.0/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['high']>=tp: ep=tp;break
                            if db[k]['low']<=sp: ep=sp;break
                        trades.append((ep-entry)/entry*100/100*POS-CHARGES)
                        dates_list.append(date)

            if len(trades)>=15:
                w=sum(1 for t in trades if t>0);n=len(trades)
                if w/n*100>=85:
                    # Walk forward by date
                    train=[t for t,d in zip(trades,dates_list) if d<'2025-01-01']
                    test=[t for t,d in zip(trades,dates_list) if d>='2025-01-01']
                    tr_wr=sum(1 for t in train if t>0)/len(train)*100 if train else 0
                    te_wr=sum(1 for t in test if t>0)/len(test)*100 if test else 0
                    marker=' <<<' if w/n*100>=92 else ' <<' if w/n*100>=90 else ''
                    print(f'  MA<{ma_spread}% {vol_name:>15} T={target}: {n:>4} trades, WR={w/n*100:.0f}%, Rs{sum(trades)/n:>+7,.0f}/tr | Train={tr_wr:.0f}% Test={te_wr:.0f}%{marker}')

print(f'\n{"="*80}')
print('DONE')
