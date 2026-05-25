"""Debug: why no signals today?"""
import time
from collections import defaultdict
from live import upstox_client as api, strategy, config

print("Checking all 45 stocks...")
print()

short_candidates = 0
long_candidates = 0
no_trend = 0
cd_filtered = 0
yd_filtered = 0
no_touch = 0

for sym in config.INSTRUMENTS:
    bars = api.load_previous_days(sym, 10)
    if not bars:
        print(f"  {sym}: NO DATA")
        time.sleep(0.3)
        continue

    by_date = defaultdict(list)
    for b in bars:
        by_date[b['timestamp'][:10]].append(b)
    dates = sorted(by_date.keys())
    if len(dates) < 2:
        continue

    closes = [by_date[d][-1]['close'] for d in dates]
    trend = strategy.compute_daily_trend(closes)

    ps = strategy.compute_prev_day_stats(by_date[dates[-1]])
    if not ps:
        continue

    if trend == 'SIDE':
        no_trend += 1
        continue

    if trend == 'DOWN':
        cd = strategy.count_consec(closes, 'DOWN')
        rp = ps['range_pct']
        br = ps['body_ratio']
        passed_cd = cd <= config.MAX_CONSEC_DOWN
        passed_yd = rp >= config.MIN_YD_RANGE and br >= config.MIN_YD_BODY

        status = "PASS" if passed_cd and passed_yd else "FILTERED"
        reason = ""
        if not passed_cd:
            reason = f"CD={cd}>2"
            cd_filtered += 1
        elif not passed_yd:
            reason = f"yd_range={rp:.1f}%<2% or body={br:.2f}<0.2"
            yd_filtered += 1
        else:
            short_candidates += 1

        print(f"  {sym:>12}: DOWN cd={cd} yd_range={rp:.1f}% body={br:.2f} -> {status} {reason}")

    elif trend == 'UP':
        long_candidates += 1
        print(f"  {sym:>12}: UP (long candidate)")

    time.sleep(0.3)

# Now check intraday touch for candidates
print(f"\n--- SUMMARY ---")
print(f"  SIDE (no trend): {no_trend}")
print(f"  SHORT candidates (passed filters): {short_candidates}")
print(f"  LONG candidates: {long_candidates}")
print(f"  Filtered by CD>2: {cd_filtered}")
print(f"  Filtered by yesterday: {yd_filtered}")
print(f"\n  Total potential: {short_candidates + long_candidates}")
print(f"  (These still need R3/S3 touch at 10:15 AM to trigger)")

# Check intraday for candidates
print(f"\n--- INTRADAY TOUCH CHECK ---")
from pathlib import Path
token = Path('data/upstox_token.txt').read_text().strip()

for sym in config.INSTRUMENTS:
    bars = api.load_previous_days(sym, 10)
    if not bars: continue
    by_date = defaultdict(list)
    for b in bars:
        by_date[b['timestamp'][:10]].append(b)
    dates = sorted(by_date.keys())
    if len(dates) < 2: continue
    closes = [by_date[d][-1]['close'] for d in dates]
    trend = strategy.compute_daily_trend(closes)
    if trend == 'SIDE': continue

    ps = strategy.compute_prev_day_stats(by_date[dates[-1]])
    if not ps: continue

    # Get today's bars
    inst = config.INSTRUMENTS[sym]
    candles = api.get_intraday_candles(inst, '1minute')
    if not candles: continue
    today_bars = api.aggregate_1min_to_5min(candles)
    if len(today_bars) < config.SCAN_BAR + 1: continue

    signal = strategy.check_signal(sym, today_bars, ps, trend, closes)
    if signal:
        print(f"  SIGNAL: {signal['direction']} {sym} @ {signal['entry']}")
    time.sleep(0.3)
