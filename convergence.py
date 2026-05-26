"""
CONVERGENCE STRATEGY — When multiple MAs converge at the same zone,
it creates a WALL of support/resistance. Price bounces hard.

Test: What happens when 20 DMA, 50 DMA, and EMA all cluster together
and price touches that zone?
"""
import sys,io,csv,math
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
        daily_ohlc[sym][d]={'open':bs[0]['open'],'close':bs[-1]['close'],'high':max(b['high'] for b in bs),'low':min(b['low'] for b in bs),'range':max(b['high'] for b in bs)-min(b['low'] for b in bs)}
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

def ema(closes, period):
    """Calculate EMA."""
    if len(closes) < period: return None
    mult = 2 / (period + 1)
    val = sum(closes[:period]) / period
    for c in closes[period:]:
        val = c * mult + val * (1 - mult)
    return val

print('='*80)
print('CONVERGENCE STRATEGY — Multiple MAs clustering = strong level')
print('='*80)

# For each day, calculate multiple MAs and check if they converge
for max_spread in [0.5, 0.75, 1.0, 1.5, 2.0]:
    for min_mas in [2, 3, 4, 5]:
        for target in [0.50, 0.75, 1.00]:
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

                    # Yesterday filter
                    pc=daily_ohlc[sym][sd2[di-1]]
                    yr=pc['range']/pc['close']*100 if pc['close']>0 else 0
                    yb=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
                    if yr<2.0 or yb<0.2: continue

                    closes=[daily_ohlc[sym][sd2[di-k]]['close'] for k in range(52,0,-1)]
                    price=db[0]['open']

                    # Calculate multiple MAs
                    sma20=sum(closes[-20:])/20
                    sma50=sum(closes[-50:])/50
                    ema9=ema(closes, 9)
                    ema20=ema(closes, 20)
                    ema50=ema(closes, 50)

                    if not all([sma20,sma50,ema9,ema20,ema50]): continue

                    # Also add Camarilla and Pivot levels
                    pd2=prev_day.get((date,sym))
                    if not pd2: continue
                    rng=pd2['high']-pd2['low']
                    if rng<=0: continue
                    r3=pd2['close']+rng*1.1/4; s3=pd2['close']-rng*1.1/4
                    pp=(pd2['high']+pd2['low']+pd2['close'])/3

                    # Collect all levels
                    all_levels=[sma20, sma50, ema9, ema20, ema50, pp]
                    level_names=['SMA20','SMA50','EMA9','EMA20','EMA50','PP']

                    # How many levels are within max_spread% of each other?
                    # Find the tightest cluster
                    center=sum(all_levels)/len(all_levels)
                    near=[]
                    for lv,ln in zip(all_levels,level_names):
                        if abs(lv-center)/center*100 < max_spread:
                            near.append((lv,ln))

                    if len(near)<min_mas: continue

                    # The convergence zone
                    zone_center=sum(lv for lv,_ in near)/len(near)

                    # Price must be near this zone
                    if abs(price-zone_center)/price*100 > 1.5: continue

                    # Trade: if price above zone and trend DOWN → SHORT to zone
                    # If price below zone and trend UP → LONG to zone
                    entry=db[SB]['close']
                    if trend=='DOWN' and price>zone_center:
                        tp=entry*(1-target/100);sp=entry*(1+1.0/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['low']<=tp: ep=tp;break
                            if db[k]['high']>=sp: ep=sp;break
                        pnl=(entry-ep)/entry*100/100*POS-CHARGES
                        trades.append(pnl)
                    elif trend=='UP' and price<zone_center:
                        tp=entry*(1+target/100);sp=entry*(1-1.0/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['high']>=tp: ep=tp;break
                            if db[k]['low']<=sp: ep=sp;break
                        pnl=(ep-entry)/entry*100/100*POS-CHARGES
                        trades.append(pnl)

            if len(trades)>=15:
                w=sum(1 for t in trades if t>0);n=len(trades)
                if w/n*100>=75:
                    marker=' <<<' if w/n*100>=85 else ' <<' if w/n*100>=80 else ''
                    print(f'  Spread<{max_spread}% MAs>={min_mas} T={target}: {n:>4} trades, WR={w/n*100:.0f}%, Rs{sum(trades)/n:>+7,.0f}/tr{marker}')

# ═══ SPECIFIC CONVERGENCE: Price at SMA20=EMA20=SMA50 intersection ═══
print(f'\n{"="*80}')
print('TIGHT CONVERGENCE — SMA20 and EMA20 within X% of each other')
print('Price touching this double-MA zone')
print('='*80)

for ma_spread in [0.2, 0.3, 0.5, 0.75, 1.0]:
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
                cd=0
                for back in range(1,20):
                    if di-back<1: break
                    if trend=='DOWN':
                        if daily_ohlc[sym][sd2[di-back]]['close']<daily_ohlc[sym][sd2[di-back-1]]['close']: cd+=1
                        else: break
                    else:
                        if daily_ohlc[sym][sd2[di-back]]['close']>daily_ohlc[sym][sd2[di-back-1]]['close']: cd+=1
                        else: break
                    if cd>2: break
                if cd>2: continue
                pc=daily_ohlc[sym][sd2[di-1]]
                yr=pc['range']/pc['close']*100 if pc['close']>0 else 0
                yb=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
                if yr<2.0 or yb<0.2: continue

                closes=[daily_ohlc[sym][sd2[di-k]]['close'] for k in range(52,0,-1)]
                sma20=sum(closes[-20:])/20
                ema20_val=ema(closes, 20)
                sma50=sum(closes[-50:])/50
                if not ema20_val: continue

                # Check if SMA20 and EMA20 converge
                spread_20=abs(sma20-ema20_val)/sma20*100
                if spread_20>ma_spread: continue

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
            if w/n*100>=75:
                marker=' <<<' if w/n*100>=88 else ' <<' if w/n*100>=85 else ' <' if w/n*100>=80 else ''
                print(f'  SMA20~EMA20 spread<{ma_spread}% T={target}: {n:>4} trades, WR={w/n*100:.0f}%, Rs{sum(trades)/n:>+7,.0f}/tr{marker}')

# ═══ TRIPLE CONVERGENCE: SMA20 + EMA20 + SMA50 all near each other ═══
print(f'\n{"="*80}')
print('TRIPLE CONVERGENCE — SMA20 + EMA20 + SMA50 all within X%')
print('='*80)

for spread in [0.5, 0.75, 1.0, 1.5]:
    for target in [0.50, 0.75, 1.00]:
        trades=[]
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
                sma50=sum(closes[-50:])/50
                if not ema20_val: continue

                # All 3 must be within spread%
                center=(sma20+ema20_val+sma50)/3
                if abs(sma20-center)/center*100>spread: continue
                if abs(ema20_val-center)/center*100>spread: continue
                if abs(sma50-center)/center*100>spread: continue

                price=db[0]['open']
                if abs(price-center)/price*100>1.5: continue

                entry=db[SB]['close']
                if trend=='DOWN' and price>center:
                    tp=entry*(1-target/100);sp=entry*(1+1.0/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(SB+1,min(len(db),70)):
                        if db[k]['low']<=tp: ep=tp;break
                        if db[k]['high']>=sp: ep=sp;break
                    trades.append((entry-ep)/entry*100/100*POS-CHARGES)
                elif trend=='UP' and price<center:
                    tp=entry*(1+target/100);sp=entry*(1-1.0/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(SB+1,min(len(db),70)):
                        if db[k]['high']>=tp: ep=tp;break
                        if db[k]['low']<=sp: ep=sp;break
                    trades.append((ep-entry)/entry*100/100*POS-CHARGES)

        if len(trades)>=15:
            w=sum(1 for t in trades if t>0);n=len(trades)
            if w/n*100>=75:
                marker=' <<<' if w/n*100>=88 else ' <<' if w/n*100>=85 else ' <' if w/n*100>=80 else ''
                print(f'  Triple spread<{spread}% T={target}: {n:>4} trades, WR={w/n*100:.0f}%, Rs{sum(trades)/n:>+7,.0f}/tr{marker}')

print(f'\n{"="*80}')
print('DONE')
