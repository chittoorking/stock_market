"""
OPTIONS BACKTEST WITH REAL IV (India VIX) + BLACK-SCHOLES PRICING
No more fake 12x or fake Rs 20,000 premium.
Uses actual VIX for each day to compute real option premium and Greeks.
"""
import sys, io, csv, math
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict
from datetime import datetime as dt, timedelta

# ═══ BLACK-SCHOLES FUNCTIONS ═══
def norm_cdf(x):
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5*t + a4)*t) + a3)*t + a2)*t + a1)*t * math.exp(-x*x/2)
    return 0.5 * (1.0 + sign * y)

def bs_put_price(S, K, T, r, sigma):
    """Black-Scholes PUT price. S=spot, K=strike, T=years to expiry, r=risk-free, sigma=IV."""
    if T <= 0: return max(K - S, 0)
    d1 = (math.log(S/K) + (r + sigma**2/2)*T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r*T) * norm_cdf(-d2) - S * norm_cdf(-d1)

def bs_put_delta(S, K, T, r, sigma):
    """Black-Scholes PUT delta."""
    if T <= 0: return -1.0 if S < K else 0.0
    d1 = (math.log(S/K) + (r + sigma**2/2)*T) / (sigma * math.sqrt(T))
    return norm_cdf(d1) - 1

# ═══ LOAD DATA ═══
print('Loading data...', flush=True)
data_dir = Path('data/5min')

