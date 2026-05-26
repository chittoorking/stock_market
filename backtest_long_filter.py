"""Backtest: LONG with vs without yesterday filter."""
import sys,io,csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

data_dir=Path('data/5min'); all_data={}; date_bars=defaultdict(dict)
for f in sorted(data_dir.glob('*.csv')):
    sym=f.stem.split('_')[0].upper()
    if sym in ('NIFTY_50','NIFTY_BANK'): continue
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
            'range':max(b['high'] for b in bs)-min(b['low'] for b in bs)}
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
POS=1000000;CHARGES=386;SB=10;T=1.75;S=1.50;TA=1.00;TL=0.075;RS=0.25

def run(long_yd_filter):
    trades=[]
    yearly=defaultdict(lambda:{'n':0,'w':0,'pnl':0})
    daily_pnl=defaultdict(float)
    shorts=0;longs=0;sw=0;lw=0
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
            sd2=sorted(daily_ohlc[sym].keys())
            di=sd2.index(date) if date in sd2 else -1
            if di<7: continue

            if trend=='DOWN':
                cd=0
                for back in range(1,20):
                    if di-back<1: break
                    if daily_ohlc[sym][sd2[di-back]]['close']<daily_ohlc[sym][sd2[di-back-1]]['close']:
                        cd+=1
                    else: break
                if cd>2: continue
                prev_d=sd2[di-1]; pc=daily_ohlc[sym][prev_d]
                yr=pc['range']/pc['close']*100 if pc['close']>0 else 0
                yb=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
                if yr<2.0 or yb<0.2: continue
                level=pd['close']+rng*1.1/4
                triggered=False
                for j in range(1,SB+1):
                    atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                    if abs(db[j]['high']-level)<atr*0.3 and db[j]['close']<level:
                        triggered=True; break
                if not triggered: continue
                direction='SHORT'
            else:
                if long_yd_filter:
                    prev_d=sd2[di-1]; pc=daily_ohlc[sym][prev_d]
                    yr=pc['range']/pc['close']*100 if pc['close']>0 else 0
                    yb=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
                    if yr<2.0 or yb<0.2: continue
                level=pd['close']-rng*1.1/4
                triggered=False
                for j in range(1,SB+1):
                    atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                    if abs(db[j]['low']-level)<atr*0.3 and db[j]['close']>level:
                        triggered=True; break
                if not triggered: continue
                direction='LONG'

            entry=db[SB]['close']
            if direction=='SHORT':
                tp=entry*(1-T/100);sp=entry*(1+S/100);lp=entry*(1-TL/100)
            else:
                tp=entry*(1+T/100);sp=entry*(1-S/100);lp=entry*(1+TL/100)
            ep=db[min(69,len(db)-1)]['close'];mfe=0;ta=False;th=False;cs=sp
            for k in range(SB+1,min(len(db),70)):
                if direction=='SHORT':
                    fav=(entry-db[k]['low'])/entry*100;mfe=max(mfe,fav)
                    if mfe>=TA: ta=True
                    if not th and mfe>=T: th=True; cs=tp
                    if th:
                        rsp=max(T,mfe-RS); ns=entry*(1-rsp/100)
                        if ns<cs: cs=ns
                        if db[k]['high']>=cs: ep=cs;break
                    elif ta:
                        if db[k]['high']>=lp: ep=lp;break
                    else:
                        if db[k]['high']>=sp: ep=sp;break
                else:
                    fav=(db[k]['high']-entry)/entry*100;mfe=max(mfe,fav)
                    if mfe>=TA: ta=True
                    if not th and mfe>=T: th=True; cs=tp
                    if th:
                        rsp=max(T,mfe-RS); ns=entry*(1+rsp/100)
                        if ns>cs: cs=ns
                        if db[k]['low']<=cs: ep=cs;break
                    elif ta:
                        if db[k]['low']<=lp: ep=lp;break
                    else:
                        if db[k]['low']<=sp: ep=sp;break

            if direction=='SHORT': pnl_pct=(entry-ep)/entry*100
            else: pnl_pct=(ep-entry)/entry*100
            pnl=pnl_pct/100*POS-CHARGES
            win=pnl>0
            trades.append({'pnl':pnl,'win':win,'date':date,'dir':direction})
            daily_pnl[date]+=pnl
            y=date[:4]
            yearly[y]['n']+=1;yearly[y]['pnl']+=pnl
            if win: yearly[y]['w']+=1
            if direction=='SHORT': shorts+=1; sw+=(1 if win else 0)
            else: longs+=1; lw+=(1 if win else 0)
    return trades, yearly, daily_pnl, shorts, sw, longs, lw

for label, use_filter in [('CURRENT (LONG no yd filter)', False), ('NEW (LONG with yd filter)', True)]:
    trades, yearly, daily_pnl, sn, sw, ln, lw = run(use_filter)
    n=len(trades);w=sum(1 for t in trades if t['win']);total=sum(t['pnl'] for t in trades)
    daily_vals=[v for v in daily_pnl.values() if v!=0]
    cum=0;peak=0;dd=0
    for d in sorted(daily_pnl.keys()): cum+=daily_pnl[d];peak=max(peak,cum);dd=max(dd,peak-cum)
    train=[t for t in trades if t['date']<'2025-01-01']
    test=[t for t in trades if t['date']>='2025-01-01']
    tr_w=sum(1 for t in train if t['win'])
    te_w=sum(1 for t in test if t['win'])
    tr_per=sum(t['pnl'] for t in train)/len(train) if train else 0
    te_per=sum(t['pnl'] for t in test)/len(test) if test else 0

    swr = sw/sn*100 if sn else 0
    lwr = lw/ln*100 if ln else 0
    print(f'\n  {label}:')
    print(f'    Trades: {n} (SHORT={sn} WR={swr:.0f}% | LONG={ln} WR={lwr:.0f}%)')
    print(f'    WR: {w/n*100:.1f}%')
    print(f'    Per trade: Rs {total/n:+,.0f}')
    print(f'    Total 4yr: Rs {total:+,.0f}')
    print(f'    Per year: Rs {total/4:+,.0f}')
    print(f'    Max DD: Rs {dd:,.0f}')
    print(f'    Green days: {sum(1 for v in daily_vals if v>0)}/{len(daily_vals)} ({sum(1 for v in daily_vals if v>0)/len(daily_vals)*100:.0f}%)')
    print(f'    Walk-fwd: Train {tr_w/len(train)*100:.1f}% Rs{tr_per:+,.0f} | Test {te_w/len(test)*100:.1f}% Rs{te_per:+,.0f}')
    for y in sorted(yearly):
        m=yearly[y]
        wr=m['w']/m['n']*100
        print(f'      {y}: {m["n"]} trades, WR={wr:.0f}%, Rs {m["pnl"]:+,.0f}')
