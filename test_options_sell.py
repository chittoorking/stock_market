"""Test: SELL options using our 4 strategy signals."""
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
SB=10;OPT_CHARGES=100

LOTS={'ADANIENT':250,'ADANIPORTS':500,'APOLLOHOSP':100,'ASIANPAINT':200,
    'AXISBANK':400,'BAJAJ-AUTO':100,'BPCL':1000,'BHARTIARTL':450,
    'BRITANNIA':100,'CIPLA':400,'COALINDIA':1050,'DIVISLAB':100,
    'EICHERMOT':125,'GRASIM':275,'HCLTECH':350,'HDFCBANK':400,
    'HDFCLIFE':700,'HEROMOTOCO':100,'HINDALCO':850,'HINDUNILVR':200,
    'ICICIBANK':525,'ITC':1600,'INDUSINDBK':500,'INFY':400,
    'JSWSTEEL':675,'LT':150,'M&M':350,'MARUTI':50,
    'NTPC':1500,'NESTLEIND':25,'ONGC':1925,'POWERGRID':1800,
    'RELIANCE':250,'SBILIFE':375,'SBIN':750,'SUNPHARMA':350,
    'TCS':125,'TATACONSUM':500,'TATAMOTORS':550,'TATASTEEL':1700,
    'TECHM':350,'TITAN':175,'UPL':1300,'ULTRACEMCO':50,'WIPRO':1000}

def get_cam_signals(date):
    signals=[]
    for sym in date_bars.get(date,{}):
        db=date_bars[date][sym]
        if len(db)<=SB+20: continue
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
        r3=pd['close']+rng*1.1/4; s3=pd['close']-rng*1.1/4
        if trend=='DOWN':
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                    signals.append({'sym':sym,'dir':'SHORT','entry':db[SB]['close'],'date':date})
                    break
        else:
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['low']-s3)<atr*0.3 and db[j]['close']>s3:
                    signals.append({'sym':sym,'dir':'LONG','entry':db[SB]['close'],'date':date})
                    break
    return signals

# Test option selling at different strike distances
print('OPTION SELLING — Using CAM signals')
print('='*70)
print()
print('When CAM says SHORT: SELL a CALL at entry + X%')
print('When CAM says LONG:  SELL a PUT at entry - X%')
print('Collect premium. Win if stock stays below/above strike.')
print()

for strike_dist in [0.25, 0.50, 0.75, 1.00, 1.50]:
    for premium_pct in [0.20, 0.30, 0.40, 0.50]:
        equity_trades=[]
        option_trades=[]
        for date in all_dates:
            sigs=get_cam_signals(date)
            for s in sigs:
                sym=s['sym'];entry=s['entry'];direction=s['dir']
                db=date_bars[date][sym]
                lot=LOTS.get(sym,500)

                # EQUITY trade (existing)
                if direction=='SHORT':
                    tp=entry*(1-1.75/100);sp=entry*(1+1.50/100)
                    ep=db[-1]['close']
                    for k in range(SB+1,min(len(db),70)):
                        if db[k]['low']<=tp: ep=tp;break
                        if db[k]['high']>=sp: ep=sp;break
                    eq_pnl=(entry-ep)/entry*100/100*1000000-386
                else:
                    tp=entry*(1+1.75/100);sp=entry*(1-1.50/100)
                    ep=db[-1]['close']
                    for k in range(SB+1,min(len(db),70)):
                        if db[k]['high']>=tp: ep=tp;break
                        if db[k]['low']<=sp: ep=sp;break
                    eq_pnl=(ep-entry)/entry*100/100*1000000-386
                equity_trades.append({'pnl':eq_pnl,'win':eq_pnl>0,'date':date})

                # OPTION SELL trade
                premium_per_unit=entry*premium_pct/100
                premium_total=premium_per_unit*lot

                if direction=='SHORT':
                    strike=entry*(1+strike_dist/100)
                    # Worst case: max price stock reached after entry
                    max_p=max(db[k]['high'] for k in range(SB+1,min(len(db),70)))
                    if max_p>strike:
                        intrinsic=(max_p-strike)*lot
                        opt_pnl=premium_total-intrinsic-OPT_CHARGES
                    else:
                        opt_pnl=premium_total-OPT_CHARGES
                else:
                    strike=entry*(1-strike_dist/100)
                    min_p=min(db[k]['low'] for k in range(SB+1,min(len(db),70)))
                    if min_p<strike:
                        intrinsic=(strike-min_p)*lot
                        opt_pnl=premium_total-intrinsic-OPT_CHARGES
                    else:
                        opt_pnl=premium_total-OPT_CHARGES
                option_trades.append({'pnl':opt_pnl,'win':opt_pnl>0,'date':date})

        en=len(equity_trades);ew=sum(1 for t in equity_trades if t['win'])
        on=len(option_trades);ow=sum(1 for t in option_trades if t['win'])
        et=sum(t['pnl'] for t in equity_trades)
        ot=sum(t['pnl'] for t in option_trades)

        if ow/on*100>=85:
            # Walk forward
            te=[t for t in option_trades if t['date']>='2025-01-01']
            te_wr=sum(1 for t in te if t['win'])/len(te)*100 if te else 0
            marker=' <<<' if ow/on*100>=90 else ''
            print(f'  Strike+{strike_dist}% Prem={premium_pct}%: OPT {on} tr WR={ow/on*100:.0f}% Rs{ot/on:>+6,.0f}/tr Te={te_wr:.0f}% | EQ WR={ew/en*100:.0f}% Rs{et/en:>+6,.0f}/tr{marker}')

# Simple summary
print()
print('EQUITY vs OPTION SELLING (best setups):')
print('='*70)
for sd, pp in [(0.50, 0.30), (0.75, 0.30), (1.00, 0.30), (0.50, 0.40), (0.75, 0.40)]:
    ot2=[]
    for date in all_dates:
        sigs=get_cam_signals(date)
        for s in sigs:
            sym=s['sym'];entry=s['entry'];d=s['dir']
            db=date_bars[date][sym]; lot=LOTS.get(sym,500)
            prem=entry*pp/100*lot
            if d=='SHORT':
                strike=entry*(1+sd/100)
                mx=max(db[k]['high'] for k in range(SB+1,min(len(db),70)))
                if mx>strike: pnl=prem-(mx-strike)*lot-OPT_CHARGES
                else: pnl=prem-OPT_CHARGES
            else:
                strike=entry*(1-sd/100)
                mn=min(db[k]['low'] for k in range(SB+1,min(len(db),70)))
                if mn<strike: pnl=prem-(strike-mn)*lot-OPT_CHARGES
                else: pnl=prem-OPT_CHARGES
            ot2.append({'pnl':pnl,'win':pnl>0,'date':date})
    n=len(ot2);w=sum(1 for t in ot2 if t['win'])
    total=sum(t['pnl'] for t in ot2)
    te=[t for t in ot2 if t['date']>='2025-01-01']
    te_wr=sum(1 for t in te if t['win'])/len(te)*100 if te else 0
    print(f'  Strike+{sd}% Prem={pp}%: {n} tr, WR={w/n*100:.0f}%, Rs{total/n:>+6,.0f}/tr, Total Rs{total:>+10,.0f}, Te={te_wr:.0f}%')
