"""
Optimize options strategy:
1. Premium stop loss (exit if option drops X%)
2. Time-based exit (exit by 1PM if no move — save theta)
3. Day-of-week filter (skip bad days)
4. Trailing stop on premium
5. Tighter/wider index stops
"""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict
from datetime import datetime as dt

# Load NIFTY data
data_dir = Path('data/5min')
f = data_dir / 'NIFTY_50_5min.csv'
with open(f) as fh: rows = list(csv.DictReader(fh))
by_d = defaultdict(list)
for r in rows:
    b = {'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
         'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))}
    by_d[r['timestamp'][:10]].append(b)
date_bars = dict(by_d)
dates = sorted(date_bars.keys())

prev_day = {}; daily_trend = {}; dc = []
for i, d in enumerate(dates):
    dc.append(date_bars[d][-1]['close'])
    if i > 0:
        pb = date_bars[dates[i-1]]
        prev_day[d] = {'high':max(b['high'] for b in pb),'low':min(b['low'] for b in pb),'close':pb[-1]['close']}
    if len(dc) >= 5:
        up = sum(1 for j in range(1, len(dc[-5:])) if dc[-5:][j] > dc[-5:][j-1])
        daily_trend[d] = 'UP' if up >= 4 else 'DOWN' if up <= 1 else 'SIDE'

SCAN_BAR = 10
CHARGES = 100

def get_signals():
    """Get all CAM_R3 signal dates with entry prices."""
    signals = []
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
        dow = dt.strptime(date, '%Y-%m-%d').weekday()
        days_to_thu = (3 - dow) % 7
        if days_to_thu >= 4: prem_unit = 300
        elif days_to_thu == 3: prem_unit = 250
        elif days_to_thu == 2: prem_unit = 180
        elif days_to_thu == 1: prem_unit = 120
        else: prem_unit = 80
        premium = prem_unit * 25
        signals.append({'date': date, 'entry': entry, 'premium': premium,
                        'prem_unit': prem_unit, 'dow': dow, 'dte': days_to_thu})
    return signals

def simulate_trade(sig, idx_stop, prem_stop_pct, time_exit_bar, trail_pct):
    """
    Simulate one trade with all optimization parameters.
    idx_stop: index stop loss % (e.g., 0.75)
    prem_stop_pct: premium stop loss % (e.g., 30 means exit if premium drops 30%)
    time_exit_bar: exit at this bar if no target hit (e.g., 40 = ~1:30 PM)
    trail_pct: trailing stop on premium (e.g., 30 means if premium was up 50%, stop at 50-30=20%)
    """
    date = sig['date']
    db = date_bars[date]
    entry = sig['entry']
    premium = sig['premium']
    prem_unit = sig['prem_unit']
    delta = 0.47
    theta_per_bar = prem_unit * 0.07 / 75  # 7% decay over ~75 bars (full day)

    idx_target = entry * (1 - 0.75/100)  # Always 0.75% target on index
    idx_stop_price = entry * (1 + idx_stop/100)

    max_opt_pnl_pct = 0  # Track peak for trailing stop
    end_bar = min(time_exit_bar, 69, len(db)-1)

    for k in range(SCAN_BAR+1, end_bar+1):
        bars_held = k - SCAN_BAR
        # Current index move
        idx_move_pct = (entry - db[k]['close']) / entry * 100
        idx_move_low = (entry - db[k]['low']) / entry * 100  # Best in bar
        idx_move_high = (db[k]['high'] - entry) / entry * 100  # Worst in bar

        # Option P&L estimate (using bar close for simplicity)
        opt_gain_points = (entry - db[k]['close']) * delta
        theta_lost = theta_per_bar * bars_held
        opt_pnl_per_unit = opt_gain_points - theta_lost
        opt_pnl_pct = opt_pnl_per_unit / prem_unit * 100

        # Track peak
        opt_gain_best = (entry - db[k]['low']) * delta
        opt_pnl_best_pct = (opt_gain_best - theta_lost) / prem_unit * 100
        max_opt_pnl_pct = max(max_opt_pnl_pct, opt_pnl_best_pct)

        # Check INDEX target (using low of bar)
        if db[k]['low'] <= idx_target:
            opt_gain_at_target = (entry - idx_target) * delta - theta_lost
            final_pnl = opt_gain_at_target * 25 - CHARGES
            return final_pnl, 'target'

        # Check INDEX stop (using high of bar)
        if db[k]['high'] >= idx_stop_price:
            opt_loss_at_stop = (entry - idx_stop_price) * delta - theta_lost
            final_pnl = opt_loss_at_stop * 25 - CHARGES
            return final_pnl, 'idx_stop'

        # Check PREMIUM stop loss
        if prem_stop_pct > 0 and opt_pnl_pct < -prem_stop_pct:
            final_pnl = -prem_stop_pct / 100 * premium - CHARGES
            return final_pnl, 'prem_stop'

        # Check TRAILING stop on premium
        if trail_pct > 0 and max_opt_pnl_pct > trail_pct:
            trail_level = max_opt_pnl_pct - trail_pct
            if opt_pnl_pct <= trail_level:
                final_pnl = trail_level / 100 * premium - CHARGES
                return final_pnl, 'trail'

    # Time exit or EOD
    bars_held = end_bar - SCAN_BAR
    opt_gain = (entry - db[end_bar]['close']) * delta
    theta_lost = theta_per_bar * bars_held
    final_pnl = (opt_gain - theta_lost) * 25 - CHARGES
    return final_pnl, 'time' if time_exit_bar < 69 else 'eod'


def test_config(signals, idx_stop, prem_stop, time_exit, trail, day_filter=None, label=''):
    """Test a configuration and return results."""
    trades = []
    for sig in signals:
        if day_filter and sig['dow'] not in day_filter:
            continue
        pnl, reason = simulate_trade(sig, idx_stop, prem_stop, time_exit, trail)
        trades.append({'pnl': pnl, 'reason': reason, 'date': sig['date']})

    if len(trades) < 10:
        return None

    n = len(trades)
    w = sum(1 for t in trades if t['pnl'] > 0)
    total = sum(t['pnl'] for t in trades)
    wins = [t['pnl'] for t in trades if t['pnl'] > 0]
    losses = [t['pnl'] for t in trades if t['pnl'] <= 0]
    avg_win = sum(wins)/len(wins) if wins else 0
    avg_loss = sum(losses)/len(losses) if losses else 0

    # Drawdown
    cum = 0; peak = 0; max_dd = 0
    for t in trades:
        cum += t['pnl']
        peak = max(peak, cum)
        dd = peak - cum
        max_dd = max(max_dd, dd)

    return {
        'label': label, 'n': n, 'wins': w, 'wr': w/n*100,
        'total': total, 'per_trade': total/n,
        'avg_win': avg_win, 'avg_loss': avg_loss,
        'max_dd': max_dd,
        'exits': defaultdict(int, {t['reason']: sum(1 for x in trades if x['reason']==t['reason']) for t in trades}),
    }


print('Loading signals...', flush=True)
signals = get_signals()
print(f'Found {len(signals)} signals\n')

# ═══════════════════════════════════════════════════════════════
# TEST 1: BASELINE (no premium stop, no time exit, no trail)
# ═══════════════════════════════════════════════════════════════
print('='*100)
print('TEST 1: BASELINE vs PREMIUM STOP LOSS')
print('What happens if we add a stop loss on the option premium itself?')
print('='*100)

results = []
# Baseline
r = test_config(signals, 0.75, 0, 69, 0, label='No premium stop (baseline)')
results.append(r)

for ps in [15, 20, 25, 30, 40, 50]:
    r = test_config(signals, 0.75, ps, 69, 0, label=f'Premium stop {ps}%')
    results.append(r)

# Also test without index stop at all, only premium stop
for ps in [20, 30, 40]:
    r = test_config(signals, 99, ps, 69, 0, label=f'ONLY prem stop {ps}% (no idx stop)')
    results.append(r)

print(f'\n{"Config":>40} {"N":>5} {"WR":>5} {"Total":>10} {"/trade":>8} {"AvgW":>8} {"AvgL":>8} {"MaxDD":>8}')
print('-'*95)
for r in results:
    if not r: continue
    marker = ' <<<' if r['per_trade'] > 400 else ''
    print(f'{r["label"]:>40} {r["n"]:>5} {r["wr"]:>4.0f}% Rs{r["total"]:>+9,.0f} Rs{r["per_trade"]:>+7,.0f} Rs{r["avg_win"]:>+7,.0f} Rs{r["avg_loss"]:>+7,.0f} Rs{r["max_dd"]:>7,.0f}{marker}')

# ═══════════════════════════════════════════════════════════════
# TEST 2: TIME-BASED EXIT
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*100}')
print('TEST 2: TIME-BASED EXIT — Exit early to save theta')
print('Bar 10 = 10:15 AM entry. Bar 30 = 12:15 PM. Bar 40 = 1:15 PM. Bar 50 = 2:15 PM.')
print('='*100)

