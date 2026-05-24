"""
HIGH PROBABILITY SCANNER: Find ALL trades with 90%+ confidence of making > breakeven.
Don't care about size of win. Just need > 0.01% after charges.
Many small wins compounded = exponential.

For every signal type, every stock, every day:
What CONDITIONS produce 90%+ WR with positive expectancy?
"""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

data_dir=Path('data/5min'); all_data={}; date_bars=defaultdict(dict)
print('Loading...', flush=True)
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

all_dates=sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))

prev_close={}; daily_ctx={}
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars)); dc=[]
    for i,d in enumerate(dfs):
        db=date_bars[d].get(sym,[])
        if not db: continue
        c=db[-1]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[dfs[i-1]].get(sym,[])
            if pdb: prev_close[(d,sym)]=pdb[-1]['close']
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_ctx[(d,sym)]={'trend':'UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'}

# Pre-compute prev day bars per (date, sym)
prev_day_bars = {}
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars))
    for i,d in enumerate(dfs):
        if i > 0:
            prev_day_bars[(d,sym)] = date_bars[dfs[i-1]].get(sym,[])
print('Done.\n', flush=True)

# Target: make > breakeven (0.084% for equity, 0.01% for futures/options)
# For each condition combo, count WR on "did it move at least +0.1% in our direction?"
# Not R:R based. Just: did price touch entry + 0.1% at ANY point before 3PM?

scan_bar = 10
BREAKEVEN = 0.10  # Need at least +0.10% move in our direction (covers charges + small profit)

print(f'SCANNING: Every signal that gives 90%+ chance of +{BREAKEVEN}% move')
print(f'4 years, 45 stocks, 1087 days')
print('='*80)

# For every stock every day, check ALL signal types
all_signals = []

