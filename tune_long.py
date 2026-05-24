"""Tune LONG with same approach as SHORT: CD filter, yesterday filter, trail."""
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
POS=1000000;CHARGES=386;SB=10;T=1.75;S=1.50

def run_long(max_cd=99, min_yd_range=0, min_yd_body=0, trail_act=999, trail_lock=0.075):
    trades=[]
    for date in all_dates:
        for sym in date_bars[date]:
            db=date_bars[date][sym]
            if len(db)<=SB+20: continue
            if daily_trend.get((date,sym))!='UP': continue
            pd=prev_day.get((date,sym))
            if not pd: continue
            rng=pd['high']-pd['low']
            if rng<=0: continue

            # CD filter (consecutive UP days)
            if max_cd<99:
                sd2=sorted(daily_ohlc[sym].keys())
                di=sd2.index(date) if date in sd2 else -1
                if di<7: continue
                cd=0
                for back in range(1,20):
                    if di-back<1: break
                    if daily_ohlc[sym][sd2[di-back]]['close']>daily_ohlc[sym][sd2[di-back-1]]['close']:
                        cd+=1
                    else: break
                if cd>max_cd: continue

                # Yesterday filter
                if min_yd_range>0 or min_yd_body>0:
                    prev_d=sd2[di-1]; pc=daily_ohlc[sym][prev_d]
                    yr=pc['range']/pc['close']*100 if pc['close']>0 else 0
                    yb=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
                    if yr<min_yd_range or yb<min_yd_body: continue

            s3=pd['close']-rng*1.1/4
            triggered=False
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['low']-s3)<atr*0.3 and db[j]['close']>s3:
                    triggered=True; break
            if not triggered: continue

            entry=db[SB]['close']
            tp=entry*(1+T/100);sp=entry*(1-S/100)
            lp=entry*(1+trail_lock/100)
            ep=db[min(69,len(db)-1)]['close'];mfe=0;ta=False
            for k in range(SB+1,min(len(db),70)):
                fav=(db[k]['high']-entry)/entry*100;mfe=max(mfe,fav)
                if mfe>=trail_act: ta=True
                if db[k]['high']>=tp: ep=tp;break
                if ta and db[k]['low']<=lp: ep=lp;break
                if not ta and db[k]['low']<=sp: ep=sp;break
            pnl=(ep-entry)/entry*100/100*POS-CHARGES
            trades.append({'pnl':pnl,'win':pnl>0,'date':date})
    return trades

def report(label, trades):
    if not trades or len(trades)<10:
        print(f'  {label:>55}: {len(trades) if trades else 0} trades (too few)')
        return
    n=len(trades);w=sum(1 for t in trades if t['win']);total=sum(t['pnl'] for t in trades)
    train=[t for t in trades if t['date']<'2025-01-01']
    test=[t for t in trades if t['date']>='2025-01-01']
    tr_wr=sum(1 for t in train if t['win'])/len(train)*100 if train else 0
    te_wr=sum(1 for t in test if t['win'])/len(test)*100 if test else 0
    te_per=sum(t['pnl'] for t in test)/len(test) if test else 0
    print(f'  {label:>55}: {n:>4} WR={w/n*100:.1f}% Rs{total/n:>+7,.0f}/tr | TestWR={te_wr:.1f}% Rs{te_per:>+7,.0f}')

# ═══ CONSECUTIVE UP DAYS ═══
print('='*90)
print('LONG: CONSECUTIVE UP DAYS — When does WR drop?')
print('='*90)

# First show per-CD bucket
trades_all=run_long()
# Rerun with tracking CD
for date in all_dates:
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=SB+20: continue
        if daily_trend.get((date,sym))!='UP': continue

