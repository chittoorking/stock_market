"""Test more structural trade setups."""
import sys,io,csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

data_dir=Path('data/5min'); all_data={}; date_bars=defaultdict(dict); daily_ohlc=defaultdict(dict); prev_day={}; daily_trend={}
for f in sorted(data_dir.glob('*.csv')):
    sym=f.stem.split('_')[0].upper()
    if sym in ('NIFTY_50','NIFTY_BANK'): continue
    with open(f) as fh: rows=list(csv.DictReader(fh))
    by_d=defaultdict(list)
    for r in rows:
        by_d[r['timestamp'][:10]].append({'open':float(r['open']),'high':float(r['high']),'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))})
    all_data[sym]=rows
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

def test(name, get_trades):
    trades=get_trades()
    if len(trades)<20: return
    w=sum(1 for t in trades if t>0);n=len(trades);total=sum(trades)
    train=[t for i,t in enumerate(trades) if i<len(trades)*0.75]
    test_t=[t for i,t in enumerate(trades) if i>=len(trades)*0.75]
    tr_w=sum(1 for t in train if t>0)
    te_w=sum(1 for t in test_t if t>0)
    tr_wr=tr_w/len(train)*100 if train else 0
    te_wr=te_w/len(test_t)*100 if test_t else 0
    marker=' <<<' if w/n*100>=80 else ' <' if w/n*100>=70 else ''
    print(f'  {name:>50}: {n:>5} trades, WR={w/n*100:.0f}%, Rs{total/n:>+7,.0f}/tr Te={te_wr:.0f}%{marker}')

# ═══ 1. PREV CLOSE AS SUPPORT/RESISTANCE ═══
print('='*80)
print('1. PREVIOUS CLOSE — Price returns to yesterday close')
print('='*80)
for target in [0.50, 0.75]:
    for stop in [0.75, 1.0]:
        def make_trades(t=target,s=stop):
            trades=[]
            for date in all_dates:
                for sym in date_bars[date]:
                    db=date_bars[date][sym]; pd=prev_day.get((date,sym))
                    if not pd or len(db)<=SB+20: continue
                    trend=daily_trend.get((date,sym))
                    if trend not in ('DOWN','UP'): continue
                    # CD + yesterday filter
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
                    if yr<2.0: continue
                    # Price touches prev close
                    prev_c=pd['close']
                    for j in range(1,SB+1):
                        atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                        if trend=='DOWN' and abs(db[j]['high']-prev_c)<atr*0.3 and db[j]['close']<prev_c:
                            e=db[SB]['close'];tp=e*(1-t/100);sp=e*(1+s/100)
                            ep=db[min(69,len(db)-1)]['close']
                            for k in range(SB+1,min(len(db),70)):
                                if db[k]['low']<=tp: ep=tp;break
                                if db[k]['high']>=sp: ep=sp;break
                            trades.append((e-ep)/e*100/100*POS-CHARGES);break
                        if trend=='UP' and abs(db[j]['low']-prev_c)<atr*0.3 and db[j]['close']>prev_c:
                            e=db[SB]['close'];tp=e*(1+t/100);sp=e*(1-s/100)
                            ep=db[min(69,len(db)-1)]['close']
                            for k in range(SB+1,min(len(db),70)):
                                if db[k]['high']>=tp: ep=tp;break
                                if db[k]['low']<=sp: ep=sp;break
                            trades.append((ep-e)/e*100/100*POS-CHARGES);break
            return trades
        test(f'PrevClose T={target} S={stop}', make_trades)

# ═══ 2. VWAP BOUNCE ═══
print(f'\n{"="*80}')
print('2. VWAP BOUNCE — Price touches VWAP and bounces in trend direction')
print('='*80)
for target in [0.50, 0.75, 1.00]:
    def make_trades(t=target):
        trades=[]
        for date in all_dates:
            for sym in date_bars[date]:
                db=date_bars[date][sym]
                if len(db)<=SB+20: continue
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
                if yr<2.0: continue
                # VWAP = cumulative (price * volume) / cumulative volume
                cum_pv=0; cum_v=0
                for j in range(SB+1):
                    tp_bar=(db[j]['high']+db[j]['low']+db[j]['close'])/3
                    cum_pv+=tp_bar*db[j]['volume']; cum_v+=db[j]['volume']
                if cum_v==0: continue
                vwap=cum_pv/cum_v
                # Check if bar 9 or 10 touches VWAP
                for j in range(max(1,SB-2), SB+1):
                    atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                    if trend=='DOWN' and abs(db[j]['high']-vwap)<atr*0.3 and db[j]['close']<vwap:
                        e=db[SB]['close'];tp2=e*(1-t/100);sp2=e*(1+1.0/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['low']<=tp2: ep=tp2;break
                            if db[k]['high']>=sp2: ep=sp2;break
                        trades.append((e-ep)/e*100/100*POS-CHARGES);break
                    if trend=='UP' and abs(db[j]['low']-vwap)<atr*0.3 and db[j]['close']>vwap:
                        e=db[SB]['close'];tp2=e*(1+t/100);sp2=e*(1-1.0/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['high']>=tp2: ep=tp2;break
                            if db[k]['low']<=sp2: ep=sp2;break
                        trades.append((ep-e)/e*100/100*POS-CHARGES);break
        return trades
    test(f'VWAP bounce T={target}', make_trades)

# ═══ 3. CAMARILLA R4/S4 EXTREME ═══
print(f'\n{"="*80}')
print('3. CAM R4/S4 EXTREME — Price way overextended, snap back')
print('='*80)
for target in [0.50, 0.75, 1.00]:
    def make_trades(t=target):
        trades=[]
        for date in all_dates:
            for sym in date_bars[date]:
                db=date_bars[date][sym]; pd=prev_day.get((date,sym))
                if not pd or len(db)<=SB+20: continue
                rng=pd['high']-pd['low']
                if rng<=0: continue
                r4=pd['close']+rng*1.1/2; s4=pd['close']-rng*1.1/2
                # Price touched R4 → SHORT (overextended)
                for j in range(1,SB+1):
                    atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                    if db[j]['high']>r4 and db[j]['close']<r4:
                        e=db[SB]['close'];tp=e*(1-t/100);sp=e*(1+1.0/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['low']<=tp: ep=tp;break
                            if db[k]['high']>=sp: ep=sp;break
                        trades.append((e-ep)/e*100/100*POS-CHARGES);break
                    if db[j]['low']<s4 and db[j]['close']>s4:
                        e=db[SB]['close'];tp=e*(1+t/100);sp=e*(1-1.0/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['high']>=tp: ep=tp;break
                            if db[k]['low']<=sp: ep=sp;break
                        trades.append((ep-e)/e*100/100*POS-CHARGES);break
        return trades
    test(f'CAM R4/S4 extreme T={target}', make_trades)

# ═══ 4. CPR NARROW RANGE BREAKOUT ═══
print(f'\n{"="*80}')
print('4. CPR NARROW — Yesterday pivot range was tight, breakout today')
print('='*80)
for target in [0.50, 0.75, 1.00]:
    def make_trades(t=target):
        trades=[]
        for date in all_dates:
            for sym in date_bars[date]:
                db=date_bars[date][sym]; pd=prev_day.get((date,sym))
                if not pd or len(db)<=SB+20: continue
                trend=daily_trend.get((date,sym))
                if trend not in ('DOWN','UP'): continue
                # CPR
                pp=(pd['high']+pd['low']+pd['close'])/3
                bc=(pd['high']+pd['low'])/2
                tc=2*pp-bc
                cpr_width=abs(tc-bc)/pd['close']*100
                if cpr_width>0.3: continue  # Only narrow CPR
                # Breakout from CPR in trend direction
                for j in range(1,SB+1):
                    if trend=='DOWN' and db[j]['low']<min(tc,bc) and db[j]['close']<min(tc,bc):
                        e=db[SB]['close'];tp=e*(1-t/100);sp=e*(1+1.0/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['low']<=tp: ep=tp;break
                            if db[k]['high']>=sp: ep=sp;break
                        trades.append((e-ep)/e*100/100*POS-CHARGES);break
                    if trend=='UP' and db[j]['high']>max(tc,bc) and db[j]['close']>max(tc,bc):
                        e=db[SB]['close'];tp=e*(1+t/100);sp=e*(1-1.0/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(SB+1,min(len(db),70)):
                            if db[k]['high']>=tp: ep=tp;break
                            if db[k]['low']<=sp: ep=sp;break
                        trades.append((ep-e)/e*100/100*POS-CHARGES);break
        return trades
    test(f'CPR narrow breakout T={target}', make_trades)

# ═══ 5. 20 DMA PROXIMITY ═══
print(f'\n{"="*80}')
print('5. 20-DAY MA — Stock near 20 DMA + trend + bounce')
print('='*80)
for target in [0.50, 0.75, 1.00]:
    def make_trades(t=target):
        trades=[]
        for date in all_dates:
            for sym in date_bars[date]:
                db=date_bars[date][sym]
                if len(db)<=SB+20: continue
                trend=daily_trend.get((date,sym))
                if trend not in ('DOWN','UP'): continue
                sd2=sorted(daily_ohlc[sym].keys()); di=sd2.index(date) if date in sd2 else -1
                if di<22: continue
                # 20 DMA
                ma20=sum(daily_ohlc[sym][sd2[di-k]]['close'] for k in range(1,21))/20
                price=db[0]['open']
                dist=abs(price-ma20)/price*100
                if dist>1.0: continue  # Only near 20 DMA
                # CD + yd filter
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
                yr=pc['range']/pc['close']*100
                if yr<2.0: continue
                if trend=='DOWN' and price>ma20:
                    e=db[SB]['close'];tp=e*(1-t/100);sp=e*(1+1.0/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(SB+1,min(len(db),70)):
                        if db[k]['low']<=tp: ep=tp;break
                        if db[k]['high']>=sp: ep=sp;break
                    trades.append((e-ep)/e*100/100*POS-CHARGES)
                elif trend=='UP' and price<ma20:
                    e=db[SB]['close'];tp=e*(1+t/100);sp=e*(1-1.0/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(SB+1,min(len(db),70)):
                        if db[k]['high']>=tp: ep=tp;break
                        if db[k]['low']<=sp: ep=sp;break
                    trades.append((ep-e)/e*100/100*POS-CHARGES)
        return trades
    test(f'20 DMA bounce T={target}', make_trades)

# ═══ 6. FIRST 5-MIN HIGH/LOW HOLD ═══
print(f'\n{"="*80}')
print('6. FIRST BAR HIGH/LOW — If first bar high holds for 10 bars → SHORT')
print('='*80)
for target in [0.50, 0.75, 1.00]:
    def make_trades(t=target):
        trades=[]
        for date in all_dates:
            for sym in date_bars[date]:
                db=date_bars[date][sym]
                if len(db)<=SB+20: continue
                trend=daily_trend.get((date,sym))
                if trend not in ('DOWN','UP'): continue
                fb_high=db[0]['high']; fb_low=db[0]['low']
                # Check if first bar high/low holds for next 9 bars
                if trend=='DOWN':
                    held=all(db[j]['high']<=fb_high*1.001 for j in range(1,SB))
                    if not held: continue
                    e=db[SB]['close'];tp=e*(1-t/100);sp=e*(1+0.75/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(SB+1,min(len(db),70)):
                        if db[k]['low']<=tp: ep=tp;break
                        if db[k]['high']>=sp: ep=sp;break
                    trades.append((e-ep)/e*100/100*POS-CHARGES)
                else:
                    held=all(db[j]['low']>=fb_low*0.999 for j in range(1,SB))
                    if not held: continue
                    e=db[SB]['close'];tp=e*(1+t/100);sp=e*(1-0.75/100)
                    ep=db[min(69,len(db)-1)]['close']
                    for k in range(SB+1,min(len(db),70)):
                        if db[k]['high']>=tp: ep=tp;break
                        if db[k]['low']<=sp: ep=sp;break
                    trades.append((ep-e)/e*100/100*POS-CHARGES)
        return trades
    test(f'First bar hold T={target}', make_trades)

print(f'\n{"="*80}')
print('DONE')