results = []
for time_bar in [25, 30, 35, 40, 45, 50, 55, 60, 69]:
    hour = 9 + (time_bar * 5) // 60
    minute = (time_bar * 5) % 60 + 15
    if minute >= 60: hour += 1; minute -= 60
    time_label = f'{hour}:{minute:02d}'
    r = test_config(signals, 0.75, 0, time_bar, 0, label=f'Exit by {time_label} (bar {time_bar})')
    results.append(r)

print(f'\n{"Config":>40} {"N":>5} {"WR":>5} {"Total":>10} {"/trade":>8} {"AvgW":>8} {"AvgL":>8} {"MaxDD":>8}')
print('-'*95)
for r in results:
    if not r: continue
    marker = ' <<<' if r['per_trade'] > 400 else ''
    print(f'{r["label"]:>40} {r["n"]:>5} {r["wr"]:>4.0f}% Rs{r["total"]:>+9,.0f} Rs{r["per_trade"]:>+7,.0f} Rs{r["avg_win"]:>+7,.0f} Rs{r["avg_loss"]:>+7,.0f} Rs{r["max_dd"]:>7,.0f}{marker}')

# ═══════════════════════════════════════════════════════════════
# TEST 3: TRAILING STOP ON PREMIUM
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*100}')
print('TEST 3: TRAILING STOP — Lock in gains when premium goes up')
print('E.g., trail 15% means: if option was +30%, stop triggers at +15%')
print('='*100)

