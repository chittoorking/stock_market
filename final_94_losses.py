"""Analyze the final 94 losses in the 88.7% WR setup."""
import sys,io,csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime as dt

data_dir=Path('data/5min'); all_data={}; date_bars=defaultdict(dict)
for f in sorted(data_dir.glob('*.csv')):
    sym=f.stem.split('_')[0].upper()
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
            'volume':sum(b['volume'] for b in bs),'range':max(b['high'] for b in bs)-min(b['low'] for b in bs)}
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
POS=1000000;CHARGES=386;SB=10;T=1.75;S=1.50

losses=[];wins=[]
for date in all_dates:
    for sym in date_bars[date]:
        if sym in ('NIFTY_50','NIFTY_BANK'): continue
        db=date_bars[date][sym]
        if len(db)<=SB+20: continue
        if daily_trend.get((date,sym))!='DOWN': continue
        pd=prev_day.get((date,sym))
        if not pd: continue
        rng=pd['high']-pd['low']
        if rng<=0: continue
        r3=pd['close']+rng*1.1/4
        triggered=False
        for j in range(1,SB+1):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                triggered=True; break
        if not triggered: continue
        sd2=sorted(daily_ohlc[sym].keys())
        di=sd2.index(date) if date in sd2 else -1
        if di<7: continue
        cd=0
        for back in range(1,20):
            if di-back<1: break
            if daily_ohlc[sym][sd2[di-back]]['close']<daily_ohlc[sym][sd2[di-back-1]]['close']:
                cd+=1
            else: break
        if cd>2: continue
        prev_d=sd2[di-1]; pc=daily_ohlc[sym][prev_d]
        yd_range_pct=pc['range']/pc['close']*100 if pc['close']>0 else 0
        yd_body_ratio=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
        if yd_range_pct<2.0 or yd_body_ratio<0.2: continue

        entry=db[SB]['close']
        tp=entry*(1-T/100);sp=entry*(1+S/100)
        lp=entry*(1-0.075/100)
        ep=db[min(69,len(db)-1)]['close'];exit_r='eod';mfe=0;mae=0;ta=False
        for k in range(SB+1,min(len(db),70)):
            fav=(entry-db[k]['low'])/entry*100;adv=(db[k]['high']-entry)/entry*100
            mfe=max(mfe,fav);mae=max(mae,adv)
            if mfe>=1.00: ta=True
            if db[k]['low']<=tp: ep=tp;exit_r='target';break
            if ta and db[k]['high']>=lp: ep=lp;exit_r='trail';break
            if not ta and db[k]['high']>=sp: ep=sp;exit_r='stop';break
        pnl=(entry-ep)/entry*100/100*POS-CHARGES
        dow=dt.strptime(date,'%Y-%m-%d').weekday()
        day_names=['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
        t={'pnl':pnl,'win':pnl>0,'date':date,'sym':sym,'exit':exit_r,
           'mfe':round(mfe,3),'mae':round(mae,3),'pnl_pct':round((entry-ep)/entry*100,3),
           'day':day_names[dow],'entry':round(entry,2),'ep':round(ep,2)}
        if pnl>0: wins.append(t)
        else: losses.append(t)

total=len(wins)+len(losses)

# Categorize
stops=[t for t in losses if t['exit']=='stop']
almost=[t for t in losses if t['exit']=='eod' and t['mfe']>=T*0.5]
flat=[t for t in losses if t['exit']=='eod' and t['mfe']<0.3 and t['mae']<0.5]
wrong=[t for t in losses if t['exit']=='eod' and t['mae']>0.5 and t['mfe']<0.3]
choppy=[t for t in losses if t['exit']=='eod' and t not in almost and t not in flat and t not in wrong]

print(f'88.7% WR Setup: {total} trades, {len(wins)} wins, {len(losses)} losses')
print(f'Wins earn:  Rs {sum(t["pnl"] for t in wins):+,.0f}')
print(f'Losses cost: Rs {sum(t["pnl"] for t in losses):+,.0f}')
print()
print('='*100)
print(f'ALL {len(losses)} LOSSES — Every single one')
print('='*100)

for name,cat in [('STOP HIT',stops),('WRONG DIRECTION',wrong),('CHOPPY',choppy),('ALMOST WON',almost),('FLAT',flat)]:
    if not cat: continue
    pct=len(cat)/total*100
    cat_loss=sum(t['pnl'] for t in cat)
    print(f'\n  {name}: {len(cat)} trades ({pct:.1f}% of all) | Rs {cat_loss:,.0f} total | Rs {cat_loss/len(cat):,.0f} avg')
    print(f'  {"#":>3} {"Date":>10} {"Day":>3} {"Stock":>12} {"Entry":>8} {"Exit":>8} {"PnL%":>7} {"Rs":>9} {"MFE":>5} {"MAE":>5}')
    print(f'  {"-"*80}')
    for i,t in enumerate(sorted(cat, key=lambda x:x['pnl']),1):
        print(f'  {i:>3} {t["date"]:>10} {t["day"]:>3} {t["sym"]:>12} {t["entry"]:>8.1f} {t["ep"]:>8.1f} {t["pnl_pct"]:>+6.3f}% Rs{t["pnl"]:>+8,.0f} {t["mfe"]:>5.2f} {t["mae"]:>5.2f}')

# Stock frequency
print(f'\n{"="*100}')
print(f'WHICH STOCKS APPEAR IN LOSSES?')
print(f'{"="*100}')
loss_stocks=Counter(t['sym'] for t in losses)
total_stocks=Counter(t['sym'] for t in wins+losses)
print(f'\n  {"Stock":>12} {"Losses":>7} {"Total":>6} {"Loss%":>6}')
for sym,cnt in loss_stocks.most_common():
    tot=total_stocks[sym]
    print(f'  {sym:>12} {cnt:>7} {tot:>6} {cnt/tot*100:>5.0f}%')

# Day frequency
print(f'\n{"="*100}')
print(f'WHICH DAYS?')
print(f'{"="*100}')
for di,day in enumerate(['Mon','Tue','Wed','Thu','Fri']):
    dw=sum(1 for t in wins if t['day']==day)
    dl=sum(1 for t in losses if t['day']==day)
    dn=dw+dl
    if dn==0: continue
    print(f'  {day}: {dn:>4} trades, {dl:>2} losses, WR={dw/dn*100:.0f}%')

# Year
print(f'\n{"="*100}')
print(f'WHICH YEARS?')
print(f'{"="*100}')
for y in ['2022','2023','2024','2025','2026']:
    yw=sum(1 for t in wins if t['date'][:4]==y)
    yl=sum(1 for t in losses if t['date'][:4]==y)
    if yw+yl==0: continue
    print(f'  {y}: {yw+yl:>4} trades, {yl:>2} losses, WR={yw/(yw+yl)*100:.0f}%')
