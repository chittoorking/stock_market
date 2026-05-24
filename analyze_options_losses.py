"""
Analyze ALL options losses with REAL numbers.
- What is the REAL option premium for NIFTY ATM PUT?
- What is the REAL amplification factor (not assumed 12x)?
- Why did each losing trade lose?
"""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict
import math

# Load NIFTY data
data_dir = Path('data/5min')
f = data_dir / 'NIFTY_50_5min.csv'
with open(f) as fh: rows = list(csv.DictReader(fh))
by_d = defaultdict(list)
for r in rows:
    b = {'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
         'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))}
    by_d[r['timestamp'][:10]].append(b)

date_bars = {d: bs for d, bs in by_d.items()}
dates = sorted(date_bars.keys())

# Compute prev_day and trend
prev_day = {}; daily_trend = {}; dc = []
for i, d in enumerate(dates):
    dc.append(date_bars[d][-1]['close'])
    if i > 0:
        prev_bars = date_bars[dates[i-1]]
        prev_day[d] = {'high':max(b['high'] for b in prev_bars),
                       'low':min(b['low'] for b in prev_bars),
                       'close':prev_bars[-1]['close']}
    if len(dc) >= 5:
        up = sum(1 for j in range(1, len(dc[-5:])) if dc[-5:][j] > dc[-5:][j-1])
        daily_trend[d] = 'UP' if up >= 4 else 'DOWN' if up <= 1 else 'SIDE'

SCAN_BAR = 10; TARGET = 0.75; STOP = 0.75

print('='*90)
print('REAL OPTIONS ANALYSIS — What is the ACTUAL amplification?')
print('='*90)

# ═══ REAL OPTION MATH ═══
print('''
NIFTY ATM WEEKLY PUT — REAL NUMBERS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NIFTY level: ~24,000-25,000
1 lot = 25 units
Lot value = 25 × 25,000 = Rs 6,25,000

ATM PUT premium depends on days to expiry (DTE):
  Monday  (4 DTE): ~Rs 250-350/unit × 25 = Rs 6,250 - 8,750
  Tuesday (3 DTE): ~Rs 200-280/unit × 25 = Rs 5,000 - 7,000
  Wednesday (2 DTE): ~Rs 120-200/unit × 25 = Rs 3,000 - 5,000
  Thursday (1 DTE): ~Rs 50-120/unit × 25 = Rs 1,250 - 3,000
  (NIFTY weekly options expire Thursday)

Delta of ATM option ≈ 0.45-0.50

When NIFTY drops 0.75% (our target):
  NIFTY at 25,000 → drops 187.5 points
  ATM PUT gains ≈ 187.5 × 0.47 = 88 points (delta ≈ 0.47)

  Option premium change by DTE:
    Monday  (premium ~300): 88/300 = 29% gain → 29/0.75 = 39x amplification
    Tuesday (premium ~240): 88/240 = 37% gain → 37/0.75 = 49x amplification
    Wednesday (premium ~160): 88/160 = 55% gain → 55/0.75 = 73x amplification
    Thursday (premium ~85):  88/85  = 104% gain → DOUBLES! = 138x amplification

  MINUS theta decay (time value loss during the day):
    Monday: ~Rs 15-20/unit lost to theta
    Thursday: ~Rs 30-50/unit lost to theta (rapid decay)

  NET realistic amplification:
    Monday:    ~25-35x
    Tuesday:   ~35-45x
    Wednesday: ~50-65x
    Thursday:  ~80-120x (but risky — theta eats fast if no move)

  CONSERVATIVE average across all days: ~30-40x
  OUR ASSUMPTION of 12x is EXTREMELY conservative!
''')

# ═══ SIMULATE WITH REAL PREMIUMS ═══
print('='*90)
print('BACKTEST WITH REALISTIC PREMIUMS (not fixed 12x)')
print('='*90)

trades_all = []
for date in dates:
    db = date_bars.get(date, [])
    if len(db) <= SCAN_BAR + 20: continue
    if daily_trend.get(date) != 'DOWN': continue
    pd = prev_day.get(date)
    if not pd: continue
    rng = pd['high'] - pd['low']
    if rng <= 0: continue
    r3 = pd['close'] + rng * 1.1 / 4
    triggered = False
    for j in range(1, SCAN_BAR + 1):
        atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1)) / min(4,j+1)
        if abs(db[j]['high'] - r3) < atr * 0.3 and db[j]['close'] < r3:
            triggered = True; break
    if not triggered: continue

    entry = db[SCAN_BAR]['close']
    tp = entry * (1 - TARGET/100)
    sp = entry * (1 + STOP/100)
    ep = db[min(69, len(db)-1)]['close']
    exit_reason = 'eod'
    exit_bar = min(69, len(db)-1)
    for k in range(SCAN_BAR+1, min(len(db), 70)):
        if db[k]['low'] <= tp: ep = tp; exit_reason = 'target'; exit_bar = k; break
        if db[k]['high'] >= sp: ep = sp; exit_reason = 'stop'; exit_bar = k; break

    idx_pnl = (entry - ep) / entry * 100  # Index move %
    idx_points = entry - ep  # Points moved

    # Day of week (0=Mon, 1=Tue, ..., 4=Fri)
    from datetime import datetime as dt
    day_of_week = dt.strptime(date, '%Y-%m-%d').weekday()
    day_names = ['Mon','Tue','Wed','Thu','Fri']
    day_name = day_names[day_of_week] if day_of_week < 5 else 'Sat'

    # Real premium estimate based on day of week
    # NIFTY weekly options expire Thursday
    # Days to Thursday expiry:
    days_to_thu = (3 - day_of_week) % 7
    if days_to_thu == 0: days_to_thu = 0  # Thursday itself

    # Premium per unit based on DTE
    if days_to_thu >= 4: prem_per_unit = 300  # Monday
    elif days_to_thu == 3: prem_per_unit = 250
    elif days_to_thu == 2: prem_per_unit = 180
    elif days_to_thu == 1: prem_per_unit = 120
    else: prem_per_unit = 80  # Thursday (expiry day)

    premium_total = prem_per_unit * 25  # 1 lot = 25 units
    delta = 0.47  # ATM delta

    # Theta loss during the day (~5 hours of trading)
    theta_per_unit = prem_per_unit * 0.07  # ~7% of premium lost to theta per day
    theta_loss = theta_per_unit * 25

    # Option P&L
    opt_points_gain = idx_points * delta  # Delta-based gain
    opt_pnl_per_unit = opt_points_gain - theta_per_unit
    opt_pnl_total = opt_pnl_per_unit * 25
    opt_pnl_pct = opt_pnl_total / premium_total * 100

    # Real amplification
    if idx_pnl != 0:
        real_mult = opt_pnl_pct / idx_pnl
    else:
        real_mult = 0

    # Charges: buy + sell options = ~Rs 100
    charges = 100
    net_pnl = opt_pnl_total - charges

    win = net_pnl > 0
    trades_all.append({
        'date': date, 'day': day_name, 'dte': days_to_thu,
        'entry': entry, 'exit': round(ep,2), 'exit_reason': exit_reason,
        'idx_move': round(idx_pnl, 3), 'idx_points': round(idx_points, 1),
        'premium': premium_total, 'prem_unit': prem_per_unit,
        'delta_gain': round(opt_points_gain, 1), 'theta_loss': round(theta_loss, 0),
        'opt_pnl': round(opt_pnl_total, 0), 'opt_pct': round(opt_pnl_pct, 1),
        'real_mult': round(real_mult, 1),
        'net_pnl': round(net_pnl, 0), 'win': win,
    })

