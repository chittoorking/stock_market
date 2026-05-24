"""
FUTURES BACKTEST — CAM_R3 SHORT on NIFTY FUTURES
No theta. No delta. 1:1 with index. Lower charges than equity.

NIFTY Futures:
  Lot size: 25 units
  Margin: ~Rs 1.5L per lot (SPAN)
  Charges per lot (Rs 10L notional):
    Brokerage: Rs 20
    STT: 0.0125% sell = Rs 125 (vs Rs 250 equity)
    Exchange: 0.05% both = Rs 500... NO wait,

  Let's compute REAL charges:
    Brokerage: Rs 20 (flat, Zerodha/Upstox)
    STT: 0.0125% on sell side = 0.0125/100 * lot_value
    Exchange txn: 0.00173% both sides = 0.00173/100 * lot_value * 2
    SEBI: Rs 10/crore = negligible
    Stamp duty: 0.002% on buy = 0.002/100 * lot_value
    GST: 18% on (brokerage + exchange)

Also test:
  1. NIFTY futures (single index)
  2. Stock futures (45 stocks) — same signals as equity bot
"""
import sys, io, csv, math
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict
from datetime import datetime as dt

# Load data
data_dir = Path('data/5min')
all_data = {}; date_bars = defaultdict(dict)
print('Loading...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    if sym in ('NIFTY', 'NIFTY_50'): sym = 'NIFTY_50'
    if sym == 'NIFTY': continue
    with open(f) as fh: rows = list(csv.DictReader(fh))
    bars = [{'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
             'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))} for r in rows]
    all_data[sym] = bars
    by_d = defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d, bs in by_d.items(): date_bars[d][sym] = bs

all_dates = sorted(date_bars.keys())

# Pre-compute
prev_day = {}; daily_trend = {}
for sym, bars in all_data.items():
    dfs = sorted(set(b['timestamp'][:10] for b in bars)); dc = []
    for i, d in enumerate(dfs):
        db = date_bars[d].get(sym, [])
        if not db: continue
        c = db[-1]['close']; dc.append(c)
        if i > 0:
            pdb = date_bars[dfs[i-1]].get(sym, [])
            if pdb:
                prev_day[(d,sym)] = {'high':max(b['high'] for b in pdb),
                                     'low':min(b['low'] for b in pdb),
                                     'close':pdb[-1]['close']}
        if len(dc) >= 5:
            up = sum(1 for j in range(1, len(dc[-5:])) if dc[-5:][j] > dc[-5:][j-1])
            daily_trend[(d,sym)] = 'UP' if up >= 4 else 'DOWN' if up <= 1 else 'SIDE'

print(f'{len(all_data)} instruments, {len(all_dates)} days\n')

SCAN_BAR = 10; TARGET = 1.50; STOP = 1.00

def calc_futures_charges(lot_value):
    """Real futures charges for 1 trade (buy + sell)."""
    brokerage = 20  # Flat per order × 2 sides = 40? No, Rs 20 per trade
    stt = lot_value * 0.0125 / 100  # Sell side only
    exchange = lot_value * 0.00173 / 100 * 2  # Both sides
    stamp = lot_value * 0.002 / 100  # Buy side
    sebi = lot_value / 10000000 * 10  # Rs 10 per crore
    gst = (brokerage + exchange) * 0.18
    total = brokerage + stt + exchange + stamp + sebi + gst
    return round(total, 2)

def get_signals(date, symbols=None):
    """Get CAM_R3 SHORT signals."""
    signals = []
    syms = symbols or [s for s in date_bars[date] if s not in ('NIFTY_50','NIFTY_BANK')]
    for sym in syms:
        if sym not in date_bars[date]: continue
        db = date_bars[date][sym]
        if len(db) <= SCAN_BAR + 20: continue
        if daily_trend.get((date,sym)) != 'DOWN': continue
        pd = prev_day.get((date,sym))
        if not pd: continue
        rng = pd['high'] - pd['low']
        if rng <= 0: continue
        r3 = pd['close'] + rng * 1.1 / 4
        for j in range(1, SCAN_BAR + 1):
            atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1)) / min(4,j+1)
            if abs(db[j]['high'] - r3) < atr * 0.3 and db[j]['close'] < r3:
                signals.append({'sym':sym, 'entry':db[SCAN_BAR]['close'], 'date':date, 'r3':round(r3,2)})
                break
    return signals

def simulate_trade(signal, lot_size, margin_per_lot):
    """Simulate a futures trade."""
    date = signal['date']; sym = signal['sym']
    db = date_bars[date][sym]
    entry = signal['entry']
    tp = entry * (1 - TARGET/100)
    sp = entry * (1 + STOP/100)
    ep = db[min(69, len(db)-1)]['close']
    exit_reason = 'eod'

    for k in range(SCAN_BAR+1, min(len(db), 70)):
        if db[k]['low'] <= tp: ep = tp; exit_reason = 'target'; break
        if db[k]['high'] >= sp: ep = sp; exit_reason = 'stop'; break

    lot_value = entry * lot_size
    pnl_points = entry - ep
    pnl_rs = pnl_points * lot_size
    charges = calc_futures_charges(lot_value)
    net_pnl = pnl_rs - charges
    pnl_pct = (entry - ep) / entry * 100

    return {
        'date': date, 'sym': sym, 'entry': entry, 'exit': round(ep,2),
        'pnl_pct': round(pnl_pct, 3), 'pnl_rs': round(pnl_rs, 0),
        'charges': round(charges, 0), 'net_pnl': round(net_pnl, 0),
        'win': net_pnl > 0, 'exit_reason': exit_reason,
        'lot_value': round(lot_value, 0), 'margin': margin_per_lot,
    }

def report(label, trades):
    """Print summary for a set of trades."""
    if not trades:
        print(f'\n  {label}: 0 trades')
        return
    n = len(trades); w = sum(1 for t in trades if t['win'])
    total = sum(t['net_pnl'] for t in trades)
    gross = sum(t['pnl_rs'] for t in trades)
    charges = sum(t['charges'] for t in trades)
    wins = [t for t in trades if t['win']]
    losses = [t for t in trades if not t['win']]
    avg_win = sum(t['net_pnl'] for t in wins)/len(wins) if wins else 0
    avg_loss = sum(t['net_pnl'] for t in losses)/len(losses) if losses else 0

    # Drawdown
    cum = 0; peak = 0; max_dd = 0
    for t in trades:
        cum += t['net_pnl']; peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    print(f'\n  {label}:')
    print(f'    Trades: {n} | Wins: {w} | WR: {w/n*100:.0f}%')
    print(f'    Gross P&L: Rs {gross:+,.0f} | Charges: Rs {charges:,.0f} | Net: Rs {total:+,.0f}')
    print(f'    Per trade: Rs {total/n:+,.0f} | Avg win: Rs {avg_win:+,.0f} | Avg loss: Rs {avg_loss:+,.0f}')
    print(f'    Max drawdown: Rs {max_dd:,.0f}')
    return total

# ═══════════════════════════════════════════════════════════════
# TEST 1: NIFTY FUTURES (index, lot=25, margin ~Rs 1.5L)
# ═══════════════════════════════════════════════════════════════
print('='*90)
print('TEST 1: NIFTY FUTURES — CAM_R3 SHORT on NIFTY index')
print('Lot: 25 units | Margin: ~Rs 1.5L | No theta/delta issues')
print('='*90)

nifty_trades = []
for date in all_dates:
    sigs = get_signals(date, ['NIFTY_50'])
    for s in sigs:
        t = simulate_trade(s, lot_size=25, margin_per_lot=150000)
        nifty_trades.append(t)

report('NIFTY Futures (1 lot)', nifty_trades)

# Show charges breakdown
if nifty_trades:
    sample = nifty_trades[0]
    lv = sample['lot_value']
    print(f'\n    Charges breakdown (lot value Rs {lv:,.0f}):')
    print(f'      Brokerage: Rs 20')
    print(f'      STT (0.0125% sell): Rs {lv*0.0125/100:.0f}')
    print(f'      Exchange (0.00173% × 2): Rs {lv*0.00173/100*2:.0f}')
    print(f'      Stamp (0.002% buy): Rs {lv*0.002/100:.0f}')
    print(f'      GST (18%): Rs {(20 + lv*0.00173/100*2)*0.18:.0f}')
    print(f'      TOTAL: Rs {sample["charges"]:.0f}')

# All trades detail
if nifty_trades:
    print(f'\n    All NIFTY futures trades:')
    cum = 0
    for i, t in enumerate(nifty_trades, 1):
        cum += t['net_pnl']
        wl = 'W' if t['win'] else 'L'
        print(f'      {i:>3}. {t["date"]} NIFTY {t["entry"]:>8.1f} -> {t["exit"]:>8.1f} '
              f'({t["pnl_pct"]:>+6.3f}%) Rs {t["pnl_rs"]:>+7,.0f} - Rs {t["charges"]:>3,.0f} = Rs {t["net_pnl"]:>+7,.0f} '
              f'Cum Rs {cum:>+9,.0f} {t["exit_reason"]:>6} {wl}')

# ═══════════════════════════════════════════════════════════════
# TEST 2: STOCK FUTURES (45 stocks, various lot sizes)
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST 2: STOCK FUTURES — CAM_R3 SHORT on all 45 stocks')
print('Same signals as equity bot but with FUTURES charges')
print('='*90)

# Typical stock futures lot sizes and margins
# Lot sizes are set so 1 lot ≈ Rs 5-10L notional
STOCK_LOTS = {
    'ADANIENT':250, 'ADANIPORTS':500, 'APOLLOHOSP':100, 'ASIANPAINT':200,
    'AXISBANK':400, 'BAJAJ-AUTO':100, 'BPCL':1000, 'BHARTIARTL':450,
    'BRITANNIA':100, 'CIPLA':400, 'COALINDIA':1050, 'DIVISLAB':100,
    'EICHERMOT':125, 'GRASIM':275, 'HCLTECH':350, 'HDFCBANK':400,
    'HDFCLIFE':700, 'HEROMOTOCO':100, 'HINDALCO':850, 'HINDUNILVR':200,
    'ICICIBANK':525, 'ITC':1600, 'INDUSINDBK':500, 'INFY':400,
    'JSWSTEEL':675, 'LT':150, 'M&M':350, 'MARUTI':50,
    'NTPC':1500, 'NESTLEIND':25, 'ONGC':1925, 'POWERGRID':1800,
    'RELIANCE':250, 'SBILIFE':375, 'SBIN':750, 'SUNPHARMA':350,
    'TCS':125, 'TATACONSUM':500, 'TATAMOTORS':550, 'TATASTEEL':1700,
    'TECHM':350, 'TITAN':175, 'UPL':1300, 'ULTRACEMCO':50,
    'WIPRO':1000,
}

# Compare: equity (Rs 10L fixed position) vs futures (1 lot)
equity_trades = []; futures_trades = []

for date in all_dates:
    sigs = get_signals(date)
    for s in sigs:
        sym = s['sym']
        # Equity: Rs 10L position, charges Rs 386
        entry = s['entry']
        db = date_bars[date][sym]
        tp = entry * (1 - TARGET/100); sp = entry * (1 + STOP/100)
        ep = db[min(69, len(db)-1)]['close']; exit_r = 'eod'
        for k in range(SCAN_BAR+1, min(len(db), 70)):
            if db[k]['low'] <= tp: ep = tp; exit_r = 'target'; break
            if db[k]['high'] >= sp: ep = sp; exit_r = 'stop'; break
        pnl_pct = (entry - ep) / entry * 100
        eq_pnl_gross = pnl_pct / 100 * 1000000
        eq_pnl = eq_pnl_gross - 386
        equity_trades.append({'date':date,'sym':sym,'net_pnl':round(eq_pnl,0),'win':eq_pnl>0,
                             'pnl_pct':round(pnl_pct,3),'exit_reason':exit_r,'charges':386,
                             'pnl_rs':round(eq_pnl_gross,0)})

        # Futures: 1 lot
        lot = STOCK_LOTS.get(sym, 500)
        lot_value = entry * lot
        fut_pnl_rs = (entry - ep) * lot
        charges = calc_futures_charges(lot_value)
        net = fut_pnl_rs - charges
        futures_trades.append({'date':date,'sym':sym,'entry':entry,'exit':round(ep,2),
                              'net_pnl':round(net,0),'win':net>0,'pnl_pct':round(pnl_pct,3),
                              'pnl_rs':round(fut_pnl_rs,0),'charges':round(charges,0),
                              'exit_reason':exit_r,'lot':lot,'lot_value':round(lot_value,0)})

report('EQUITY (Rs 10L position, Rs 386 charges)', equity_trades)
report('STOCK FUTURES (1 lot, real charges)', futures_trades)

# ═══════════════════════════════════════════════════════════════
# TEST 3: FUTURES with different position sizes
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST 3: STOCK FUTURES — Different position sizes')
print('What if we trade 2 lots? 3 lots? More lots = more profit but same % charges')
print('='*90)

for n_lots in [1, 2, 3, 5]:
    trades = []
    for date in all_dates:
        sigs = get_signals(date)
        for s in sigs:
            sym = s['sym']; entry = s['entry']
            db = date_bars[date][sym]
            tp = entry * (1 - TARGET/100); sp = entry * (1 + STOP/100)
            ep = db[min(69, len(db)-1)]['close']; exit_r = 'eod'
            for k in range(SCAN_BAR+1, min(len(db), 70)):
                if db[k]['low'] <= tp: ep = tp; exit_r = 'target'; break
                if db[k]['high'] >= sp: ep = sp; exit_r = 'stop'; break

            lot = STOCK_LOTS.get(sym, 500)
            total_lot = lot * n_lots
            lot_value = entry * total_lot
            pnl_rs = (entry - ep) * total_lot
            charges = calc_futures_charges(lot_value)
            # Brokerage is per order, not per lot — so Rs 20 not Rs 20*n_lots
            # But STT/exchange/stamp scale with value
            net = pnl_rs - charges
            margin = lot_value * 0.15  # ~15% SPAN margin
            trades.append({'net_pnl':round(net,0),'win':net>0,'margin':round(margin,0)})

    n = len(trades); w = sum(1 for t in trades if t['win'])
    total = sum(t['net_pnl'] for t in trades)
    avg_margin = sum(t['margin'] for t in trades) / n
    print(f'  {n_lots} lot(s): {n} trades, WR={w/n*100:.0f}%, Rs {total:>+12,.0f} total, Rs {total/n:>+7,.0f}/trade, Avg margin: Rs {avg_margin:,.0f}')

# ═══════════════════════════════════════════════════════════════
# TEST 4: NIFTY vs BANKNIFTY FUTURES
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST 4: NIFTY vs BANKNIFTY FUTURES')
print('='*90)

for idx in ['NIFTY_50', 'NIFTY_BANK']:
    if idx not in all_data: continue
    lot = 25 if idx == 'NIFTY_50' else 15  # BANKNIFTY lot = 15
    margin = 150000 if idx == 'NIFTY_50' else 120000
    trades = []
    for date in all_dates:
        sigs = get_signals(date, [idx])
        for s in sigs:
            t = simulate_trade(s, lot_size=lot, margin_per_lot=margin)
            trades.append(t)
    label = 'NIFTY 50' if idx == 'NIFTY_50' else 'BANK NIFTY'
    report(f'{label} Futures ({lot} units/lot)', trades)

# ═══════════════════════════════════════════════════════════════
# TEST 5: COMPOUND with futures
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST 5: COMPOUND GROWTH — Stock futures')
print('Start with X capital, use 20% per trade as margin')
print('='*90)

for START in [300000, 500000, 1000000, 2500000]:
    available = float(START)
    peak = available; max_dd = 0
    total_trades = 0; total_wins = 0

    for date in all_dates:
        sigs = get_signals(date)
        for s in sigs:
            sym = s['sym']; entry = s['entry']
            lot = STOCK_LOTS.get(sym, 500)
            margin_per_lot = entry * lot * 0.15  # 15% SPAN

            # How many lots can we afford with 20% of available?
            alloc = available * 0.20
            n_lots = max(1, int(alloc / margin_per_lot))
            if margin_per_lot * n_lots > available: continue  # Can't afford

            db = date_bars[date][sym]
            tp = entry * (1 - TARGET/100); sp = entry * (1 + STOP/100)
            ep = db[min(69, len(db)-1)]['close']; exit_r = 'eod'
            for k in range(SCAN_BAR+1, min(len(db), 70)):
                if db[k]['low'] <= tp: ep = tp; exit_r = 'target'; break
                if db[k]['high'] >= sp: ep = sp; exit_r = 'stop'; break

            total_lot = lot * n_lots
            lot_value = entry * total_lot
            pnl_rs = (entry - ep) * total_lot
            charges = calc_futures_charges(lot_value)
            net = pnl_rs - charges

            available += net
            available = max(available, 1000)
            total_trades += 1
            if net > 0: total_wins += 1
            peak = max(peak, available)
            dd = (peak - available) / peak * 100
            max_dd = max(max_dd, dd)

    wr = total_wins/total_trades*100 if total_trades else 0
    ret = (available/START - 1) * 100
    print(f'  Rs {START/100000:.0f}L: {total_trades} trades, WR={wr:.0f}%, '
          f'Rs {START:>10,.0f} -> Rs {available:>12,.0f} ({ret:>+8,.0f}%) | Max DD: {max_dd:.1f}%')

# ═══════════════════════════════════════════════════════════════
# CHARGES COMPARISON
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('CHARGES COMPARISON: Equity vs Futures')
print('='*90)
for price in [500, 1000, 2000, 5000]:
    eq_charges = 386  # Fixed for Rs 10L
    # Futures 1 lot at this price
    for lot in [250, 500, 1000]:
        lv = price * lot
        fc = calc_futures_charges(lv)
        pct = fc / lv * 100
        print(f'  Price Rs {price}, Lot {lot} (value Rs {lv:>8,.0f}): Futures Rs {fc:>6,.0f} ({pct:.3f}%) vs Equity Rs 386 on Rs 10L (0.039%)')
