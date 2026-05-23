"""
Daily P&L + Charges Analysis for Cam R3 Whitelist Portfolio.
Are we profitable AFTER brokerage, STT, GST, etc?
"""
import sys; sys.path.insert(0, '.')
import csv
from pathlib import Path
from collections import defaultdict

data_dir = Path('data/5min')
all_data = {}; date_bars = defaultdict(dict)
print('Loading...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    bars = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
             'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]
    all_data[sym] = bars
    by_date = defaultdict(list)
    for b in bars:
        by_date[b['timestamp'][:10]].append(b)
    for d, bs in by_date.items():
        date_bars[d][sym] = bs

all_dates = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))
prev_close_map = {}; prev_day_data = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    for i, d in enumerate(dates_for_sym):
        if i > 0:
            pdb = date_bars[dates_for_sym[i-1]].get(sym, [])
            if pdb:
                prev_close_map[(d, sym)] = pdb[-1]['close']
                prev_day_data[(d, sym)] = {'high': max(b['high'] for b in pdb), 'low': min(b['low'] for b in pdb), 'close': pdb[-1]['close']}

CAM_WL = {'HDFCBANK','HCLTECH','TITAN','SBIN','NESTLEIND','HEROMOTOCO','WIPRO','UPL','ASIANPAINT','HINDUNILVR'}

def simulate(db, eb, entry, d, stop, target, exit_bar=69):
    for j in range(eb+1, min(len(db), exit_bar+1)):
        if d=='LONG':
            if db[j]['low']<=stop: return stop
            if db[j]['high']>=target: return target
        else:
            if db[j]['high']>=stop: return stop
            if db[j]['low']<=target: return target
    return db[min(exit_bar,len(db)-1)]['close']

def pnl_calc(e,x,d): return (x-e)/e*100 if d=='LONG' else (e-x)/e*100

# Generate all whitelist Cam signals with rejection score
daily_trades = defaultdict(list)
for date in all_dates:
    for sym in CAM_WL:
        if sym not in date_bars[date]: continue
        db = date_bars[date][sym]
        pd_data = prev_day_data.get((date,sym))
        if pd_data is None or len(db)<30: continue
        h=pd_data['high'];l=pd_data['low'];c=pd_data['close'];rng=h-l
        if rng==0: continue
        r3=c+rng*1.1/4; r4=c+rng*1.1/2; s3=c-rng*1.1/4; s4=c-rng*1.1/2
        for j in range(1, min(len(db), 20)):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            tol=atr*0.3
            entry=None; d_dir=None
            if abs(db[j]['high']-r3)<tol and db[j]['close']<r3:
                entry=db[j]['close']; d_dir='SHORT'; stop=r4; target=c
                body=abs(db[j]['close']-db[j]['open'])
                wick=abs(db[j]['high']-r3)
                rejection=wick/(body+0.01)
            elif abs(db[j]['low']-s3)<tol and db[j]['close']>s3:
                entry=db[j]['close']; d_dir='LONG'; stop=s4; target=c
                body=abs(db[j]['close']-db[j]['open'])
                wick=abs(db[j]['low']-s3)
                rejection=wick/(body+0.01)
            if entry is None: continue
            ep=simulate(db,j,entry,d_dir,stop,target,69)
            pnl=pnl_calc(entry,ep,d_dir)
            daily_trades[date].append({'sym':sym,'pnl':pnl,'win':pnl>0,'rejection':rejection,'entry':entry})
            break

# === DAILY P&L ===
print('='*80)
print('DAILY P&L ANALYSIS')
print('='*80)

daily_pnls = []
for date in sorted(all_dates):
    trades = daily_trades.get(date, [])
    if not trades: continue
    day_pnl = sum(t['pnl'] for t in trades) / len(trades)
    daily_pnls.append({'date':date, 'pnl':day_pnl, 'n':len(trades),
                       'wins':sum(t['win'] for t in trades),
                       'raw_pnl_sum': sum(t['pnl'] for t in trades)})

total_days = len(daily_pnls)
green_days = sum(1 for d in daily_pnls if d['pnl'] > 0)
red_days = total_days - green_days
print(f'Trading days: {total_days}')
print(f'Green days: {green_days} ({green_days/total_days*100:.0f}%)')
print(f'Red days: {red_days} ({red_days/total_days*100:.0f}%)')