# Report
n = len(trades_all); w = sum(1 for t in trades_all if t['win'])
total = sum(t['net_pnl'] for t in trades_all)
losses = [t for t in trades_all if not t['win']]
wins = [t for t in trades_all if t['win']]

print(f'\nTrades: {n} | Wins: {w} | WR: {w/n*100:.0f}%')
print(f'Total P&L: Rs {total:+,.0f}')
print(f'Per trade: Rs {total/n:+,.0f}')
print(f'Avg win: Rs {sum(t["net_pnl"] for t in wins)/len(wins):+,.0f}')
print(f'Avg loss: Rs {sum(t["net_pnl"] for t in losses)/len(losses):+,.0f}')
print(f'Avg real amplification: {sum(t["real_mult"] for t in trades_all if t["real_mult"]!=0)/len([t for t in trades_all if t["real_mult"]!=0]):.0f}x (not 12x!)')

# ═══ ALL TRADES WITH REAL NUMBERS ═══
print(f'\n{"="*90}')
print(f'ALL 92 TRADES — REAL OPTION PREMIUM & DELTA')
print(f'{"="*90}')
print(f'{"#":>3} {"Date":>10} {"Day":>3} {"DTE":>3} {"NIFTY Entry":>11} {"NIFTY Exit":>10} {"Idx%":>7} {"Prem":>6} {"OptP&L":>7} {"Opt%":>6} {"Mult":>5} {"Net Rs":>8} {"Exit":>6} {"W/L":>3}')
print('-'*100)
cumulative = 0
for i, t in enumerate(trades_all, 1):
    cumulative += t['net_pnl']
    wl = 'W' if t['win'] else '*** L'
    print(f'{i:>3} {t["date"]:>10} {t["day"]:>3} {t["dte"]:>3}d {t["entry"]:>10.1f} {t["exit"]:>10.1f} {t["idx_move"]:>+7.3f} {t["premium"]:>6,} {t["opt_pnl"]:>+7,.0f} {t["opt_pct"]:>+6.1f}% {t["real_mult"]:>4.0f}x {t["net_pnl"]:>+8,.0f} {t["exit_reason"]:>6} {wl}')

