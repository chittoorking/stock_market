"""
FINAL SYSTEM: CAM_R3 + trendDOWN, Target 1.00%, Stop 0.75%
63% WR, +102% median in 1 year.
Run on FULL 1087 days (4 years) to validate.
"""
import sys, io, csv, random
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
prev_day_bars={}; daily_ctx={}
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars)); dc=[]
    for i,d in enumerate(dfs):
        db=date_bars[d].get(sym,[])
        if not db: continue
        c=db[-1]['close']; dc.append(c)
        if i>0: prev_day_bars[(d,sym)]=date_bars[dfs[i-1]].get(sym,[])
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_ctx[(d,sym)]={'trend':'UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'}
print('Done.\n', flush=True)

scan_bar=10; TARGET=1.00; STOP=0.75

# Run on ALL 1087 days
all_trades=[]
for date in all_dates:
    for sym in date_bars[date]:
        db=date_bars[date][sym]; ctx=daily_ctx.get((date,sym),{})
        if len(db)<=scan_bar+20 or ctx.get('trend')!='DOWN': continue
        lp=prev_day_bars.get((date,sym),[])
        if not lp: continue
        ph=max(b['high'] for b in lp);pl=min(b['low'] for b in lp);pcc=lp[-1]['close'];rng=ph-pl
        if rng<=0: continue
        r3=pcc+rng*1.1/4
        cam_r3=False
        for j in range(1,scan_bar+1):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3: cam_r3=True; break
        if not cam_r3: continue

        entry=db[scan_bar]['close']
        tp=entry*(1-TARGET/100); sp=entry*(1+STOP/100)
        ep=db[min(69,len(db)-1)]['close']; exit_r='eod'
        for k in range(scan_bar+1,min(len(db),70)):
            if db[k]['low']<=tp: ep=tp; exit_r='target'; break
            if db[k]['high']>=sp: ep=sp; exit_r='stop'; break
        pnl=(entry-ep)/entry*100
        all_trades.append({'date':date,'sym':sym,'pnl':round(pnl,4),'win':pnl>0,'exit':exit_r})

print(f'FINAL SYSTEM: CAM_R3 SHORT + trendDOWN | Target {TARGET}% | Stop {STOP}%')
print(f'='*80)

w=sum(t['win'] for t in all_trades); n=len(all_trades)
gross=sum(t['pnl'] for t in all_trades)
days=len(set(t['date'] for t in all_trades))
tpd=n/days

print(f'4-YEAR RESULTS ({len(all_dates)} days):')
print(f'  Trades: {n} | Trading days: {days} | Trades/day: {tpd:.1f}')
print(f'  WR: {w}/{n} = {w/n*100:.0f}%')
print(f'  Gross: {gross:+.1f}%')
print(f'  Avg per trade: {gross/n:+.3f}%')

# By exit
for ex in ['target','stop','eod']:
    sub=[t for t in all_trades if t['exit']==ex]
    if sub:
        sw=sum(t['win'] for t in sub)
        print(f'  {ex}: {len(sub)} ({len(sub)/n*100:.0f}%), WR={sw/len(sub)*100:.0f}%')

# Yearly
yearly=defaultdict(lambda:{'w':0,'l':0,'pnl':0,'n':0})
for t in all_trades:
    y=t['date'][:4]; yearly[y]['n']+=1; yearly[y]['pnl']+=t['pnl']
    if t['win']: yearly[y]['w']+=1
    else: yearly[y]['l']+=1

print(f'\nYearly:')
for y in sorted(yearly):
    m=yearly[y]; t2=m['w']+m['l']; wr=m['w']/t2*100
    print(f'  {y}: {t2} trades, WR={wr:.0f}%, gross={m["pnl"]:+.1f}%, avg={m["pnl"]/t2:+.3f}%')

# COMPOUND SIMULATION
print(f'\n{"="*80}')
print('COMPOUND SIMULATION')
print('='*80)

