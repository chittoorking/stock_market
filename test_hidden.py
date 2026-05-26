"""
Test the 'institutional' strategies on our 4-year data.
1. Statistical Arbitrage (pairs trading)
2. Index Arbitrage (NSE price gaps)
3. Dividend Capture
"""
import sys, io, csv, math
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

data_dir=Path('data/5min'); all_data={}; date_bars=defaultdict(dict)
print('Loading...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym=f.stem.split('_')[0].upper()
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
            'volume':sum(b['volume'] for b in bs)}
POS=1000000; CHARGES=386; SB=10
print(f'{len(all_data)} symbols, {len(all_dates)} days\n')

# ═══════════════════════════════════════════════════════════════
# 1. PAIRS TRADING (Statistical Arbitrage)
# Find correlated pairs, trade when they diverge
# ═══════════════════════════════════════════════════════════════
print('='*80)
print('1. PAIRS TRADING — Correlated stocks diverge, bet they converge')
print('='*80)

# Known correlated pairs in NIFTY
pairs = [
    ('HDFCBANK', 'ICICIBANK', 'Banking'),
    ('TCS', 'INFY', 'IT'),
    ('SBIN', 'AXISBANK', 'Banking'),
    ('TATASTEEL', 'JSWSTEEL', 'Steel'),
    ('RELIANCE', 'BPCL', 'Oil'),
    ('HCLTECH', 'TECHM', 'IT'),
    ('SUNPHARMA', 'CIPLA', 'Pharma'),
    ('ADANIENT', 'ADANIPORTS', 'Adani'),
    ('COALINDIA', 'NTPC', 'Power/Mining'),
    ('MARUTI', 'TATAMOTORS', 'Auto'),
    ('TITAN', 'ASIANPAINT', 'Consumer'),
    ('HINDUNILVR', 'BRITANNIA', 'FMCG'),
]

print('\nFor each pair: if A goes up 1%+ and B goes down 1%+ in first hour,')
print('SHORT A + LONG B, bet they converge by EOD.\n')

for sym_a, sym_b, sector in pairs:
    if sym_a not in all_data or sym_b not in all_data: continue
    trades = []
    for date in all_dates:
        if sym_a not in date_bars[date] or sym_b not in date_bars[date]: continue
        da = date_bars[date][sym_a]
        db = date_bars[date][sym_b]
        if len(da) <= SB+20 or len(db) <= SB+20: continue

        # First hour returns
        ret_a = (da[SB]['close'] - da[0]['open']) / da[0]['open'] * 100
        ret_b = (db[SB]['close'] - db[0]['open']) / db[0]['open'] * 100
        spread = ret_a - ret_b

        # If spread > 1.5%: A outperformed B too much, bet on convergence
        if abs(spread) < 1.5: continue

        # SHORT the outperformer, LONG the underperformer
        if spread > 0:
            short_bars = da; long_bars = db
        else:
            short_bars = db; long_bars = da

        short_entry = short_bars[SB]['close']
        long_entry = long_bars[SB]['close']
        short_exit = short_bars[-1]['close']
        long_exit = long_bars[-1]['close']

        short_pnl = (short_entry - short_exit) / short_entry * 100
        long_pnl = (long_exit - long_entry) / long_entry * 100
        total_pnl = (short_pnl + long_pnl) / 2  # Average of both legs
        net = total_pnl / 100 * POS - CHARGES * 2  # 2 legs = 2x charges
        trades.append(net)

    if len(trades) >= 10:
        w = sum(1 for t in trades if t > 0)
        total = sum(trades)
        print(f'  {sym_a:>10}-{sym_b:<10} ({sector:>10}): {len(trades):>4} trades, '
              f'WR={w/len(trades)*100:.0f}%, Rs {total/len(trades):+,.0f}/trade')

# Also test with tighter spread
print('\nWith 2.0% spread threshold:')
for sym_a, sym_b, sector in pairs:
    if sym_a not in all_data or sym_b not in all_data: continue
    trades = []
    for date in all_dates:
        if sym_a not in date_bars[date] or sym_b not in date_bars[date]: continue
        da = date_bars[date][sym_a]
        db = date_bars[date][sym_b]
        if len(da) <= SB+20 or len(db) <= SB+20: continue
        ret_a = (da[SB]['close'] - da[0]['open']) / da[0]['open'] * 100
        ret_b = (db[SB]['close'] - db[0]['open']) / db[0]['open'] * 100
        spread = ret_a - ret_b
        if abs(spread) < 2.0: continue
        if spread > 0: short_bars = da; long_bars = db
        else: short_bars = db; long_bars = da
        se = short_bars[SB]['close']; le = long_bars[SB]['close']
        sx = short_bars[-1]['close']; lx = long_bars[-1]['close']
        sp = (se - sx) / se * 100; lp = (lx - le) / le * 100
        net = (sp + lp) / 2 / 100 * POS - CHARGES * 2
        trades.append(net)
    if len(trades) >= 5:
        w = sum(1 for t in trades if t > 0)
        total = sum(trades)
        marker = ' <<<' if w/len(trades)*100 >= 80 else ''
        print(f'  {sym_a:>10}-{sym_b:<10}: {len(trades):>4} trades, '
              f'WR={w/len(trades)*100:.0f}%, Rs {total/len(trades):+,.0f}/trade{marker}')