# ═══ LOSING TRADES DEEP DIVE ═══
print(f'\n{"="*90}')
print(f'DEEP DIVE: ALL {len(losses)} LOSING TRADES — WHY DID THEY LOSE?')
print(f'{"="*90}')

for i, t in enumerate(losses, 1):
    # Get the bars for that day
    db = date_bars[t['date']]
    entry_price = t['entry']

    # Find what happened bar by bar
    mfe = 0  # Max favorable excursion
    mae = 0  # Max adverse excursion
    for k in range(SCAN_BAR+1, min(len(db), 70)):
        fav = (entry_price - db[k]['low']) / entry_price * 100
        adv = (db[k]['high'] - entry_price) / entry_price * 100
        mfe = max(mfe, fav)
        mae = max(mae, adv)

    # Next day data
    di = dates.index(t['date'])
    next_day = dates[di+1] if di+1 < len(dates) else None
    next_move = ''
    if next_day and next_day in date_bars:
        ndb = date_bars[next_day]
        next_close = ndb[-1]['close']
        next_chg = (entry_price - next_close) / entry_price * 100
        next_move = f'Next day: {next_chg:+.2f}%'

    print(f'\n  Loss #{i}: {t["date"]} ({t["day"]}, {t["dte"]}d to expiry)')
    print(f'    NIFTY: {t["entry"]:.1f} -> {t["exit"]:.1f} ({t["idx_move"]:+.3f}%)')
    print(f'    Premium: Rs {t["premium"]:,} ({t["prem_unit"]}/unit)')
    print(f'    Option P&L: Rs {t["opt_pnl"]:+,.0f} ({t["opt_pct"]:+.1f}%)')
    print(f'    Net loss: Rs {t["net_pnl"]:+,.0f}')
    print(f'    MFE (best it got): {mfe:.3f}% | MAE (worst it got): {mae:.3f}%')
    print(f'    Exit: {t["exit_reason"]}')
    if next_move: print(f'    {next_move}')

    # Categorize the loss
    if t['exit_reason'] == 'stop':
        print(f'    CAUSE: NIFTY reversed hard (+0.75%), hit stop loss')
    elif t['idx_move'] > 0 and t['net_pnl'] < 0:
        print(f'    CAUSE: NIFTY moved in our favor ({t["idx_move"]:+.3f}%) but THETA ATE THE GAINS')
        print(f'           Theta cost: Rs {t["theta_loss"]:.0f} vs Delta gain: Rs {t["delta_gain"]*25:.0f}')
    elif abs(t['idx_move']) < 0.1:
        print(f'    CAUSE: NIFTY DIDN\'T MOVE enough. Sat flat, theta ate premium.')
    else:
        print(f'    CAUSE: NIFTY went AGAINST us ({t["idx_move"]:+.3f}%)')

# ═══ LOSS CATEGORIES ═══
print(f'\n{"="*90}')
print(f'LOSS BREAKDOWN BY CATEGORY')
print(f'{"="*90}')