avg_green = sum(d['pnl'] for d in daily_pnls if d['pnl']>0)/green_days
avg_red = sum(d['pnl'] for d in daily_pnls if d['pnl']<=0)/red_days
print(f'Avg green day: {avg_green:+.3f}%')
print(f'Avg red day: {avg_red:+.3f}%')
print(f'Edge per day: green*prob + red*prob = {avg_green*green_days/total_days + avg_red*red_days/total_days:+.4f}%')

worst = min(daily_pnls, key=lambda d: d['pnl'])
best = max(daily_pnls, key=lambda d: d['pnl'])
print(f'Best day: {best["date"]} = {best["pnl"]:+.3f}%')
print(f'Worst day: {worst["date"]} = {worst["pnl"]:+.3f}%')

# Consecutive losing days
max_streak = 0; streak = 0
for d in daily_pnls:
    if d['pnl'] <= 0: streak += 1
    else: max_streak = max(max_streak, streak); streak = 0
max_streak = max(max_streak, streak)
print(f'Max consecutive red days: {max_streak}')

# === PICK 1 TRADE (best rejection) ===
print(f'\n{"="*80}')
print('SINGLE TRADE PER DAY (best rejection picker)')
print('='*80)

single_pnls = []
for date in sorted(all_dates):
    trades = daily_trades.get(date, [])
    if not trades: continue
    pick = max(trades, key=lambda t: t['rejection'])
    single_pnls.append({'date':date, 'pnl':pick['pnl'], 'win':pick['win'], 'entry':pick['entry']})

s_green = sum(1 for d in single_pnls if d['pnl']>0)
s_red = len(single_pnls) - s_green
print(f'Green days: {s_green}/{len(single_pnls)} ({s_green/len(single_pnls)*100:.0f}%)')
print(f'Red days: {s_red}/{len(single_pnls)} ({s_red/len(single_pnls)*100:.0f}%)')
s_avg_green = sum(d['pnl'] for d in single_pnls if d['pnl']>0)/s_green if s_green else 0
s_avg_red = sum(d['pnl'] for d in single_pnls if d['pnl']<=0)/s_red if s_red else 0
print(f'Avg green day: {s_avg_green:+.3f}%')
print(f'Avg red day: {s_avg_red:+.3f}%')

# === CHARGES ===
print(f'\n{"="*80}')
print('CHARGES ANALYSIS (Zerodha/Upstox intraday on Rs 1 Lakh)')
print('='*80)

capital = 100000

# Intraday charges for 1 trade on Rs 1 lakh:
# Brokerage: Rs 20/order x 2 = Rs 40
# STT: 0.025% on sell = Rs 25
# Exchange txn: 0.00345% x 2 = Rs 6.90
# GST: 18% on (brokerage + exchange) = 18% of Rs 46.90 = Rs 8.44
# Stamp duty: 0.003% on buy = Rs 3
# SEBI charges: 0.0001% x 2 = Rs 0.20
# TOTAL per trade: ~Rs 83.54

charges_per_trade = 83.54
charges_pct = charges_per_trade / capital * 100
print(f'Charges per trade (Rs 1L): Rs {charges_per_trade:.2f} ({charges_pct:.4f}%)')

# Single trade per day
print(f'\n--- 1 trade/day (best rejection) ---')
bal = capital
yearly_net = defaultdict(lambda: {'pnl':0,'charges':0,'days':0,'green':0})
for d in single_pnls:
    gross = d['pnl']
    net = gross - charges_pct  # Subtract charges
    bal *= (1 + net/100)
    y = d['date'][:4]
    yearly_net[y]['pnl'] += net
    yearly_net[y]['charges'] += charges_pct
    yearly_net[y]['days'] += 1
    if net > 0: yearly_net[y]['green'] += 1

total_net = sum(v['pnl'] for v in yearly_net.values())
total_charges_paid = sum(v['charges'] for v in yearly_net.values())
net_green = sum(1 for d in single_pnls if d['pnl'] - charges_pct > 0)
print(f'  Green days after charges: {net_green}/{len(single_pnls)} ({net_green/len(single_pnls)*100:.0f}%)')
print(f'  Total charges paid: Rs {total_charges_paid/100*capital:,.0f}')
print(f'  Gross P&L: {sum(d["pnl"] for d in single_pnls):+.1f}%')
print(f'  Net P&L: {total_net:+.1f}%')
print(f'  1 Lakh compound: Rs {bal:,.0f} ({(bal/capital-1)*100:+.1f}%)')
print(f'  Yearly:')
all_positive = True
for y in sorted(yearly_net):
    m = yearly_net[y]
    if m['pnl'] <= 0: all_positive = False
    print(f'    {y}: {m["days"]} days, {m["green"]} green, net={m["pnl"]:+.1f}%, charges=Rs {m["charges"]/100*capital:,.0f}')