# ═══════════════════════════════════════════════════════════════
# 2. INDEX ARBITRAGE — NIFTY 50 vs components
# If NIFTY drops but a stock doesn't, short the stock (it will catch up)
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print('2. INDEX ARBITRAGE — Stock lagging behind NIFTY')
print('='*80)

if 'NIFTY_50' in all_data:
    print('\nIf NIFTY drops 0.5%+ in first hour but stock is flat/up, SHORT stock')
    print('(Stock will catch up to index direction)\n')

    for lag_threshold in [0.5, 0.75, 1.0]:
        trades = []
        for date in all_dates:
            if 'NIFTY_50' not in date_bars[date]: continue
            nifty = date_bars[date]['NIFTY_50']
            if len(nifty) <= SB + 20: continue
            nifty_ret = (nifty[SB]['close'] - nifty[0]['open']) / nifty[0]['open'] * 100

            for sym in date_bars[date]:
                if sym in ('NIFTY_50', 'NIFTY_BANK'): continue
                db = date_bars[date][sym]
                if len(db) <= SB + 20: continue
                stock_ret = (db[SB]['close'] - db[0]['open']) / db[0]['open'] * 100
                lag = stock_ret - nifty_ret

                # NIFTY down, stock flat/up = stock will drop
                if nifty_ret < -lag_threshold and lag > lag_threshold:
                    entry = db[SB]['close']
                    exit_p = db[-1]['close']
                    pnl = (entry - exit_p) / entry * 100 / 100 * POS - CHARGES
                    trades.append(pnl)

                # NIFTY up, stock flat/down = stock will rise
                elif nifty_ret > lag_threshold and lag < -lag_threshold:
                    entry = db[SB]['close']
                    exit_p = db[-1]['close']
                    pnl = (exit_p - entry) / entry * 100 / 100 * POS - CHARGES
                    trades.append(pnl)

        if trades:
            w = sum(1 for t in trades if t > 0)
            total = sum(trades)
            print(f'  Lag>{lag_threshold}%: {len(trades)} trades, WR={w/len(trades)*100:.0f}%, '
                  f'Rs {total/len(trades):+,.0f}/trade, Total Rs {total:+,.0f}')

# ═══════════════════════════════════════════════════════════════
# 3. MEAN REVERSION — Extreme intraday move reversal
# If stock drops 2%+ in first hour, buy (it bounces back)
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print('3. MEAN REVERSION — Extreme first hour move reverses')
print('='*80)

for direction in ['BUY_after_drop', 'SHORT_after_spike']:
    for threshold in [1.5, 2.0, 2.5, 3.0]:
        for target in [0.50, 0.75, 1.00]:
            trades = []
            for date in all_dates:
                for sym in date_bars[date]:
                    if sym in ('NIFTY_50', 'NIFTY_BANK'): continue
                    db = date_bars[date][sym]
                    if len(db) <= SB + 20: continue
                    fh_ret = (db[SB]['close'] - db[0]['open']) / db[0]['open'] * 100

                    if direction == 'BUY_after_drop' and fh_ret > -threshold: continue
                    if direction == 'SHORT_after_spike' and fh_ret < threshold: continue

                    entry = db[SB]['close']
                    if direction == 'BUY_after_drop':
                        tp = entry * (1 + target/100); sp = entry * (1 - 1.0/100)
                        ep = db[-1]['close']
                        for k in range(SB+1, min(len(db), 70)):
                            if db[k]['high'] >= tp: ep = tp; break
                            if db[k]['low'] <= sp: ep = sp; break
                        pnl = (ep - entry) / entry * 100 / 100 * POS - CHARGES
                    else:
                        tp = entry * (1 - target/100); sp = entry * (1 + 1.0/100)
                        ep = db[-1]['close']
                        for k in range(SB+1, min(len(db), 70)):
                            if db[k]['low'] <= tp: ep = tp; break
                            if db[k]['high'] >= sp: ep = sp; break
                        pnl = (entry - ep) / entry * 100 / 100 * POS - CHARGES

                    trades.append(pnl)

            if len(trades) >= 15:
                w = sum(1 for t in trades if t > 0)
                wr = w / len(trades) * 100
                total = sum(trades)
                if wr >= 70:
                    marker = ' <<<' if wr >= 85 else ''
                    print(f'  {direction} >{threshold}% T={target}: {len(trades)} trades, '
                          f'WR={wr:.0f}%, Rs {total/len(trades):+,.0f}/trade{marker}')

# ═══════════════════════════════════════════════════════════════
# 4. OVERNIGHT GAP FILL — Gap from prev close fills within first hour
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print('4. GAP FILL — Overnight gap fills in first hour')
print('='*80)