results = []
r = test_config(signals, 0.75, 0, 69, 0, label='No trailing (baseline)')
results.append(r)

for trail in [10, 15, 20, 25, 30]:
    r = test_config(signals, 0.75, 0, 69, trail, label=f'Trail {trail}%')
    results.append(r)

# Trail + premium stop combo
for trail in [15, 20]:
    for ps in [20, 25, 30]:
        r = test_config(signals, 0.75, ps, 69, trail, label=f'Trail {trail}% + PremStop {ps}%')
        results.append(r)

print(f'\n{"Config":>40} {"N":>5} {"WR":>5} {"Total":>10} {"/trade":>8} {"AvgW":>8} {"AvgL":>8} {"MaxDD":>8}')
print('-'*95)
for r in results:
    if not r: continue
    marker = ' <<<' if r['per_trade'] > 400 else ''
    print(f'{r["label"]:>40} {r["n"]:>5} {r["wr"]:>4.0f}% Rs{r["total"]:>+9,.0f} Rs{r["per_trade"]:>+7,.0f} Rs{r["avg_win"]:>+7,.0f} Rs{r["avg_loss"]:>+7,.0f} Rs{r["max_dd"]:>7,.0f}{marker}')

# ═══════════════════════════════════════════════════════════════
# TEST 4: DAY-OF-WEEK FILTER
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*100}')
print('TEST 4: DAY-OF-WEEK FILTER — Skip bad days')
print('='*100)

day_combos = [
    ({0,1,2,3,4}, 'All days (baseline)'),
    ({2,3}, 'Wed+Thu only (highest amplification)'),
    ({1,2,3}, 'Tue+Wed+Thu'),
    ({0,1,2,3}, 'Mon-Thu (skip Fri)'),
    ({1,2}, 'Tue+Wed only'),
    ({2}, 'Wed only (85% WR)'),
    ({3}, 'Thu only (expiry day)'),
    ({0,1,2}, 'Mon+Tue+Wed'),
]

results = []
for day_set, label in day_combos:
    r = test_config(signals, 0.75, 0, 69, 0, day_filter=day_set, label=label)
    results.append(r)

print(f'\n{"Config":>40} {"N":>5} {"WR":>5} {"Total":>10} {"/trade":>8} {"AvgW":>8} {"AvgL":>8} {"MaxDD":>8}')
print('-'*95)
for r in results:
    if not r: continue
    marker = ' <<<' if r['per_trade'] > 400 else ''
    print(f'{r["label"]:>40} {r["n"]:>5} {r["wr"]:>4.0f}% Rs{r["total"]:>+9,.0f} Rs{r["per_trade"]:>+7,.0f} Rs{r["avg_win"]:>+7,.0f} Rs{r["avg_loss"]:>+7,.0f} Rs{r["max_dd"]:>7,.0f}{marker}')