for capital_label, CAPITAL, sizing in [
    ('Rs 1L, 100% sizing', 100000, 1.0),
    ('Rs 5L, 20% sizing', 500000, 0.20),
    ('Rs 10L, 20% sizing', 1000000, 0.20),
    ('Rs 10L, 30% sizing', 1000000, 0.30),
    ('Rs 25L, 20% sizing', 2500000, 0.20),
]:
    # Sort trades by date and simulate compounding
    bal = CAPITAL
    max_bal = bal; max_dd = 0
    yearly_bal = {}

    for t in sorted(all_trades, key=lambda x: x['date']):
        size = bal * sizing
        # Charges: brokerage Rs 40 + STT + exchange + GST
        charges = 40 + size*0.025/100 + size*0.00345/100*2 + (40+size*0.00345/100*2)*0.18 + size*0.003/100

        if t['win']:
            bal += size * TARGET / 100 - charges
        else:
            bal -= size * STOP / 100 + charges

        max_bal = max(max_bal, bal)
        dd = (max_bal - bal) / max_bal * 100
        max_dd = max(max_dd, dd)

        y = t['date'][:4]
        yearly_bal[y] = bal

    total_return = (bal / CAPITAL - 1) * 100
    years = len(all_dates) / 250
    annual = total_return / years

    print(f'\n{capital_label}:')
    print(f'  Start: Rs {CAPITAL:>12,.0f}')
    print(f'  End:   Rs {bal:>12,.0f} ({total_return:+.0f}%)')
    print(f'  Annual: {annual:+.0f}%')
    print(f'  Max drawdown: {max_dd:.1f}%')
    print(f'  Yearly progression:')
    for y in sorted(yearly_bal):
        print(f'    {y}: Rs {yearly_bal[y]:>12,.0f}')

# WALK-FORWARD: First 3 years train, last year test
print(f'\n{"="*80}')
print('WALK-FORWARD VALIDATION')
print('='*80)

split_date = all_dates[int(len(all_dates)*0.75)]
train=[t for t in all_trades if t['date']<split_date]
test=[t for t in all_trades if t['date']>=split_date]

tw=sum(t['win'] for t in train); tn=len(train)
ew=sum(t['win'] for t in test); en=len(test)
tg=sum(t['pnl'] for t in train); eg=sum(t['pnl'] for t in test)

print(f'Train ({all_dates[0]} to {split_date}): {tn} trades, WR={tw/tn*100:.0f}%, gross={tg:+.1f}%')
print(f'Test  ({split_date} to {all_dates[-1]}): {en} trades, WR={ew/en*100:.0f}%, gross={eg:+.1f}%')
holds = 'HOLDS' if en>0 and ew/en*100 >= tw/tn*100 - 5 else 'DEGRADES'
print(f'Walk-forward: {holds}')

# Monte Carlo: 100 trials with random order
print(f'\n{"="*80}')
print('MONTE CARLO (100 trials, random trade order)')
print('Rs 10L, 20% sizing')
print('='*80)

mc_results=[]
for trial in range(100):
    random.seed(trial)
    shuffled=list(all_trades); random.shuffle(shuffled)
    bal=1000000
    for t in shuffled:
        size=bal*0.20
        charges=40+size*0.025/100+size*0.00345/100*2+(40+size*0.00345/100*2)*0.18+size*0.003/100
        if t['win']: bal+=size*TARGET/100-charges
        else: bal-=size*STOP/100+charges
        if bal<50000: break
    mc_results.append(bal)

mc_results.sort()
print(f'  Worst:   Rs {mc_results[0]:>12,.0f} ({(mc_results[0]/1000000-1)*100:+.0f}%)')
print(f'  5th pct: Rs {mc_results[5]:>12,.0f} ({(mc_results[5]/1000000-1)*100:+.0f}%)')
print(f'  25th:    Rs {mc_results[25]:>12,.0f} ({(mc_results[25]/1000000-1)*100:+.0f}%)')
print(f'  Median:  Rs {mc_results[50]:>12,.0f} ({(mc_results[50]/1000000-1)*100:+.0f}%)')
print(f'  75th:    Rs {mc_results[75]:>12,.0f} ({(mc_results[75]/1000000-1)*100:+.0f}%)')
print(f'  Best:    Rs {mc_results[99]:>12,.0f} ({(mc_results[99]/1000000-1)*100:+.0f}%)')
print(f'  Bust (<50K): {sum(1 for r in mc_results if r<50000)}/100')