for gap_min in [1.0, 1.5, 2.0, 2.5, 3.0]:
    for direction in ['gap_up_short', 'gap_down_long']:
        trades = []
        for date in all_dates:
            for sym in date_bars[date]:
                if sym in ('NIFTY_50', 'NIFTY_BANK'): continue
                db = date_bars[date][sym]
                if len(db) <= SB + 20: continue
                pd = None
                sd2 = sorted(daily_ohlc.get(sym, {}).keys())
                di = sd2.index(date) if date in sd2 else -1
                if di < 1: continue
                prev_close = daily_ohlc[sym][sd2[di-1]]['close']
                gap = (db[0]['open'] - prev_close) / prev_close * 100

                if direction == 'gap_up_short' and gap < gap_min: continue
                if direction == 'gap_down_long' and gap > -gap_min: continue

                entry = db[0]['open']  # Enter at open
                if direction == 'gap_up_short':
                    # Target: gap fills (price returns to prev close)
                    tp = prev_close
                    sp = entry * (1 + 1.0/100)
                    ep = db[SB]['close']  # Check at bar 10
                    for k in range(1, SB+1):
                        if db[k]['low'] <= tp: ep = tp; break
                        if db[k]['high'] >= sp: ep = sp; break
                    pnl = (entry - ep) / entry * 100 / 100 * POS - CHARGES
                else:
                    tp = prev_close
                    sp = entry * (1 - 1.0/100)
                    ep = db[SB]['close']
                    for k in range(1, SB+1):
                        if db[k]['high'] >= tp: ep = tp; break
                        if db[k]['low'] <= sp: ep = sp; break
                    pnl = (ep - entry) / entry * 100 / 100 * POS - CHARGES
                trades.append(pnl)

        if len(trades) >= 10:
            w = sum(1 for t in trades if t > 0)
            wr = w / len(trades) * 100
            total = sum(trades)
            marker = ' <<<' if wr >= 80 else ''
            print(f'  {direction} gap>{gap_min}%: {len(trades)} trades, '
                  f'WR={wr:.0f}%, Rs {total/len(trades):+,.0f}/trade{marker}')

# ═══════════════════════════════════════════════════════════════
# 5. SECTOR ROTATION — Strongest sector stock longs
# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print('5. SECTOR MOMENTUM — Yesterday strongest sector, buy leader today')
print('='*80)

sectors = {
    'Banking': ['HDFCBANK','ICICIBANK','SBIN','AXISBANK','INDUSINDBK'],
    'IT': ['TCS','INFY','HCLTECH','TECHM','WIPRO'],
    'Auto': ['MARUTI','TATAMOTORS','BAJAJ-AUTO','HEROMOTOCO','EICHERMOT','M&M'],
    'Metal': ['TATASTEEL','JSWSTEEL','HINDALCO','COALINDIA'],
    'Pharma': ['SUNPHARMA','CIPLA','DIVISLAB','APOLLOHOSP'],
    'Energy': ['RELIANCE','BPCL','ONGC','NTPC','POWERGRID'],
}

for min_sector_ret in [1.0, 1.5, 2.0]:
    trades = []
    for date in all_dates:
        sd2_ref = sorted(daily_ohlc.get('RELIANCE',{}).keys())
        di = sd2_ref.index(date) if date in sd2_ref else -1
        if di < 1: continue
        prev_date = sd2_ref[di-1]

        for sector_name, stocks in sectors.items():
            # Yesterday's sector return
            rets = []
            for s in stocks:
                if prev_date in daily_ohlc.get(s, {}) and sd2_ref[di-2] if di >= 2 else None:
                    prev2 = sd2_ref[di-2]
                    if prev2 in daily_ohlc.get(s, {}):
                        r = (daily_ohlc[s][prev_date]['close'] - daily_ohlc[s][prev2]['close']) / daily_ohlc[s][prev2]['close'] * 100
                        rets.append((s, r))
            if not rets: continue
            avg_ret = sum(r for _, r in rets) / len(rets)

            if avg_ret > min_sector_ret:
                # Buy the leader
                leader = max(rets, key=lambda x: x[1])
                sym = leader[0]
                if sym not in date_bars[date]: continue
                db = date_bars[date][sym]
                if len(db) <= SB + 20: continue
                entry = db[SB]['close']
                tp = entry * (1 + 1.0/100); sp = entry * (1 - 0.75/100)
                ep = db[-1]['close']
                for k in range(SB+1, min(len(db), 70)):
                    if db[k]['high'] >= tp: ep = tp; break
                    if db[k]['low'] <= sp: ep = sp; break
                pnl = (ep - entry) / entry * 100 / 100 * POS - CHARGES
                trades.append(pnl)

    if len(trades) >= 15:
        w = sum(1 for t in trades if t > 0)
        wr = w / len(trades) * 100
        total = sum(trades)
        print(f'  Sector>{min_sector_ret}%: {len(trades)} trades, WR={wr:.0f}%, '
              f'Rs {total/len(trades):+,.0f}/trade')

print(f'\n{"="*80}')
print('SUMMARY')
print('='*80)