# ═══════════════════════════════════════════════════════════════
# TEST 5: INDEX STOP LOSS SIZE
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*100}')
print('TEST 5: INDEX STOP LOSS — How tight/wide?')
print('='*100)

results = []
for idx_stop in [0.30, 0.40, 0.50, 0.60, 0.75, 1.00, 1.50, 99]:
    label = f'Index stop {idx_stop}%' if idx_stop < 50 else 'No index stop'
    r = test_config(signals, idx_stop, 0, 69, 0, label=label)
    results.append(r)

print(f'\n{"Config":>40} {"N":>5} {"WR":>5} {"Total":>10} {"/trade":>8} {"AvgW":>8} {"AvgL":>8} {"MaxDD":>8}')
print('-'*95)
for r in results:
    if not r: continue
    marker = ' <<<' if r['per_trade'] > 400 else ''
    print(f'{r["label"]:>40} {r["n"]:>5} {r["wr"]:>4.0f}% Rs{r["total"]:>+9,.0f} Rs{r["per_trade"]:>+7,.0f} Rs{r["avg_win"]:>+7,.0f} Rs{r["avg_loss"]:>+7,.0f} Rs{r["max_dd"]:>7,.0f}{marker}')

# ═══════════════════════════════════════════════════════════════
# TEST 6: BEST COMBINATIONS
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*100}')
print('TEST 6: BEST COMBINATIONS — Mix the winners')
print('='*100)

combos = [
    # (idx_stop, prem_stop, time_exit, trail, days, label)
    (0.75, 0, 69, 0, None, 'BASELINE'),
    (0.50, 25, 50, 0, None, 'IdxStop0.5 + PremStop25 + Exit2:15'),
    (0.50, 20, 45, 15, None, 'IdxStop0.5 + PremStop20 + Exit1:45 + Trail15'),
    (0.50, 25, 50, 15, {1,2,3}, 'Tue-Thu + IdxStop0.5 + PremStop25 + Trail15'),
    (0.50, 20, 45, 0, {2,3}, 'Wed+Thu + IdxStop0.5 + PremStop20 + Exit1:45'),
    (0.50, 25, 50, 0, {1,2,3}, 'Tue-Thu + IdxStop0.5 + PremStop25 + Exit2:15'),
    (0.40, 20, 40, 0, {2,3}, 'Wed+Thu + IdxStop0.4 + PremStop20 + Exit1:15'),
    (0.50, 30, 55, 20, {0,1,2,3}, 'Mon-Thu + IdxStop0.5 + PremStop30 + Trail20'),
    (0.50, 25, 50, 15, {2,3}, 'Wed+Thu + IdxStop0.5 + PremStop25 + Trail15'),
    (0.40, 15, 40, 10, {2,3}, 'Wed+Thu + TIGHT (Stop0.4/Prem15/Exit1:15/Trail10)'),
    (0.75, 25, 50, 15, None, 'All days + PremStop25 + Exit2:15 + Trail15'),
    (0.50, 0, 50, 15, {1,2,3}, 'Tue-Thu + IdxStop0.5 + Trail15 + Exit2:15'),
    (0.50, 20, 69, 15, {1,2,3}, 'Tue-Thu + IdxStop0.5 + PremStop20 + Trail15 (no time exit)'),
]

results = []
for idx_s, prem_s, time_e, trail, days, label in combos:
    r = test_config(signals, idx_s, prem_s, time_e, trail, day_filter=days, label=label)
    results.append(r)

# Sort by per-trade profit
results = [r for r in results if r]
results.sort(key=lambda x: x['per_trade'], reverse=True)

print(f'\n{"#":>3} {"Config":>55} {"N":>5} {"WR":>5} {"Total":>10} {"/trade":>8} {"AvgW":>8} {"AvgL":>8} {"MaxDD":>8}')
print('-'*115)
for i, r in enumerate(results, 1):
    marker = ' <<<' if i <= 3 else ''
    print(f'{i:>3} {r["label"]:>55} {r["n"]:>5} {r["wr"]:>4.0f}% Rs{r["total"]:>+9,.0f} Rs{r["per_trade"]:>+7,.0f} Rs{r["avg_win"]:>+7,.0f} Rs{r["avg_loss"]:>+7,.0f} Rs{r["max_dd"]:>7,.0f}{marker}')

# Show exit breakdown for top 3
print(f'\nExit reasons for top 3:')
for i, r in enumerate(results[:3], 1):
    exits = r['exits']
    print(f'  #{i} {r["label"]}:')
    for reason, count in sorted(exits.items()):
        print(f'      {reason}: {count}')