all_by_cd=defaultdict(list)
for date in all_dates:
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=SB+20: continue
        if daily_trend.get((date,sym))!='UP': continue
        pd=prev_day.get((date,sym))
        if not pd: continue
        rng=pd['high']-pd['low']
        if rng<=0: continue
        sd2=sorted(daily_ohlc[sym].keys())
        di=sd2.index(date) if date in sd2 else -1
        if di<7: continue
        cd=0
        for back in range(1,20):
            if di-back<1: break
            if daily_ohlc[sym][sd2[di-back]]['close']>daily_ohlc[sym][sd2[di-back-1]]['close']:
                cd+=1
            else: break

        s3=pd['close']-rng*1.1/4
        triggered=False
        for j in range(1,SB+1):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            if abs(db[j]['low']-s3)<atr*0.3 and db[j]['close']>s3:
                triggered=True; break
        if not triggered: continue
        entry=db[SB]['close']
        tp=entry*(1+T/100);sp=entry*(1-S/100)
        ep=db[min(69,len(db)-1)]['close']
        for k in range(SB+1,min(len(db),70)):
            if db[k]['high']>=tp: ep=tp;break
            if db[k]['low']<=sp: ep=sp;break
        pnl=(ep-entry)/entry*100/100*POS-CHARGES
        all_by_cd[cd].append({'pnl':pnl,'win':pnl>0})

print(f'\n  ConsecUp days:')
for cd in range(0,8):
    tl=all_by_cd.get(cd,[])
    if len(tl)<10: continue
    w=sum(1 for t in tl if t['win']);n=len(tl)
    per=sum(t['pnl'] for t in tl)/n
    label='GREAT' if w/n*100>=85 else 'GOOD' if w/n*100>=75 else 'OK' if w/n*100>=65 else 'TRAP'
    print(f'    CD {cd}: {n:>4} trades, WR={w/n*100:.0f}%, Rs {per:+,.0f}/trade  [{label}]')

# ═══ CD FILTER ═══
print(f'\n{"="*90}')
print('LONG: CD FILTER — Which cutoff?')
print('='*90)
for max_cd in [99, 1, 2, 3, 4, 5]:
    report(f'ConsecUp <= {max_cd}' if max_cd<99 else 'No CD filter', run_long(max_cd=max_cd))

# ═══ YESTERDAY FILTER ═══
print(f'\n{"="*90}')
print('LONG: YESTERDAY RANGE + BODY FILTER')
print('='*90)
report('No filter', run_long())
for yr in [1.0, 1.5, 2.0, 2.5]:
    report(f'Yd range > {yr}%', run_long(min_yd_range=yr, max_cd=99))
for yr in [1.5, 2.0]:
    for yb in [0.15, 0.2, 0.25, 0.3]:
        report(f'Yd range>{yr}% + body>{yb}', run_long(min_yd_range=yr, min_yd_body=yb, max_cd=99))

# ═══ TRAIL ═══
print(f'\n{"="*90}')
print('LONG: TRAIL — Lock profit at what level?')
print('='*90)
report('No trail', run_long())
for ta in [0.75, 1.00, 1.25]:
    report(f'Trail at {ta}% lock 0.075%', run_long(trail_act=ta, trail_lock=0.075))

# ═══ BEST COMBOS ═══
print(f'\n{"="*90}')
print('LONG: BEST COMBINATIONS')
print('='*90)

combos=[
    ('RAW (no filters)', 99, 0, 0, 999),
    ('CD 0-2', 2, 0, 0, 999),
    ('CD 0-3', 3, 0, 0, 999),
    ('Yd range>2% + body>0.2', 99, 2.0, 0.2, 999),
    ('Yd range>1.5% + body>0.2', 99, 1.5, 0.2, 999),
    ('CD 0-2 + trail 1.0', 2, 0, 0, 1.0),
    ('CD 0-3 + trail 1.0', 3, 0, 0, 1.0),
    ('CD 0-2 + Yd>1.5%+body>0.2', 2, 1.5, 0.2, 999),
    ('CD 0-3 + Yd>1.5%+body>0.2', 3, 1.5, 0.2, 999),
    ('CD 0-3 + Yd>2%+body>0.2', 3, 2.0, 0.2, 999),
    ('CD 0-2 + Yd>1.5%+body>0.2 + trail', 2, 1.5, 0.2, 1.0),
    ('CD 0-3 + Yd>1.5%+body>0.2 + trail', 3, 1.5, 0.2, 1.0),
    ('CD 0-3 + Yd>2%+body>0.2 + trail', 3, 2.0, 0.2, 1.0),
]
for label, mcd, yr, yb, ta in combos:
    report(label, run_long(max_cd=mcd, min_yd_range=yr, min_yd_body=yb, trail_act=ta))