# Load NIFTY 5-min
f = data_dir / 'NIFTY_50_5min.csv'
with open(f) as fh: rows = list(csv.DictReader(fh))
by_d = defaultdict(list)
for r in rows:
    b = {'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
         'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))}
    by_d[r['timestamp'][:10]].append(b)
date_bars = dict(by_d)
dates = sorted(date_bars.keys())

# Load VIX daily
vix_data = {}
with open('data/vix_daily.csv') as f:
    for r in csv.DictReader(f):
        vix_data[r['date']] = float(r['close'])

# Compute prev_day and trend
prev_day = {}; daily_trend = {}; dc = []
for i, d in enumerate(dates):
    dc.append(date_bars[d][-1]['close'])
    if i > 0:
        pb = date_bars[dates[i-1]]
        prev_day[d] = {'high':max(b['high'] for b in pb),'low':min(b['low'] for b in pb),'close':pb[-1]['close']}
    if len(dc) >= 5:
        up = sum(1 for j in range(1, len(dc[-5:])) if dc[-5:][j] > dc[-5:][j-1])
        daily_trend[d] = 'UP' if up >= 4 else 'DOWN' if up <= 1 else 'SIDE'

SCAN_BAR = 10; TARGET = 0.75; STOP = 0.75
CHARGES = 100  # Options charges per trade
RISK_FREE = 0.065  # India risk-free rate ~6.5%
LOT_SIZE = 25  # NIFTY lot size

print(f'Loaded {len(dates)} days, {len(vix_data)} VIX days\n')

# ═══ FIND SIGNALS ═══
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

    # Days to Thursday expiry
    days_to_thu = (3 - dow) % 7
    if days_to_thu == 0 and dow == 3:
        days_to_thu = 0  # Thursday itself — expiry day

    # Get VIX for this day
    vix = vix_data.get(date)
    if not vix:
        # Try previous day
        di = dates.index(date)
        for back in range(1, 5):
            if di-back >= 0 and dates[di-back] in vix_data:
                vix = vix_data[dates[di-back]]
                break
    if not vix: vix = 15.0  # Fallback

    signals.append({
        'date': date, 'entry': entry, 'dow': dow,
        'dte': days_to_thu, 'vix': vix, 'r3': round(r3, 2),
    })

print(f'Found {len(signals)} signals\n')

# ═══ SIMULATE WITH BLACK-SCHOLES ═══
print('='*110)
print('BACKTEST WITH REAL BLACK-SCHOLES PRICING (using actual India VIX per day)')
print('='*110)

trades = []
for sig in signals:
    date = sig['date']
    db = date_bars[date]
    entry = sig['entry']
    vix = sig['vix']
    dte = sig['dte']
    dow = sig['dow']

    # ATM strike (round to nearest 50)
    atm_strike = round(entry / 50) * 50

    # Time to expiry in years
    # On entry day: remaining hours ≈ (3:15 PM - 10:15 AM) = 5 hours of this day + full remaining days
    intraday_hours = 5.0  # Hours left in trading day after entry
    total_hours = intraday_hours + dte * 6.25  # 6.25 trading hours per day
    T_entry = total_hours / (252 * 6.25)  # Convert to years (252 trading days × 6.25 hours)

    # IV from VIX (VIX is annualized, for weekly options multiply by ~1.2 for skew)
    iv = vix / 100 * 1.2  # Weekly options have higher IV than monthly

    # Calculate entry premium using Black-Scholes
    entry_premium_per_unit = bs_put_price(entry, atm_strike, T_entry, RISK_FREE, iv)
    entry_premium_total = entry_premium_per_unit * LOT_SIZE
    entry_delta = bs_put_delta(entry, atm_strike, T_entry, RISK_FREE, iv)

    if entry_premium_per_unit < 5:  # Premium too low, skip
        continue

    # Simulate bar by bar
    idx_target = entry * (1 - TARGET/100)
    idx_stop = entry * (1 + STOP/100)

    exit_price = db[min(69, len(db)-1)]['close']
    exit_reason = 'eod'
    exit_bar = min(69, len(db)-1)
    bars_to_exit = exit_bar - SCAN_BAR

    for k in range(SCAN_BAR+1, min(len(db), 70)):
        if db[k]['low'] <= idx_target:
            exit_price = idx_target
            exit_reason = 'target'
            exit_bar = k
            bars_to_exit = k - SCAN_BAR
            break
        if db[k]['high'] >= idx_stop:
            exit_price = idx_stop
            exit_reason = 'stop'
            exit_bar = k
            bars_to_exit = k - SCAN_BAR
            break

    # Calculate exit premium using Black-Scholes
    # Time has passed: each bar = 5 minutes
    hours_passed = bars_to_exit * 5 / 60
    T_exit = max(0.0001, T_entry - hours_passed / (252 * 6.25))

    exit_premium_per_unit = bs_put_price(exit_price, atm_strike, T_exit, RISK_FREE, iv)
    exit_premium_total = exit_premium_per_unit * LOT_SIZE

    # P&L = exit premium - entry premium - charges
    pnl = exit_premium_total - entry_premium_total - CHARGES
    opt_pnl_pct = (exit_premium_total - entry_premium_total) / entry_premium_total * 100 if entry_premium_total > 0 else 0
    idx_move = (entry - exit_price) / entry * 100

    # Actual amplification
    real_mult = opt_pnl_pct / idx_move if idx_move != 0 else 0

    day_names = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
    trades.append({
        'date': date, 'day': day_names[dow], 'dte': dte,
        'entry_idx': entry, 'exit_idx': round(exit_price, 2),
        'idx_move': round(idx_move, 3),
        'vix': round(vix, 1),
        'iv': round(iv*100, 1),
        'entry_prem': round(entry_premium_total, 0),
        'exit_prem': round(exit_premium_total, 0),
        'prem_unit': round(entry_premium_per_unit, 1),
        'delta': round(entry_delta, 3),
        'opt_pct': round(opt_pnl_pct, 1),
        'real_mult': round(real_mult, 1),
        'pnl': round(pnl, 0),
        'win': pnl > 0,
        'exit': exit_reason,
        'T_entry': round(T_entry * 252 * 6.25, 1),  # Hours to expiry
    })

# ═══ RESULTS ═══
n = len(trades)
w = sum(1 for t in trades if t['win'])
total = sum(t['pnl'] for t in trades)
wins = [t for t in trades if t['win']]
losses = [t for t in trades if not t['win']]

print(f'\nTrades: {n} | Wins: {w} | WR: {w/n*100:.0f}%')
print(f'Total P&L: Rs {total:+,.0f}')
print(f'Per trade: Rs {total/n:+,.0f}')
if wins: print(f'Avg win: Rs {sum(t["pnl"] for t in wins)/len(wins):+,.0f}')
if losses: print(f'Avg loss: Rs {sum(t["pnl"] for t in losses)/len(losses):+,.0f}')
print(f'Avg entry premium: Rs {sum(t["entry_prem"] for t in trades)/n:,.0f} per lot')
print(f'Avg delta: {sum(t["delta"] for t in trades)/n:.3f}')
print(f'Avg VIX: {sum(t["vix"] for t in trades)/n:.1f}')
print(f'Avg real amplification: {sum(t["real_mult"] for t in trades if abs(t["real_mult"])<500)/len([t for t in trades if abs(t["real_mult"])<500]):.0f}x')

# ═══ ALL TRADES ═══
print(f'\n{"="*130}')
print(f'{"#":>3} {"Date":>10} {"Day":>3} {"DTE":>3} {"VIX":>5} {"Hrs":>5} {"NIFTY":>8} {"->":>2} {"Exit":>8} {"Idx%":>7} {"Prem":>7} {"->":>2} {"ExPrem":>7} {"Opt%":>7} {"Mult":>5} {"PnL":>9} {"Reason":>7} {"W/L":>3}')
print('-'*130)
cumulative = 0
for i, t in enumerate(trades, 1):
    cumulative += t['pnl']
    wl = 'W' if t['win'] else 'L'
    print(f'{i:>3} {t["date"]:>10} {t["day"]:>3} {t["dte"]:>2}d {t["vix"]:>5.1f} {t["T_entry"]:>5.1f}h {t["entry_idx"]:>8.1f}    {t["exit_idx"]:>8.1f} {t["idx_move"]:>+7.3f} {t["entry_prem"]:>7,.0f}    {t["exit_prem"]:>7,.0f} {t["opt_pct"]:>+7.1f}% {t["real_mult"]:>4.0f}x Rs{t["pnl"]:>+8,.0f} {t["exit"]:>7} {wl}')

# ═══ BY DAY OF WEEK ═══
print(f'\n{"="*80}')
print(f'PERFORMANCE BY DAY')
print(f'{"="*80}')
for day in ['Mon','Tue','Wed','Thu','Fri']:
    dt_trades = [t for t in trades if t['day'] == day]
    if not dt_trades: continue
    dw = sum(1 for t in dt_trades if t['win'])
    dp = sum(t['pnl'] for t in dt_trades)
    avg_prem = sum(t['entry_prem'] for t in dt_trades)/len(dt_trades)
    avg_mult = sum(t['real_mult'] for t in dt_trades if abs(t['real_mult'])<500)
    cnt = len([t for t in dt_trades if abs(t['real_mult'])<500])
    avg_m = avg_mult/cnt if cnt else 0
    print(f'  {day}: {len(dt_trades):>2} trades, WR={dw/len(dt_trades)*100:.0f}%, Rs {dp:>+8,.0f} ({dp/len(dt_trades):>+,.0f}/trade), AvgPrem=Rs {avg_prem:,.0f}, Mult={avg_m:.0f}x')

# ═══ BY VIX LEVEL ═══
print(f'\n{"="*80}')
print(f'PERFORMANCE BY VIX LEVEL')
print(f'{"="*80}')
for vix_lo, vix_hi in [(0,13),(13,16),(16,20),(20,25),(25,40)]:
    vt = [t for t in trades if vix_lo <= t['vix'] < vix_hi]
    if not vt: continue
    vw = sum(1 for t in vt if t['win'])
    vp = sum(t['pnl'] for t in vt)
    print(f'  VIX {vix_lo}-{vix_hi}: {len(vt):>2} trades, WR={vw/len(vt)*100:.0f}%, Rs {vp:>+8,.0f} ({vp/len(vt):>+,.0f}/trade)')

# ═══ MONTHLY ═══
print(f'\n{"="*80}')
print(f'MONTHLY BREAKDOWN')
print(f'{"="*80}')
monthly = defaultdict(lambda: {'trades':0,'wins':0,'pnl':0})
for t in trades:
    m = t['date'][:7]
    monthly[m]['trades'] += 1
    monthly[m]['pnl'] += t['pnl']
    if t['win']: monthly[m]['wins'] += 1

green = 0
for m in sorted(monthly):
    d = monthly[m]
    wr = d['wins']/d['trades']*100
    marker = 'GREEN' if d['pnl'] > 0 else 'RED'
    if d['pnl'] > 0: green += 1
    print(f'  {m}: {d["trades"]:>2} trades, WR={wr:.0f}%, Rs {d["pnl"]:>+8,.0f} [{marker}]')
print(f'\n  Green months: {green}/{len(monthly)} ({green/len(monthly)*100:.0f}%)')

# ═══ COMPOUND ═══
print(f'\n{"="*80}')
print(f'COMPOUND GROWTH (1 lot per signal, scale lots with capital)')
print(f'{"="*80}')
for start in [50000, 100000, 200000, 500000]:
    cap = float(start); peak = cap; max_dd = 0
    for t in trades:
        lots = max(1, int(cap / (t['entry_prem'] * 2)))
        cap += t['pnl'] * lots
        cap = max(cap, 1000)
        peak = max(peak, cap)
        dd = (peak - cap) / peak * 100
        max_dd = max(max_dd, dd)
    ret = (cap/start-1)*100
    print(f'  Rs {start/1000:.0f}K -> Rs {cap:>12,.0f} ({ret:>+8,.0f}%) | Max DD: {max_dd:.1f}%')

# ═══ LOSS ANALYSIS ═══
print(f'\n{"="*80}')
print(f'LOSS CATEGORIES')
print(f'{"="*80}')
stop_l = [t for t in losses if t['exit'] == 'stop']
eod_l = [t for t in losses if t['exit'] == 'eod' and t['idx_move'] > 0]
flat_l = [t for t in losses if t['exit'] == 'eod' and -0.1 <= t['idx_move'] <= 0]
adv_l = [t for t in losses if t['exit'] == 'eod' and t['idx_move'] < -0.1]

print(f'  Stop losses: {len(stop_l)} trades, Rs {sum(t["pnl"] for t in stop_l):+,.0f}')
print(f'  Theta ate gains (right dir, too slow): {len(eod_l)} trades, Rs {sum(t["pnl"] for t in eod_l):+,.0f}')
print(f'  Flat/no move: {len(flat_l)} trades, Rs {sum(t["pnl"] for t in flat_l):+,.0f}')
print(f'  Adverse move: {len(adv_l)} trades, Rs {sum(t["pnl"] for t in adv_l):+,.0f}')
