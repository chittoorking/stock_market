"""
REAL RISK ANALYSIS — Futures drawdowns vs capital.
How much capital do you ACTUALLY need to survive?
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

SCAN_BAR=10; TARGET=1.50; STOP=1.00

STOCK_LOTS={
    'ADANIENT':250,'ADANIPORTS':500,'APOLLOHOSP':100,'ASIANPAINT':200,
    'AXISBANK':400,'BAJAJ-AUTO':100,'BPCL':1000,'BHARTIARTL':450,
    'BRITANNIA':100,'CIPLA':400,'COALINDIA':1050,'DIVISLAB':100,
    'EICHERMOT':125,'GRASIM':275,'HCLTECH':350,'HDFCBANK':400,
    'HDFCLIFE':700,'HEROMOTOCO':100,'HINDALCO':850,'HINDUNILVR':200,
    'ICICIBANK':525,'ITC':1600,'INDUSINDBK':500,'INFY':400,
    'JSWSTEEL':675,'LT':150,'M&M':350,'MARUTI':50,
    'NTPC':1500,'NESTLEIND':25,'ONGC':1925,'POWERGRID':1800,
    'RELIANCE':250,'SBILIFE':375,'SBIN':750,'SUNPHARMA':350,
    'TCS':125,'TATACONSUM':500,'TATAMOTORS':550,'TATASTEEL':1700,
    'TECHM':350,'TITAN':175,'UPL':1300,'ULTRACEMCO':50,
    'WIPRO':1000,
}

def fut_charges(lot_value):
    brok=20; stt=lot_value*0.0125/100; exch=lot_value*0.00173/100*2
    stamp=lot_value*0.002/100; sebi=lot_value/10000000*10
    gst=(brok+exch)*0.18
    return round(brok+stt+exch+stamp+sebi+gst, 2)

# Get all trades with details
all_trades = []
for date in all_dates:
    for sym in date_bars[date]:
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
        tp=entry*(1-TARGET/100); sp=entry*(1+STOP/100)
        ep=db[min(69,len(db)-1)]['close']; exit_r='eod'
        for k in range(SCAN_BAR+1,min(len(db),70)):
            if db[k]['low']<=tp: ep=tp; exit_r='target'; break
            if db[k]['high']>=sp: ep=sp; exit_r='stop'; break

        pnl_pct=(entry-ep)/entry*100
        lot=STOCK_LOTS.get(sym, 500)
        lot_value=entry*lot
        margin=lot_value*0.15  # 15% SPAN margin
        pnl_1lot=(entry-ep)*lot - fut_charges(lot_value)

        all_trades.append({
            'date':date, 'sym':sym, 'entry':entry, 'exit':round(ep,2),
            'pnl_pct':round(pnl_pct,3), 'lot':lot,
            'lot_value':round(lot_value,0), 'margin':round(margin,0),
            'pnl_1lot':round(pnl_1lot,0), 'exit_r':exit_r,
            'win': pnl_1lot > 0,
        })

print(f'{len(all_trades)} trades loaded\n')

# ═══════════════════════════════════════════════════════════════
# PROBLEM: How big are the losses?
# ═══════════════════════════════════════════════════════════════
print('='*80)
print('THE PROBLEM: How big are individual losses?')
print('='*80)

losses = sorted([t for t in all_trades if not t['win']], key=lambda x: x['pnl_1lot'])
print(f'\n  Worst 20 individual trade losses (1 lot):')
for i, t in enumerate(losses[:20], 1):
    print(f'    {i:>2}. {t["date"]} {t["sym"]:>12} | Lot value Rs {t["lot_value"]:>9,.0f} | '
          f'Margin Rs {t["margin"]:>7,.0f} | Loss Rs {t["pnl_1lot"]:>+8,.0f} ({t["pnl_pct"]:>+6.3f}%)')

# Daily losses
daily_pnl = defaultdict(float)
daily_trades = defaultdict(int)
daily_losses = defaultdict(float)
for t in all_trades:
    daily_pnl[t['date']] += t['pnl_1lot']
    daily_trades[t['date']] += 1
    if t['pnl_1lot'] < 0:
        daily_losses[t['date']] += t['pnl_1lot']

print(f'\n  Worst 20 DAILY losses (1 lot, all trades that day combined):')
worst_days = sorted(daily_pnl.items(), key=lambda x: x[1])
for i, (date, pnl) in enumerate(worst_days[:20], 1):
    n = daily_trades[date]
    print(f'    {i:>2}. {date} | {n} trades | Day P&L Rs {pnl:>+9,.0f} | Losses only: Rs {daily_losses[date]:>+9,.0f}')

# ═══════════════════════════════════════════════════════════════
# THE FIX: Proper position sizing
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print('THE FIX: What capital do you need to survive the worst days?')
print('='*80)

worst_day_loss = min(daily_pnl.values())
worst_3day = 0
dates_list = sorted(daily_pnl.keys())
for i in range(len(dates_list)-2):
    s = sum(daily_pnl.get(dates_list[i+j], 0) for j in range(3))
    worst_3day = min(worst_3day, s)

worst_5day = 0
for i in range(len(dates_list)-4):
    s = sum(daily_pnl.get(dates_list[i+j], 0) for j in range(5))
    worst_5day = min(worst_5day, s)

print(f'\n  With 1 lot per signal (no limits):')
print(f'    Worst single day: Rs {worst_day_loss:+,.0f}')
print(f'    Worst 3-day streak: Rs {worst_3day:+,.0f}')
print(f'    Worst 5-day streak: Rs {worst_5day:+,.0f}')
print(f'    To survive worst day + 50% buffer: Rs {abs(worst_day_loss)*1.5:,.0f} minimum capital')
print(f'    To survive worst 5-day + 50% buffer: Rs {abs(worst_5day)*1.5:,.0f} minimum capital')

# ═══════════════════════════════════════════════════════════════
# SAFE SIZING: Limit max trades per day + max loss per day
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print('SAFE SIZING: Limit trades per day, cap daily loss')
print('='*80)

for MAX_PER_DAY in [1, 2, 3, 5, 10, 99]:
    for MAX_LOT_VALUE in [300000, 500000, 1000000, 99999999]:
        trades_taken = []
        daily = defaultdict(lambda: {'n':0, 'pnl':0})

        for t in all_trades:
            # Skip if too many trades today
            if daily[t['date']]['n'] >= MAX_PER_DAY:
                continue
            # Skip if lot value too high
            if t['lot_value'] > MAX_LOT_VALUE:
                continue
            daily[t['date']]['n'] += 1
            daily[t['date']]['pnl'] += t['pnl_1lot']
            trades_taken.append(t)

        if len(trades_taken) < 100: continue
        n = len(trades_taken)
        w = sum(1 for t in trades_taken if t['win'])
        total = sum(t['pnl_1lot'] for t in trades_taken)
        worst = min(v['pnl'] for v in daily.values() if v['n'] > 0)
        avg_margin = sum(t['margin'] for t in trades_taken) / n

        # Max drawdown
        cum=0; peak=0; max_dd=0
        for d in sorted(daily.keys()):
            if daily[d]['n'] == 0: continue
            cum += daily[d]['pnl']
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)

        lot_label = f'Rs {MAX_LOT_VALUE/100000:.0f}L' if MAX_LOT_VALUE < 9999999 else 'Any'
        safe_cap = abs(worst) * 3  # Need 3x worst day as capital

        if total > 0:
            marker = ' <<<' if total/n > 1000 and abs(worst) < 50000 else ''
            print(f'  Max {MAX_PER_DAY}/day, lot<={lot_label:>5}: {n:>5} trades, WR={w/n*100:.0f}%, '
                  f'Rs {total/n:>+6,.0f}/trade, Total Rs {total:>+11,.0f}, '
                  f'Worst day Rs {worst:>+8,.0f}, MaxDD Rs {max_dd:>+9,.0f}, '
                  f'SafeCap Rs {safe_cap:>8,.0f}{marker}')

# ═══════════════════════════════════════════════════════════════
# BEST SETUP: Limited risk with proper capital
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print('BEST SAFE SETUPS — Compound simulation')
print('='*80)

configs = [
    (3, 500000, 'Max 3 trades/day, lot <= Rs 5L'),
    (5, 500000, 'Max 5 trades/day, lot <= Rs 5L'),
    (3, 1000000, 'Max 3 trades/day, lot <= Rs 10L'),
    (5, 1000000, 'Max 5 trades/day, lot <= Rs 10L'),
    (2, 500000, 'Max 2 trades/day, lot <= Rs 5L'),
    (99, 99999999, 'UNLIMITED (dangerous)'),
]

for MAX_PER_DAY, MAX_LOT, label in configs:
    # First pass: collect daily P&L
    daily_pnl_safe = defaultdict(float)
    daily_n = defaultdict(int)
    trade_list = []

    for t in all_trades:
        if daily_n[t['date']] >= MAX_PER_DAY: continue
        if t['lot_value'] > MAX_LOT: continue
        daily_n[t['date']] += 1
        daily_pnl_safe[t['date']] += t['pnl_1lot']
        trade_list.append(t)

    if len(trade_list) < 100: continue

    n = len(trade_list)
    w = sum(1 for t in trade_list if t['win'])
    total = sum(t['pnl_1lot'] for t in trade_list)
    worst_d = min(daily_pnl_safe.values())

    print(f'\n  {label}:')
    print(f'    {n} trades, WR={w/n*100:.0f}%, Rs {total/n:>+,.0f}/trade, Total Rs {total:>+,.0f}')
    print(f'    Worst day: Rs {worst_d:>+,.0f}')

    # Compound
    for START in [300000, 500000, 1000000]:
        cap = float(START); peak = cap; max_dd = 0; max_dd_pct = 0

        for d in sorted(daily_pnl_safe.keys()):
            day_pnl = daily_pnl_safe[d]
            # Scale: only risk what we can afford
            # If capital < 2x worst day, trade 1 lot; else scale
            scale = max(0.5, min(3, cap / (abs(worst_d) * 3)))
            cap += day_pnl * scale
            cap = max(cap, 10000)
            peak = max(peak, cap)
            dd_pct = (peak - cap) / peak * 100
            max_dd = max(max_dd, peak - cap)
            max_dd_pct = max(max_dd_pct, dd_pct)

        ret = (cap/START - 1) * 100
        print(f'    Rs {START/100000:.0f}L -> Rs {cap:>12,.0f} ({ret:>+8,.0f}%) | Max DD: {max_dd_pct:.1f}% (Rs {max_dd:>+,.0f})')

# EQUITY comparison
print(f'\n  EQUITY BASELINE (Rs 10L position, Rs 386 charges):')
for START in [300000, 500000, 1000000]:
    cap = float(START); peak = cap; max_dd_pct = 0
    for t in all_trades:
        pos = min(cap * 0.20 * 5, 1000000)  # Max Rs 10L
        pnl = t['pnl_pct']/100 * pos - 386
        cap += pnl
        cap = max(cap, 10000)
        peak = max(peak, cap)
        max_dd_pct = max(max_dd_pct, (peak-cap)/peak*100)
    ret = (cap/START-1)*100
    print(f'    Rs {START/100000:.0f}L -> Rs {cap:>12,.0f} ({ret:>+8,.0f}%) | Max DD: {max_dd_pct:.1f}%')