for date in all_dates:
    up=dn=tot=0
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=scan_bar: continue
        tot+=1; mv=(db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100
        if mv>0.15: up+=1
        elif mv<-0.15: dn+=1
    if tot==0: continue
    regime='UP' if up/tot>0.6 else 'DOWN' if dn/tot>0.6 else 'CHOPPY'

    for sym in date_bars[date]:
        db=date_bars[date][sym]; pc=prev_close.get((date,sym)); ctx=daily_ctx.get((date,sym),{})
        if pc is None or len(db)<=scan_bar+20: continue

        entry=db[scan_bar]['close']
        morning=(entry-db[0]['open'])/db[0]['open']*100
        b0_body=abs(db[0]['close']-db[0]['open'])/(db[0]['high']-db[0]['low'])*100 if db[0]['high']!=db[0]['low'] else 0
        b0_green=db[0]['close']>db[0]['open']
        vol=sum(b['volume'] for b in db[:scan_bar+1])/(scan_bar+1)
        trend=ctx.get('trend','?')

        # What actually happens after bar 10?
        peak_long=max((db[j]['high']-entry)/entry*100 for j in range(scan_bar+1,min(len(db),70)))
        peak_short=max((entry-db[j]['low'])/entry*100 for j in range(scan_bar+1,min(len(db),70)))

        # CAM_R3 signal?
        cam_r3=False; cam_s3=False
        lp=prev_day_bars.get((date,sym),[])
        if lp:
            ph=max(b['high'] for b in lp);pl=min(b['low'] for b in lp);pcc=lp[-1]['close'];rng=ph-pl
            if rng>0:
                r3=pcc+rng*1.1/4; s3=pcc-rng*1.1/4
                for j in range(1,scan_bar+1):
                    atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                    if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3: cam_r3=True; break
                    if abs(db[j]['low']-s3)<atr*0.3 and db[j]['close']>s3: cam_s3=True; break

        # ORB signal?
        rh=db[0]['high'];rl=db[0]['low']
        orb_long=any(db[j]['close']>rh for j in range(1,scan_bar+1))
        orb_short=any(db[j]['close']<rl for j in range(1,scan_bar+1))

        # Momentum: 3+ consecutive same-direction bars
        consec_green=0; consec_red=0
        for k in range(scan_bar,max(scan_bar-5,0),-1):
            if db[k]['close']>db[k]['open']: consec_green+=1
            else: break
        for k in range(scan_bar,max(scan_bar-5,0),-1):
            if db[k]['close']<db[k]['open']: consec_red+=1
            else: break

        sig = {
            'date':date,'sym':sym,
            'morning':round(morning,2),'b0_body':round(b0_body,0),'b0_green':b0_green,
            'vol':round(vol,0),'trend':trend,'regime':regime,
            'cam_r3':cam_r3,'cam_s3':cam_s3,'orb_long':orb_long,'orb_short':orb_short,
            'consec_green':consec_green,'consec_red':consec_red,
            'peak_long':round(peak_long,3),'peak_short':round(peak_short,3),
            'hit_be_long': peak_long >= BREAKEVEN,
            'hit_be_short': peak_short >= BREAKEVEN,
        }
        all_signals.append(sig)

print(f'Total signal-days: {len(all_signals)}')

# NOW: Find condition combos where 90%+ hit breakeven
print(f'\nSEARCHING for 90%+ probability conditions...\n')

conditions = [
    # CAM_R3 + filters
    ('CAM_R3 SHORT', lambda s: s['cam_r3'], 'short'),
    ('CAM_R3 SHORT + trend DOWN', lambda s: s['cam_r3'] and s['trend']=='DOWN', 'short'),
    ('CAM_R3 SHORT + regime DOWN', lambda s: s['cam_r3'] and s['regime']=='DOWN', 'short'),
    ('CAM_R3 SHORT + morning<0', lambda s: s['cam_r3'] and s['morning']<0, 'short'),
    ('CAM_R3 SHORT + bar0 RED', lambda s: s['cam_r3'] and not s['b0_green'], 'short'),
    ('CAM_R3 SHORT + bar0 RED body<50', lambda s: s['cam_r3'] and not s['b0_green'] and s['b0_body']<50, 'short'),
    ('CAM_R3 SHORT + consec_red>=3', lambda s: s['cam_r3'] and s['consec_red']>=3, 'short'),
    ('CAM_R3 SHORT + trendDOWN + morning<0', lambda s: s['cam_r3'] and s['trend']=='DOWN' and s['morning']<0, 'short'),
    ('CAM_R3 SHORT + trendDOWN + regimeDOWN', lambda s: s['cam_r3'] and s['trend']=='DOWN' and s['regime']=='DOWN', 'short'),
    ('CAM_R3 SHORT + trendDOWN + consec>=2', lambda s: s['cam_r3'] and s['trend']=='DOWN' and s['consec_red']>=2, 'short'),
    ('CAM_R3 SHORT + vol>50K', lambda s: s['cam_r3'] and s['vol']>50000, 'short'),
    ('CAM_R3 SHORT + vol>50K + trendDOWN', lambda s: s['cam_r3'] and s['vol']>50000 and s['trend']=='DOWN', 'short'),

    # ORB + filters
    ('ORB LONG', lambda s: s['orb_long'], 'long'),
    ('ORB LONG + trend UP', lambda s: s['orb_long'] and s['trend']=='UP', 'long'),
    ('ORB LONG + regime UP', lambda s: s['orb_long'] and s['regime']=='UP', 'long'),
    ('ORB LONG + morning>0.5', lambda s: s['orb_long'] and s['morning']>0.5, 'long'),
    ('ORB LONG + bar0 GREEN', lambda s: s['orb_long'] and s['b0_green'], 'long'),
    ('ORB LONG + trendUP + morning>0', lambda s: s['orb_long'] and s['trend']=='UP' and s['morning']>0, 'long'),
    ('ORB LONG + trendUP + regimeUP', lambda s: s['orb_long'] and s['trend']=='UP' and s['regime']=='UP', 'long'),
    ('ORB SHORT', lambda s: s['orb_short'], 'short'),
    ('ORB SHORT + trend DOWN', lambda s: s['orb_short'] and s['trend']=='DOWN', 'short'),
    ('ORB SHORT + regime DOWN', lambda s: s['orb_short'] and s['regime']=='DOWN', 'short'),

    # Momentum
    ('Momentum LONG (3+ green)', lambda s: s['consec_green']>=3 and s['morning']>0.3, 'long'),
    ('Momentum LONG (3+ green) + trendUP', lambda s: s['consec_green']>=3 and s['morning']>0.3 and s['trend']=='UP', 'long'),
    ('Momentum SHORT (3+ red)', lambda s: s['consec_red']>=3 and s['morning']<-0.3, 'short'),
    ('Momentum SHORT (3+ red) + trendDOWN', lambda s: s['consec_red']>=3 and s['morning']<-0.3 and s['trend']=='DOWN', 'short'),

    # Morning move continuation
    ('Morning >1% LONG', lambda s: s['morning']>1, 'long'),
    ('Morning >1% LONG + trendUP', lambda s: s['morning']>1 and s['trend']=='UP', 'long'),
    ('Morning >1% LONG + bar0 GREEN body>70', lambda s: s['morning']>1 and s['b0_green'] and s['b0_body']>70, 'long'),
    ('Morning <-1% SHORT', lambda s: s['morning']<-1, 'short'),
    ('Morning <-1% SHORT + trendDOWN', lambda s: s['morning']<-1 and s['trend']=='DOWN', 'short'),

    # Combined
    ('CAM_R3 + trendDOWN + vol>50K + morn<0', lambda s: s['cam_r3'] and s['trend']=='DOWN' and s['vol']>50000 and s['morning']<0, 'short'),
    ('ORB + trendUP + morn>0.5 + bar0G', lambda s: s['orb_long'] and s['trend']=='UP' and s['morning']>0.5 and s['b0_green'], 'long'),
]

results = []
print(f"{'Condition':>50} | {'N':>5} {'WR':>5} {'AvgPnL':>7} | {'Trades/day':>10}")
print('-'*90)

for name, filt, direction in conditions:
    matching = [s for s in all_signals if filt(s)]
    if len(matching) < 20: continue

    if direction == 'long':
        hits = sum(1 for s in matching if s['hit_be_long'])
    else:
        hits = sum(1 for s in matching if s['hit_be_short'])

    wr = hits / len(matching) * 100
    days = len(set(s['date'] for s in matching))
    trades_per_day = len(matching) / days

    if direction == 'long':
        avg_peak = sum(s['peak_long'] for s in matching) / len(matching)
    else:
        avg_peak = sum(s['peak_short'] for s in matching) / len(matching)

    marker = ' <<<' if wr >= 85 else (' <<' if wr >= 80 else '')
    print(f"{name:>50} | {len(matching):>5} {wr:>4.0f}% {avg_peak:>+6.2f}% | {trades_per_day:>9.1f}/day{marker}")

    results.append({'name':name,'n':len(matching),'wr':round(wr,1),'avg_peak':round(avg_peak,3),'tpd':round(trades_per_day,1),'direction':direction})

# Show the 90%+ WR setups
print(f'\n{"="*80}')
print(f'SETUPS WITH 80%+ WR (hit +{BREAKEVEN}% at any point before 3PM):')
print(f'{"="*80}')

high_wr = [r for r in results if r['wr'] >= 80]
high_wr.sort(key=lambda x: (-x['wr'], -x['n']))
for r in high_wr:
    # If we trade all of these
    net_per_trade = r['avg_peak'] - 0.084  # Rough estimate
    daily_return = net_per_trade * r['tpd']
    annual = daily_return * 250
    compound = 100000
    for _ in range(250): compound *= (1 + daily_return/100)
    print(f"  {r['name']:>50}: WR={r['wr']:.0f}% N={r['n']} {r['tpd']:.1f}/day avg={r['avg_peak']:+.3f}%")
    print(f"    Net/trade={net_per_trade:+.3f}% daily={daily_return:+.3f}% Rs1L={compound:,.0f}")
