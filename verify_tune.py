"""
VERIFY: Is T=2.50/S=2.00 really better? Or is it overfitting?
1. Walk-forward test (train 3yr, test 1yr)
2. Compare actual loss sizes
3. Risk-adjusted returns (Sharpe-like)
4. Worst-case scenarios
"""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict
import math

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

all_dates=sorted(date_bars.keys())
prev_day={}; daily_trend={}
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars)); dc=[]
    for i,d in enumerate(dfs):
        db=date_bars[d].get(sym,[])
        if not db: continue
        c=db[-1]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[dfs[i-1]].get(sym,[])
            if pdb: prev_day[(d,sym)]={'high':max(b['high'] for b in pdb),'low':min(b['low'] for b in pdb),'close':pdb[-1]['close']}
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_trend[(d,sym)]='UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'

POS=1000000; CHARGES=386; SCAN_BAR=10

def run_backtest(dates_subset, target, stop):
    """Run backtest on specific dates. Returns list of trade P&Ls."""
    trades=[]; daily_pnl=defaultdict(float)
    for date in dates_subset:
        for sym in date_bars.get(date,{}):
            if sym in ('NIFTY_50','NIFTY_BANK'): continue
            db=date_bars[date][sym]
            if len(db)<=SCAN_BAR+20: continue
            if daily_trend.get((date,sym))!='DOWN': continue
            pd=prev_day.get((date,sym))
            if not pd: continue
            rng=pd['high']-pd['low']
            if rng<=0: continue
            r3=pd['close']+rng*1.1/4
            triggered=False
            for j in range(1,SCAN_BAR+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                    triggered=True; break
            if not triggered: continue

            entry=db[SCAN_BAR]['close']
            tp=entry*(1-target/100); sp=entry*(1+stop/100)
            ep=db[min(69,len(db)-1)]['close']; exit_r='eod'
            for k in range(SCAN_BAR+1,min(len(db),70)):
                if db[k]['low']<=tp: ep=tp; exit_r='target'; break
                if db[k]['high']>=sp: ep=sp; exit_r='stop'; break
            pnl=(entry-ep)/entry*100/100*POS - CHARGES
            trades.append({'pnl':pnl,'exit':exit_r,'date':date,'sym':sym,
                          'pnl_pct':(entry-ep)/entry*100})
            daily_pnl[date]+=pnl
    return trades, daily_pnl

configs=[
    (1.50, 1.00, 'ORIGINAL (T=1.50 S=1.00)'),
    (2.50, 2.00, 'TUNED (T=2.50 S=2.00)'),
    (1.75, 1.50, 'MIDDLE (T=1.75 S=1.50)'),
    (1.25, 1.25, 'CONSERVATIVE (T=1.25 S=1.25)'),
    (2.00, 1.50, 'BALANCED (T=2.00 S=1.50)'),
]

print(f'\n{"="*100}')
print('TEST 1: WALK-FORWARD — Train on 2022-2024, test on 2025-2026')
print('If tuned config works on UNSEEN data, it\'s not overfitting.')
print('='*100)

# Split dates
train_dates=[d for d in all_dates if d<'2025-01-01']
test_dates=[d for d in all_dates if d>='2025-01-01']
print(f'Train: {train_dates[0]} to {train_dates[-1]} ({len(train_dates)} days)')
print(f'Test:  {test_dates[0]} to {test_dates[-1]} ({len(test_dates)} days)')

print(f'\n{"Config":>35} {"Period":>6} {"N":>6} {"WR":>5} {"/trade":>8} {"Total":>14} {"AvgWin":>8} {"AvgLoss":>9}')
print('-'*95)

for t, s, label in configs:
    for period, dates_sub in [('TRAIN', train_dates), ('TEST', test_dates), ('ALL', all_dates)]:
        trades, dpnl = run_backtest(dates_sub, t, s)
        if not trades: continue
        n=len(trades); w=sum(1 for tr in trades if tr['pnl']>0)
        total=sum(tr['pnl'] for tr in trades)
        wins=[tr['pnl'] for tr in trades if tr['pnl']>0]
        losses=[tr['pnl'] for tr in trades if tr['pnl']<=0]
        aw=sum(wins)/len(wins) if wins else 0
        al=sum(losses)/len(losses) if losses else 0
        marker=' <<<' if period=='TEST' else ''
        print(f'{label:>35} {period:>6} {n:>6} {w/n*100:>4.0f}% Rs{total/n:>+7,.0f} Rs{total:>+13,.0f} Rs{aw:>+7,.0f} Rs{al:>+8,.0f}{marker}')
    print()

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*100}')
print('TEST 2: LOSS SIZE COMPARISON — How bad are the losses?')
print('='*100)

