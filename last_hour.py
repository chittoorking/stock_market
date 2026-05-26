"""Last Hour Reversal — deep analysis."""
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
POS=1000000;CHARGES=386

def avg(lst): return sum(lst)/len(lst) if lst else 0

# ═══ FULL ANALYSIS ═══
# Stock in UP trend, dropped 1%+ by 2 PM, buy for bounce
# Also test: DOWN trend, rallied 1%+ by 2 PM, short for drop

print('='*80)
print('LAST HOUR REVERSAL — Full analysis')
print('='*80)

# Sweep all params
print('\nBUY dip in UP trend:')
for entry_bar in [48, 50, 52, 54, 56]:  # 1:55, 2:05, 2:15, 2:25, 2:35 PM
    time_str = str(9+(entry_bar*5)//60) + ':' + str(15+(entry_bar*5)%60).zfill(2)
    for min_drop in [0.75, 1.0, 1.25, 1.5]:
        for target in [0.15, 0.20, 0.25, 0.30, 0.50]:
            for stop in [0.50, 0.75]:
                trades=[]
                for date in all_dates:
                    for sym in date_bars[date]:
                        db=date_bars[date][sym]
                        if len(db)<=entry_bar+5: continue
                        trend=daily_trend.get((date,sym))
                        if trend!='UP': continue
                        drop=(db[entry_bar]['close']-db[0]['open'])/db[0]['open']*100
                        if drop>-min_drop: continue
                        entry=db[entry_bar]['close']
                        tp=entry*(1+target/100);sp=entry*(1-stop/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(entry_bar+1,min(len(db),70)):
                            if db[k]['high']>=tp: ep=tp;break
                            if db[k]['low']<=sp: ep=sp;break
                        trades.append({'pnl':(ep-entry)/entry*100/100*POS-CHARGES,'date':date})
                if len(trades)>=30:
                    w=sum(1 for t in trades if t['pnl']>0);n=len(trades)
                    if w/n*100>=85:
                        train=[t for t in trades if t['date']<'2025-01-01']
                        test=[t for t in trades if t['date']>='2025-01-01']
                        tr_wr=sum(1 for t in train if t['pnl']>0)/len(train)*100 if train else 0
                        te_wr=sum(1 for t in test if t['pnl']>0)/len(test)*100 if test else 0
                        marker=' <<<' if w/n*100>=90 else ''
                        print(f'  Bar{entry_bar}({time_str}) Drop>{min_drop}% T={target} S={stop}: {n:>3} tr, WR={w/n*100:.0f}%, Rs{sum(t["pnl"] for t in trades)/n:>+5,.0f} Te={te_wr:.0f}%{marker}')

print('\nSHORT rally in DOWN trend:')
for entry_bar in [48, 50, 52, 54, 56]:
    time_str = str(9+(entry_bar*5)//60) + ':' + str(15+(entry_bar*5)%60).zfill(2)
    for min_rally in [0.75, 1.0, 1.25, 1.5]:
        for target in [0.15, 0.20, 0.25, 0.30, 0.50]:
            for stop in [0.50, 0.75]:
                trades=[]
                for date in all_dates:
                    for sym in date_bars[date]:
                        db=date_bars[date][sym]
                        if len(db)<=entry_bar+5: continue
                        trend=daily_trend.get((date,sym))
                        if trend!='DOWN': continue
                        rally=(db[entry_bar]['close']-db[0]['open'])/db[0]['open']*100
                        if rally<min_rally: continue
                        entry=db[entry_bar]['close']
                        tp=entry*(1-target/100);sp=entry*(1+stop/100)
                        ep=db[min(69,len(db)-1)]['close']
                        for k in range(entry_bar+1,min(len(db),70)):
                            if db[k]['low']<=tp: ep=tp;break
                            if db[k]['high']>=sp: ep=sp;break
                        trades.append({'pnl':(entry-ep)/entry*100/100*POS-CHARGES,'date':date})
                if len(trades)>=30:
                    w=sum(1 for t in trades if t['pnl']>0);n=len(trades)
                    if w/n*100>=85:
                        train=[t for t in trades if t['date']<'2025-01-01']
                        test=[t for t in trades if t['date']>='2025-01-01']
                        tr_wr=sum(1 for t in train if t['pnl']>0)/len(train)*100 if train else 0
                        te_wr=sum(1 for t in test if t['pnl']>0)/len(test)*100 if test else 0
                        marker=' <<<' if w/n*100>=90 else ''
                        print(f'  Bar{entry_bar}({time_str}) Rally>{min_rally}% T={target} S={stop}: {n:>3} tr, WR={w/n*100:.0f}%, Rs{sum(t["pnl"] for t in trades)/n:>+5,.0f} Te={te_wr:.0f}%{marker}')

# ═══ BEST SETUP — detailed yearly ═══
print(f'\n{"="*80}')
print('BEST SETUP — Detailed analysis')
print('='*80)

best_trades=[]
for date in all_dates:
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=58: continue
        trend=daily_trend.get((date,sym))
        # BUY dip in UP trend at bar 54 (2:25 PM)
        if trend=='UP':
            drop=(db[54]['close']-db[0]['open'])/db[0]['open']*100
            if drop<=-1.0:
                entry=db[54]['close']
                tp=entry*(1+0.25/100);sp=entry*(1-0.50/100)
                ep=db[min(69,len(db)-1)]['close']
                exit_r='eod'
                for k in range(55,min(len(db),70)):
                    if db[k]['high']>=tp: ep=tp;exit_r='target';break
                    if db[k]['low']<=sp: ep=sp;exit_r='stop';break
                pnl=(ep-entry)/entry*100/100*POS-CHARGES
                best_trades.append({'pnl':pnl,'win':pnl>0,'date':date,'sym':sym,'exit':exit_r,
                    'drop':round(drop,2),'pnl_pct':round((ep-entry)/entry*100,3)})
        # SHORT rally in DOWN trend at bar 54
        if trend=='DOWN':
            rally=(db[54]['close']-db[0]['open'])/db[0]['open']*100
            if rally>=1.0:
                entry=db[54]['close']
                tp=entry*(1-0.25/100);sp=entry*(1+0.50/100)
                ep=db[min(69,len(db)-1)]['close']
                exit_r='eod'
                for k in range(55,min(len(db),70)):
                    if db[k]['low']<=tp: ep=tp;exit_r='target';break
                    if db[k]['high']>=sp: ep=sp;exit_r='stop';break
                pnl=(entry-ep)/entry*100/100*POS-CHARGES
                best_trades.append({'pnl':pnl,'win':pnl>0,'date':date,'sym':sym,'exit':exit_r,
                    'drop':round(rally,2),'pnl_pct':round((entry-ep)/entry*100,3)})

if best_trades:
    n=len(best_trades);w=sum(1 for t in best_trades if t['win'])
    total=sum(t['pnl'] for t in best_trades)
    train=[t for t in best_trades if t['date']<'2025-01-01']
    test=[t for t in best_trades if t['date']>='2025-01-01']
    tr_wr=sum(1 for t in train if t['win'])/len(train)*100 if train else 0
    te_wr=sum(1 for t in test if t['win'])/len(test)*100 if test else 0
    print(f'\n  Trades: {n} | Wins: {w} ({w/n*100:.0f}%) | Per trade: Rs {total/n:+,.0f}')
    print(f'  Train: {len(train)} WR={tr_wr:.0f}% | Test: {len(test)} WR={te_wr:.0f}%')

    # Yearly
    yearly=defaultdict(lambda:{'n':0,'w':0,'pnl':0})
    for t in best_trades:
        y=t['date'][:4]; yearly[y]['n']+=1; yearly[y]['pnl']+=t['pnl']
        if t['win']: yearly[y]['w']+=1
    print(f'\n  Yearly:')
    for y in sorted(yearly):
        m=yearly[y]
        print(f'    {y}: {m["n"]} trades, WR={m["w"]/m["n"]*100:.0f}%, Rs {m["pnl"]:+,.0f}')

    # Losses
    losses=[t for t in best_trades if not t['win']]
    print(f'\n  Losses ({len(losses)}):')
    for t in losses:
        print(f'    {t["date"]} {t["sym"]:>12} drop={t["drop"]}% pnl={t["pnl_pct"]:+.3f}% {t["exit"]}')