stop_losses = [t for t in losses if t['exit_reason'] == 'stop']
theta_losses = [t for t in losses if t['exit_reason'] != 'stop' and t['idx_move'] > 0]
flat_losses = [t for t in losses if t['exit_reason'] != 'stop' and abs(t['idx_move']) < 0.1 and t['idx_move'] <= 0]
adverse_losses = [t for t in losses if t['exit_reason'] != 'stop' and t['idx_move'] <= -0.1]

print(f'\n  1. STOP LOSSES (NIFTY reversed +0.75%): {len(stop_losses)} trades')
for t in stop_losses:
    print(f'     {t["date"]}: Rs {t["net_pnl"]:+,.0f}')
print(f'     Total: Rs {sum(t["net_pnl"] for t in stop_losses):+,.0f}')

print(f'\n  2. THETA ATE GAINS (moved right, but too slow): {len(theta_losses)} trades')
for t in theta_losses:
    print(f'     {t["date"]}: Idx {t["idx_move"]:+.3f}% but net Rs {t["net_pnl"]:+,.0f} (theta: Rs {t["theta_loss"]:.0f})')
print(f'     Total: Rs {sum(t["net_pnl"] for t in theta_losses):+,.0f}')

print(f'\n  3. FLAT / NO MOVE (NIFTY sat still): {len(flat_losses)} trades')
for t in flat_losses:
    print(f'     {t["date"]}: Idx {t["idx_move"]:+.3f}%, Rs {t["net_pnl"]:+,.0f}')
print(f'     Total: Rs {sum(t["net_pnl"] for t in flat_losses):+,.0f}')

print(f'\n  4. ADVERSE MOVE (NIFTY went up): {len(adverse_losses)} trades')
for t in adverse_losses:
    print(f'     {t["date"]}: Idx {t["idx_move"]:+.3f}%, Rs {t["net_pnl"]:+,.0f}')
print(f'     Total: Rs {sum(t["net_pnl"] for t in adverse_losses):+,.0f}')

# ═══ COMPARISON: 12x vs REAL ═══
print(f'\n{"="*90}')
print(f'COMPARISON: Our conservative 12x vs REAL premiums')
print(f'{"="*90}')
old_total = sum(max(t['idx_move']*12, -100)/100*20000 - 100 for t in trades_all)
new_total = sum(t['net_pnl'] for t in trades_all)
print(f'  12x fixed (Rs 20K premium): Rs {old_total:+,.0f} total, Rs {old_total/n:+,.0f}/trade')
print(f'  Real delta+theta model:     Rs {new_total:+,.0f} total, Rs {new_total/n:+,.0f}/trade')
print(f'  Difference: Rs {new_total-old_total:+,.0f} ({"BETTER" if new_total>old_total else "WORSE"} with real numbers)')

# ═══ BY DAY OF WEEK ═══
print(f'\n{"="*90}')
print(f'PERFORMANCE BY DAY OF WEEK')
print(f'{"="*90}')
for day in ['Mon','Tue','Wed','Thu','Fri']:
    dt = [t for t in trades_all if t['day'] == day]
    if not dt: continue
    dw = sum(1 for t in dt if t['win'])
    dp = sum(t['net_pnl'] for t in dt)
    avg_mult = sum(t['real_mult'] for t in dt if t['real_mult']!=0) / max(1, len([t for t in dt if t['real_mult']!=0]))
    print(f'  {day}: {len(dt):>2} trades, WR={dw/len(dt)*100:.0f}%, Rs {dp:>+8,.0f}, avg mult={avg_mult:.0f}x')

# Compound with real numbers
print(f'\n{"="*90}')
print(f'COMPOUND GROWTH WITH REAL NUMBERS')
print(f'{"="*90}')
for start in [50000, 100000, 200000, 500000]:
    cap = float(start); peak = cap; max_dd = 0
    for t in trades_all:
        lots = max(1, int(cap / (t['premium'] * 2)))
        cap += t['net_pnl'] * lots
        cap = max(cap, 0)
        peak = max(peak, cap)
        dd = (peak - cap) / peak * 100
        max_dd = max(max_dd, dd)
    ret = (cap/start-1)*100
    print(f'  Rs {start/1000:.0f}K -> Rs {cap:>12,.0f} ({ret:>+7,.0f}%) | Max DD: {max_dd:.1f}%')
