"""Test: BUY options on our signals, exit FAST before theta eats it.
Key: GAP fills in 10-30 min. If we buy option at 9:20 and exit by 9:50,
theta barely matters. The leverage amplifies our 0.5% move.
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
OPT_CHARGES=100

# Simulate option BUYING on GAP signals
# GAP: stock gaps 2%+, first bar reverses 0.5%+
# Buy ATM option, exit when stock moves 0.5% (target) or 1% against (stop)
# Option amplification: ~3-5x for ATM intraday (conservative, not 12x fake)
# Because we exit in 10-30 min, theta loss is minimal

print('OPTION BUYING ON GAP SIGNALS (fast exit)')
print('='*70)
print()

for amplification in [3, 4, 5]:
    for premium_rs in [3000, 5000, 8000]:
        for max_bars in [6, 10, 15]:  # Exit within 30/50/75 min
            trades=[]
            for date in all_dates:
                for sym in date_bars[date]:
                    db=date_bars[date][sym]
                    if len(db)<=15: continue
                    sd2=sorted(daily_ohlc.get(sym,{}).keys())
                    di=sd2.index(date) if date in sd2 else -1
                    if di<1: continue
                    prev_close=daily_ohlc[sym][sd2[di-1]]['close']
                    gap=(db[0]['open']-prev_close)/prev_close*100
                    if abs(gap)<2.0: continue
                    fb_ret=(db[0]['close']-db[0]['open'])/db[0]['open']*100
                    if gap>0 and fb_ret>=-0.5: continue
                    if gap<0 and fb_ret<=0.5: continue

                    entry_price=db[0]['open']
                    # Buy option at bar 1 (after first bar confirms reversal)
                    if len(db)<2: continue

                    stock_target=0.50  # 0.5% stock move
                    stock_stop=1.00    # 1% stock stop

                    # Track stock move bar by bar, exit fast
                    for k in range(1, min(max_bars+1, len(db))):
                        if gap>0:  # SHORT via PUT
                            stock_move=(entry_price-db[k]['low'])/entry_price*100
                            stock_adverse=(db[k]['high']-entry_price)/entry_price*100
                        else:  # LONG via CALL
                            stock_move=(db[k]['high']-entry_price)/entry_price*100
                            stock_adverse=(entry_price-db[k]['low'])/entry_price*100

                        if stock_move>=stock_target:
                            # Option gains: stock_move * amplification
                            opt_gain=stock_target*amplification/100*premium_rs
                            # Theta loss for K bars (minimal for 10-30 min)
                            theta_loss=premium_rs*0.005*k  # 0.5% per bar theta
                            pnl=opt_gain-theta_loss-OPT_CHARGES
                            trades.append(pnl);break
                        if stock_adverse>=stock_stop:
                            opt_loss=stock_stop*amplification/100*premium_rs
                            pnl=-opt_loss-OPT_CHARGES
                            trades.append(pnl);break
                    else:
                        # Didn't hit target or stop within max_bars
                        # Exit at last bar — option decayed
                        if gap>0:
                            final_move=(entry_price-db[min(max_bars,len(db)-1)]['close'])/entry_price*100
                        else:
                            final_move=(db[min(max_bars,len(db)-1)]['close']-entry_price)/entry_price*100
                        opt_pnl=final_move*amplification/100*premium_rs
                        theta_loss=premium_rs*0.005*max_bars
                        pnl=opt_pnl-theta_loss-OPT_CHARGES
                        trades.append(pnl)

            if len(trades)>=50:
                w=sum(1 for t in trades if t>0);n=len(trades)
                total=sum(trades)
                if w/n*100>=80:
                    train=trades[:int(n*0.75)];test=trades[int(n*0.75):]
                    te_wr=sum(1 for t in test if t>0)/len(test)*100
                    print(f'  Amp={amplification}x Prem=Rs{premium_rs} MaxBars={max_bars}: {n} tr, WR={w/n*100:.0f}%, Rs{total/n:>+5,.0f}/tr Te={te_wr:.0f}%')

# Also test on CAM signals with fast exit
print()
print('OPTION BUYING ON CAM SIGNALS (fast exit)')
print('='*70)
for amplification in [3, 4, 5]:
    for premium_rs in [5000, 8000]:
        for max_bars in [10, 20, 30]:
            trades=[]
            for date in all_dates:
                for sym in date_bars[date]:
                    db=date_bars[date][sym]
                    SB=10
                    if len(db)<=SB+max_bars: continue
                    trend=daily_trend.get((date,sym))
                    if trend not in ('DOWN','UP'): continue
                    pd=prev_day.get((date,sym))
                    if not pd: continue
                    rng=pd['high']-pd['low']
                    if rng<=0: continue
                    sd2=sorted(daily_ohlc[sym].keys());di=sd2.index(date) if date in sd2 else -1
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
                    r3=pd['close']+rng*1.1/4;s3=pd['close']-rng*1.1/4
                    signal=None
                    if trend=='DOWN':
                        for j in range(1,SB+1):
                            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                            if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                                signal='SHORT';break
                    else:
                        for j in range(1,SB+1):
                            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                            if abs(db[j]['low']-s3)<atr*0.3 and db[j]['close']>s3:
                                signal='LONG';break
                    if not signal: continue
                    entry=db[SB]['close']
                    for k in range(SB+1, min(SB+max_bars+1, len(db))):
                        if signal=='SHORT':
                            sm=(entry-db[k]['low'])/entry*100
                            sa=(db[k]['high']-entry)/entry*100
                        else:
                            sm=(db[k]['high']-entry)/entry*100
                            sa=(entry-db[k]['low'])/entry*100
                        if sm>=1.0:
                            pnl=1.0*amplification/100*premium_rs-premium_rs*0.005*(k-SB)-OPT_CHARGES
                            trades.append(pnl);break
                        if sa>=1.5:
                            pnl=-1.5*amplification/100*premium_rs-OPT_CHARGES
                            trades.append(pnl);break
                    else:
                        bars_held=min(max_bars, len(db)-SB-1)
                        if signal=='SHORT':
                            fm=(entry-db[min(SB+max_bars,len(db)-1)]['close'])/entry*100
                        else:
                            fm=(db[min(SB+max_bars,len(db)-1)]['close']-entry)/entry*100
                        pnl=fm*amplification/100*premium_rs-premium_rs*0.005*bars_held-OPT_CHARGES
                        trades.append(pnl)

            if len(trades)>=50:
                w=sum(1 for t in trades if t>0);n=len(trades)
                total=sum(trades)
                if w/n*100>=75:
                    print(f'  Amp={amplification}x Prem=Rs{premium_rs} MaxBars={max_bars}: {n} tr, WR={w/n*100:.0f}%, Rs{total/n:>+5,.0f}/tr')