if all_positive: print(f'  >>> EVERY YEAR PROFITABLE AFTER CHARGES <<<')

# Portfolio (6 trades/day)
print(f'\n--- 6 trades/day (portfolio, equal split) ---')
bal2 = capital
yearly_net2 = defaultdict(lambda: {'pnl':0,'days':0,'green':0})
for d in daily_pnls:
    n = d['n']
    per_trade_cap = capital / n  # Split capital
    daily_charges_total = charges_per_trade * n  # Rs 83 x N trades
    daily_charges_pct = daily_charges_total / capital * 100
    gross = d['pnl']
    net = gross - daily_charges_pct
    bal2 *= (1 + net/100)
    y = d['date'][:4]
    yearly_net2[y]['pnl'] += net
    yearly_net2[y]['days'] += 1
    if net > 0: yearly_net2[y]['green'] += 1

total_net2 = sum(v['pnl'] for v in yearly_net2.values())
net_green2 = sum(1 for d, dp in zip(daily_pnls, [d['pnl'] - charges_per_trade*d['n']/capital*100 for d in daily_pnls]) if dp > 0)
print(f'  Green days after charges: {net_green2}/{total_days}')
print(f'  Gross P&L: {sum(d["pnl"] for d in daily_pnls):+.1f}%')
print(f'  Net P&L: {total_net2:+.1f}%')
print(f'  1 Lakh compound: Rs {bal2:,.0f} ({(bal2/capital-1)*100:+.1f}%)')
print(f'  Yearly:')
for y in sorted(yearly_net2):
    m = yearly_net2[y]
    status = '+' if m['pnl'] > 0 else 'NEGATIVE'
    print(f'    {y}: {m["days"]} days, net={m["pnl"]:+.1f}%  {status}')

# === MONTHLY ===
print(f'\n{"="*80}')
print('MONTHLY P&L (single trade, after charges)')
print('='*80)
monthly = defaultdict(lambda: {'gross':0,'net':0,'days':0,'green':0})
for d in single_pnls:
    m = d['date'][:7]
    gross = d['pnl']
    net = gross - charges_pct
    monthly[m]['gross'] += gross
    monthly[m]['net'] += net
    monthly[m]['days'] += 1
    if net > 0: monthly[m]['green'] += 1

green_months = 0
for m in sorted(monthly):
    v = monthly[m]
    if v['net'] > 0: green_months += 1
    status = '+' if v['net'] > 0 else 'NEG'
    print(f'  {m}: {v["days"]}d, {v["green"]} green, gross={v["gross"]:+.2f}%, net={v["net"]:+.2f}%  {status}')

print(f'\nGreen months: {green_months}/{len(monthly)} ({green_months/len(monthly)*100:.0f}%)')

# === LAW OF AVERAGES ===
print(f'\n{"="*80}')
print('LAW OF AVERAGES CHECK')
print('='*80)
# Rolling 20-day WR
print('Rolling 20-day WR (single trade):')
for i in range(0, len(single_pnls)-20, 60):
    window = single_pnls[i:i+20]
    w = sum(1 for d in window if d['pnl']>0)
    avg = sum(d['pnl'] for d in window)/20
    print(f'  {window[0]["date"]} to {window[-1]["date"]}: WR={w/20*100:.0f}%, avg={avg:+.3f}%')

# Worst 20-day stretch
worst_20_wr = 100; worst_20_start = ''
for i in range(len(single_pnls)-20):
    window = single_pnls[i:i+20]
    w = sum(1 for d in window if d['pnl']>0)
    wr = w/20*100
    if wr < worst_20_wr:
        worst_20_wr = wr
        worst_20_start = window[0]['date']
        worst_20_pnl = sum(d['pnl'] for d in window)

print(f'\nWorst 20-day stretch: starts {worst_20_start}, WR={worst_20_wr:.0f}%, P&L={worst_20_pnl:+.2f}%')
