"""
CRITICAL: Verify live strategy matches backtest strategy.
Run on a historical date and compare results.
"""
import csv
from collections import defaultdict
from pathlib import Path
from live import strategy, config

# Load from CSV (same as backtest)
data_dir = Path('data/5min')
date_bars = defaultdict(dict)
daily_ohlc = defaultdict(dict)

print("Loading CSV data...")
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    if sym in ('NIFTY_50', 'NIFTY_BANK'): continue
    with open(f) as fh: rows = list(csv.DictReader(fh))
    by_d = defaultdict(list)
    for r in rows:
        b = {'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
             'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))}
        by_d[r['timestamp'][:10]].append(b)
    for d, bs in by_d.items():
        date_bars[d][sym] = bs
        daily_ohlc[sym][d] = {
            'open': bs[0]['open'], 'close': bs[-1]['close'],
            'high': max(b['high'] for b in bs), 'low': min(b['low'] for b in bs),
            'range': max(b['high'] for b in bs) - min(b['low'] for b in bs),
        }

# Build trends
prev_day = {}; daily_trend = {}
for sym in daily_ohlc:
    sd = sorted(daily_ohlc[sym].keys()); dc = []
    for i, d in enumerate(sd):
        c = daily_ohlc[sym][d]['close']; dc.append(c)
        if i > 0:
            pdb = date_bars[sd[i-1]].get(sym, [])
            if pdb:
                prev_day[(d, sym)] = {
                    'high': max(b['high'] for b in pdb),
                    'low': min(b['low'] for b in pdb),
                    'close': pdb[-1]['close']
                }
        if len(dc) >= 5:
            up = sum(1 for j in range(1, len(dc[-5:])) if dc[-5:][j] > dc[-5:][j-1])
            daily_trend[(d, sym)] = 'UP' if up >= 4 else 'DOWN' if up <= 1 else 'SIDE'

SB = config.SCAN_BAR
T = config.TARGET
S = config.STOP

# Test on multiple dates that should have signals
test_dates = ['2026-05-19', '2026-05-20', '2026-05-21', '2026-05-22',
              '2026-04-28', '2026-04-29', '2026-04-30',
              '2025-12-10', '2025-11-06', '2025-09-02']

print(f"\nVerifying strategy on {len(test_dates)} dates...")
print(f"Config: T={T} S={S} SB={SB} MaxCD={config.MAX_CONSEC_DOWN} "
      f"MinYdRange={config.MIN_YD_RANGE} MinYdBody={config.MIN_YD_BODY}")
print()

total_backtest = 0
total_live = 0

for date in test_dates:
    if date not in date_bars:
        continue

    # === BACKTEST METHOD (from production bot) ===
    bt_signals = []
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        if len(db) <= SB + 20: continue
        trend = daily_trend.get((date, sym))
        if trend not in ('DOWN', 'UP'): continue
        pd = prev_day.get((date, sym))
        if not pd: continue
        rng = pd['high'] - pd['low']
        if rng <= 0: continue

        if trend == 'DOWN':
            # CD filter
            sd2 = sorted(daily_ohlc[sym].keys())
            di = sd2.index(date) if date in sd2 else -1
            if di < 7: continue
            cd = 0
            for back in range(1, 20):
                if di - back < 1: break
                if daily_ohlc[sym][sd2[di-back]]['close'] < daily_ohlc[sym][sd2[di-back-1]]['close']:
                    cd += 1
                else: break
            if cd > 2: continue
            # Yesterday filter
            prev_d = sd2[di-1]; pc = daily_ohlc[sym][prev_d]
            yr = pc['range'] / pc['close'] * 100 if pc['close'] > 0 else 0
            yb = abs(pc['close'] - pc['open']) / pc['range'] if pc['range'] > 0 else 0.5
            if yr < 2.0 or yb < 0.2: continue
            # R3 touch
            r3 = pd['close'] + rng * 1.1 / 4
            for j in range(1, SB + 1):
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1)) / min(4,j+1)
                if abs(db[j]['high'] - r3) < atr * 0.3 and db[j]['close'] < r3:
                    bt_signals.append(('SHORT', sym, round(db[SB]['close'], 2)))
                    break
        else:
            # S3 touch
            s3 = pd['close'] - rng * 1.1 / 4
            for j in range(1, SB + 1):
                atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1)) / min(4,j+1)
                if abs(db[j]['low'] - s3) < atr * 0.3 and db[j]['close'] > s3:
                    bt_signals.append(('LONG', sym, round(db[SB]['close'], 2)))
                    break

    # === LIVE METHOD (from live/strategy.py) ===
    live_signals = []
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        if len(db) <= SB + 20: continue
        trend = daily_trend.get((date, sym))
        if trend not in ('DOWN', 'UP'): continue
        pd_data = prev_day.get((date, sym))
        if not pd_data: continue

        # Get daily closes for this sym up to this date
        sd2 = sorted(daily_ohlc[sym].keys())
        di = sd2.index(date) if date in sd2 else -1
        if di < 7: continue
        closes = [daily_ohlc[sym][sd2[k]]['close'] for k in range(max(0, di-10), di+1)]

        # Compute prev stats using prev day's bars
        prev_d = sd2[di-1]
        prev_bars_list = date_bars.get(prev_d, {}).get(sym, [])
        ps = strategy.compute_prev_day_stats(prev_bars_list)
        if not ps: continue

        signal = strategy.check_signal(sym, db, ps, trend, closes)
        if signal:
            live_signals.append((signal['direction'], sym, signal['entry']))

    # Compare
    bt_set = set((d, s) for d, s, _ in bt_signals)
    live_set = set((d, s) for d, s, _ in live_signals)

    match = bt_set == live_set
    total_backtest += len(bt_signals)
    total_live += len(live_signals)

    status = "MATCH" if match else "MISMATCH"
    print(f"  {date}: BT={len(bt_signals)} LIVE={len(live_signals)} [{status}]")

    if not match:
        only_bt = bt_set - live_set
        only_live = live_set - bt_set
        if only_bt:
            print(f"    Only in backtest: {only_bt}")
        if only_live:
            print(f"    Only in live: {only_live}")

    for d, s, p in bt_signals:
        marker = " *" if (d, s) not in live_set else ""
        print(f"    BT:   {d} {s:>12} @ {p}{marker}")
    for d, s, p in live_signals:
        marker = " *" if (d, s) not in bt_set else ""
        print(f"    LIVE: {d} {s:>12} @ {p}{marker}")

print(f"\n=== TOTAL ===")
print(f"Backtest signals: {total_backtest}")
print(f"Live signals:     {total_live}")
if total_backtest == total_live:
    print("STRATEGY MATCHES PERFECTLY")
else:
    print(f"DIFFERENCE: {total_live - total_backtest}")
