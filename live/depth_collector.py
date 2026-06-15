"""Market Depth Collector — record order book snapshots at 9:15 every day.

Builds our own historical depth dataset that we can backtest against.
Run this every trading day at 9:14-9:16 AM.

After 20+ days, we'll have enough data to test:
  Does buy/sell imbalance at 9:15 predict gap fill?
"""
import sys, io, json, time, csv, logging
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from . import indmoney_client as api

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')
log = logging.getLogger('depth')

ALL_STOCKS = list(api.SCRIP_CODES.keys())
DATA_DIR = Path(__file__).parent.parent / 'data'
DEPTH_DIR = DATA_DIR / 'depth'
DEPTH_CSV = DEPTH_DIR / 'depth_history.csv'


def collect_snapshot():
    """Collect market depth + LTP + prev_close for all stocks RIGHT NOW."""
    today = datetime.now().strftime('%Y-%m-%d')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    log.info(f'Collecting depth snapshot at {timestamp}...')

    # Get full quotes (LTP + prev_close + circuit limits)
    quotes = api.get_full_quote(ALL_STOCKS)

    # Get market depth (5-level order book)
    depth = api.get_market_depth(ALL_STOCKS)

    # Combine into records
    records = []
    for sym in ALL_STOCKS:
        q = quotes.get(sym, {})
        d = depth.get(sym, {})

        ltp = q.get('last_price', 0)
        prev_close = q.get('prev_close', 0)
        if ltp <= 0 or prev_close <= 0:
            continue

        gap = (ltp - prev_close) / prev_close * 100

        records.append({
            'date': today,
            'timestamp': timestamp,
            'sym': sym,
            'ltp': ltp,
            'prev_close': prev_close,
            'gap_pct': round(gap, 3),
            'open': q.get('open', 0),
            'high': q.get('high', 0),
            'low': q.get('low', 0),
            'volume': q.get('volume', 0),
            'upper_circuit': q.get('upper_circuit', 0),
            'lower_circuit': q.get('lower_circuit', 0),
            # Depth data
            'total_buy': d.get('total_buy', 0),
            'total_sell': d.get('total_sell', 0),
            'buy_pct': d.get('buy_pct', 50),
            'sell_pct': d.get('sell_pct', 50),
            # Derived
            'imbalance': round(d.get('total_buy', 0) / max(d.get('total_buy', 0) + d.get('total_sell', 0), 1), 3),
        })

    log.info(f'Collected depth for {len(records)} stocks')

    # Save to CSV
    DEPTH_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = DEPTH_CSV.exists()

    with open(DEPTH_CSV, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys() if records else [])
        if not file_exists:
            writer.writeheader()
        writer.writerows(records)

    # Also save daily JSON snapshot
    daily_file = DEPTH_DIR / f'{today}.json'
    daily_file.write_text(json.dumps(records, indent=2))

    log.info(f'Saved to {DEPTH_CSV} and {daily_file}')
    return records


