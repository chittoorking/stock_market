#!/usr/bin/env python3
"""Log micro-gap signals in real-time — saves to CSV for backtesting."""
import sys, time, csv
from pathlib import Path
sys.path.insert(0, '.')
from live import indmoney_client as api
from datetime import datetime, date

stocks = list(api.SCRIP_CODES.keys())
print(f'Monitoring {len(stocks)} stocks for micro-gaps...')

# Save to dated CSV
today = date.today().strftime('%Y-%m-%d')
log_dir = Path('data/microgap_logs')
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / f'microgap_{today}.csv'

csv_cols = ['date', 'bar_time', 'sym', 'prev_price', 'cur_price', 'gap_pct', 'direction']

with open(log_file, 'w', newline='') as f:
    csv.DictWriter(f, csv_cols).writeheader()

print(f'Saving to: {log_file}')
print()

prev_prices = {}
bar_num = 0

while True:
    now = datetime.now()
    if now.hour < 9 or (now.hour == 9 and now.minute < 15):
        time.sleep(10)
        continue
    if now.hour >= 15 and now.minute >= 30:
        print('Market closed. Done.')
        break

    mins = now.minute
    next_5 = ((mins // 5) + 1) * 5
    wait_secs = (next_5 - mins) * 60 - now.second
    if 0 < wait_secs < 300:
        time.sleep(wait_secs + 2)

    bar_num += 1
    now = datetime.now()
    bar_time = now.strftime('%H:%M')
    print(f'--- Bar {bar_num} @ {now.strftime("%H:%M:%S")} ---')

    cur_prices = api.get_ltp(stocks)

    if prev_prices:
        gaps = []
        rows = []
        for sym in stocks:
            prev = prev_prices.get(sym, 0)
            cur = cur_prices.get(sym, 0)
            if prev <= 0 or cur <= 0:
                continue
            gap_pct = (cur - prev) / prev * 100
            if abs(gap_pct) >= 0.3:
                direction = 'SHORT' if gap_pct > 0 else 'LONG'
                gaps.append((sym, prev, cur, gap_pct))
                rows.append({'date': today, 'bar_time': bar_time, 'sym': sym,
                             'prev_price': round(prev, 2), 'cur_price': round(cur, 2),
                             'gap_pct': round(gap_pct, 4), 'direction': direction})

        # Save all gaps to CSV
        if rows:
            with open(log_file, 'a', newline='') as f:
                w = csv.DictWriter(f, csv_cols)
                w.writerows(rows)

        if gaps:
            gaps.sort(key=lambda x: -abs(x[3]))
            g05 = sum(1 for _, _, _, g in gaps if abs(g) >= 0.5)
            g03 = len(gaps)
            print(f'  {g03} gaps>=0.3%  {g05} gaps>=0.5%  [saved to CSV]')
            for sym, prev, cur, gap in gaps[:10]:
                d = 'SHORT' if gap > 0 else 'LONG'
                print(f'    {sym:<12} {prev:>10.2f} -> {cur:>10.2f}  {gap:>+6.3f}%  {d}')
        else:
            print(f'  No micro-gaps >= 0.3%')

    prev_prices = cur_prices.copy()
    print()

print(f'Log saved: {log_file}')