for t, s, label in configs:
    trades, dpnl = run_backtest(all_dates, t, s)
    losses=[tr for tr in trades if tr['pnl']<=0]
    stops=[tr for tr in trades if tr['exit']=='stop']

    losses_sorted=sorted([tr['pnl'] for tr in losses])
    worst_5=losses_sorted[:5]

    # Daily worst
    daily_vals=sorted(dpnl.values())
    worst_days=daily_vals[:5]

    # Max drawdown
    cum=0;peak=0;dd=0
    for d in sorted(dpnl.keys()):
        cum+=dpnl[d]; peak=max(peak,cum); dd=max(dd,peak-cum)

    print(f'\n  {label}:')
    print(f'    Stop losses: {len(stops)} trades')
    print(f'    Avg loss (all): Rs {sum(tr["pnl"] for tr in losses)/len(losses):,.0f}')
    print(f'    Avg stop loss: Rs {sum(tr["pnl"] for tr in stops)/len(stops):,.0f}' if stops else '    No stops')
    print(f'    Worst 5 trades: {", ".join(f"Rs {x:,.0f}" for x in worst_5)}')
    print(f'    Worst 5 days: {", ".join(f"Rs {x:,.0f}" for x in worst_days)}')
    print(f'    Max drawdown: Rs {dd:,.0f}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*100}')
print('TEST 3: RISK-ADJUSTED — Profit per unit of risk')
print('='*100)

print(f'\n{"Config":>35} {"/trade":>8} {"MaxDD":>10} {"Profit/DD":>10} {"WorstDay":>10} {"Sharpe":>8}')
print('-'*85)
for t, s, label in configs:
    trades, dpnl = run_backtest(all_dates, t, s)
    n=len(trades); total=sum(tr['pnl'] for tr in trades)
    per=total/n

    # Max drawdown
    cum=0;peak=0;dd=0
    for d in sorted(dpnl.keys()):
        cum+=dpnl[d]; peak=max(peak,cum); dd=max(dd,peak-cum)

    worst_day=min(dpnl.values())

    # Daily Sharpe-like ratio
    daily_returns=list(dpnl.values())
    avg_daily=sum(daily_returns)/len(daily_returns)
    std_daily=math.sqrt(sum((x-avg_daily)**2 for x in daily_returns)/len(daily_returns))
    sharpe=avg_daily/std_daily*math.sqrt(252) if std_daily>0 else 0

    profit_dd=total/dd if dd>0 else 999
    marker=' <<<' if profit_dd>80 else ''

    print(f'{label:>35} Rs{per:>+7,.0f} Rs{dd:>+9,.0f} {profit_dd:>9.1f}x Rs{worst_day:>+9,.0f} {sharpe:>7.2f}{marker}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*100}')
print('TEST 4: YEARLY CONSISTENCY — Does it work every year?')
print('='*100)

for t, s, label in configs:
    trades, _ = run_backtest(all_dates, t, s)
    yearly=defaultdict(lambda:{'n':0,'w':0,'pnl':0})
    for tr in trades:
        y=tr['date'][:4]
        yearly[y]['n']+=1; yearly[y]['pnl']+=tr['pnl']
        if tr['pnl']>0: yearly[y]['w']+=1

    print(f'\n  {label}:')
    all_positive=True
    for y in sorted(yearly):
        m=yearly[y]
        wr=m['w']/m['n']*100
        per=m['pnl']/m['n']
        marker=' <<<' if per>4000 else ''
        if per<0: all_positive=False
        print(f'    {y}: {m["n"]:>5} trades, WR={wr:.0f}%, Rs {m["pnl"]:>+11,.0f} (Rs {per:>+6,.0f}/trade){marker}')
    print(f'    All years positive: {"YES" if all_positive else "NO"}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*100}')
print('VERDICT')
print('='*100)