def collect_with_outcome():
    """Collect depth at 9:15, then check outcome at 9:30.
    This builds the dataset: depth → did it fill?
    """
    today = datetime.now().strftime('%Y-%m-%d')

    # Step 1: Collect depth at 9:15
    log.info('Step 1: Collecting depth at 9:15...')
    records = collect_snapshot()

    # Step 2: Wait until 9:30 (15 minutes)
    log.info('Waiting 15 minutes for outcome...')
    time.sleep(15 * 60)

    # Step 3: Get prices at 9:30
    log.info('Step 2: Checking outcomes at 9:30...')
    ltp_930 = api.get_ltp(ALL_STOCKS)

    # Step 4: For each gap stock, did the gap start filling?
    outcome_file = DEPTH_DIR / f'{today}_outcome.json'
    outcomes = []

    for rec in records:
        sym = rec['sym']
        gap = rec['gap_pct']
        if abs(gap) < 0.5:
            continue

        price_930 = ltp_930.get(sym, 0)
        if price_930 <= 0:
            continue

        entry = rec['ltp']  # price at 9:15

        # Did it fill? (price moved toward prev_close)
        if gap > 0:  # gap up, fill = price goes DOWN
            fill_move = (entry - price_930) / entry * 100
        else:  # gap down, fill = price goes UP
            fill_move = (price_930 - entry) / entry * 100

        filled = 1 if fill_move > 0.05 else 0

        outcomes.append({
            **rec,
            'price_930': price_930,
            'fill_move_pct': round(fill_move, 3),
            'filled': filled,
        })

    outcome_file.write_text(json.dumps(outcomes, indent=2))
    log.info(f'Outcomes saved: {len(outcomes)} gap stocks, '
             f'{sum(o["filled"] for o in outcomes)} filled')

    # Print summary
    if outcomes:
        # Split by depth imbalance
        high_buy = [o for o in outcomes if o['imbalance'] > 0.55]
        high_sell = [o for o in outcomes if o['imbalance'] < 0.45]
        neutral = [o for o in outcomes if 0.45 <= o['imbalance'] <= 0.55]

        print(f'\nDEPTH vs OUTCOME ({today}):')
        print(f'  Gap UP stocks shorting (fill = price drops):')
        up_stocks = [o for o in outcomes if o['gap_pct'] > 0]
        for o in up_stocks:
            marker = 'FILL' if o['filled'] else 'CONT'
            print(f'    {o["sym"]:<12s} gap={o["gap_pct"]:+.1f}% '
                  f'buy={o["buy_pct"]:.0f}% sell={o["sell_pct"]:.0f}% '
                  f'imb={o["imbalance"]:.2f} -> {marker} ({o["fill_move_pct"]:+.2f}%)')

        print(f'  Gap DOWN stocks longing (fill = price rises):')
        down_stocks = [o for o in outcomes if o['gap_pct'] < 0]
        for o in down_stocks:
            marker = 'FILL' if o['filled'] else 'CONT'
            print(f'    {o["sym"]:<12s} gap={o["gap_pct"]:+.1f}% '
                  f'buy={o["buy_pct"]:.0f}% sell={o["sell_pct"]:.0f}% '
                  f'imb={o["imbalance"]:.2f} -> {marker} ({o["fill_move_pct"]:+.2f}%)')

    return outcomes


def analyze_history():
    """Analyze collected depth data to find if imbalance predicts fills."""
    if not DEPTH_CSV.exists():
        print('No depth history yet. Run collect_with_outcome() for 20+ days first.')
        return

    # Load all outcome files
    all_outcomes = []
    for f in sorted(DEPTH_DIR.glob('*_outcome.json')):
        try:
            data = json.loads(f.read_text())
            all_outcomes.extend(data)
        except Exception:
            pass

    if len(all_outcomes) < 50:
        print(f'Only {len(all_outcomes)} records. Need 50+ for meaningful analysis.')
        print(f'Keep running collect_with_outcome() daily.')
        return

    print(f'Analyzing {len(all_outcomes)} depth records...')
    print()

    # Split by imbalance level
    import numpy as np

    for gap_dir, label in [('up', 'Gap UP (SHORT)'), ('down', 'Gap DOWN (LONG)')]:
        stocks = [o for o in all_outcomes
                  if (o['gap_pct'] > 0.5 if gap_dir == 'up' else o['gap_pct'] < -0.5)]
        if not stocks:
            continue

        print(f'{label}: {len(stocks)} trades')

        for imb_lo, imb_hi, desc in [
            (0, 0.35, 'Heavy SELL pressure (imb<0.35)'),
            (0.35, 0.45, 'Moderate SELL pressure'),
            (0.45, 0.55, 'Balanced'),
            (0.55, 0.65, 'Moderate BUY pressure'),
            (0.65, 1.0, 'Heavy BUY pressure (imb>0.65)'),
        ]:
            group = [o for o in stocks if imb_lo <= o['imbalance'] < imb_hi]
            if len(group) < 5:
                continue
            fill_rate = sum(o['filled'] for o in group) / len(group) * 100
            avg_move = np.mean([o['fill_move_pct'] for o in group])
            print(f'  {desc:<35s}: {len(group):>3d} trades, {fill_rate:.0f}% filled, avg {avg_move:+.2f}%')

        print()

    print('If heavy SELL pressure on gap-UP stocks → higher fill rate,')
    print('then depth IS predictive. Add it to scoring formula.')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--snapshot', action='store_true', help='Just collect depth snapshot')
    p.add_argument('--outcome', action='store_true', help='Collect depth + wait 15min + check outcome')
    p.add_argument('--analyze', action='store_true', help='Analyze collected history')
    args = p.parse_args()

    if args.snapshot:
        collect_snapshot()
    elif args.outcome:
        collect_with_outcome()
    elif args.analyze:
        analyze_history()
    else:
        print('Usage:')
        print('  python -m live.depth_collector --snapshot   # Just collect now')
        print('  python -m live.depth_collector --outcome    # Collect + check after 15min')
        print('  python -m live.depth_collector --analyze    # Analyze collected data')
