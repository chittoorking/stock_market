"""
Fetch delivery % data for all 45 stocks from NSE via jugaad-data.
Saves to data/delivery/ as CSV per stock.
"""
import sys; sys.path.insert(0, '.')
import csv, time
from pathlib import Path
from datetime import date
from jugaad_data.nse import stock_df

data_dir = Path('data/5min')
delivery_dir = Path('data/delivery')
delivery_dir.mkdir(exist_ok=True)

# Get stock list from 5min data
stocks = sorted(set(f.stem.split('_')[0].upper() for f in data_dir.glob('*.csv')))
print(f"Fetching delivery data for {len(stocks)} stocks...")

from_date = date(2025, 11, 1)  # Before our data starts
to_date = date(2026, 5, 15)    # After our data ends

for i, sym in enumerate(stocks):
    out_file = delivery_dir / f"{sym}_delivery.csv"
    if out_file.exists():
        print(f"  [{i+1}/{len(stocks)}] {sym}: already exists, skipping")
        continue

    try:
        df = stock_df(symbol=sym, from_date=from_date, to_date=to_date, series='EQ')
        # Save relevant columns
        df = df[['DATE', 'CLOSE', 'VOLUME', 'DELIVERY QTY', 'DELIVERY %', 'VWAP', 'NO OF TRADES']]
        df.columns = ['date', 'close', 'volume', 'delivery_qty', 'delivery_pct', 'vwap', 'num_trades']
        df['date'] = df['date'].dt.strftime('%Y-%m-%d')
        df = df.sort_values('date')
        df.to_csv(out_file, index=False)
        print(f"  [{i+1}/{len(stocks)}] {sym}: {len(df)} days fetched, delivery% range: {df['delivery_pct'].min():.1f}%-{df['delivery_pct'].max():.1f}%")
    except Exception as e:
        print(f"  [{i+1}/{len(stocks)}] {sym}: FAILED - {e}")

    time.sleep(1)  # Rate limit

print("\nDone. Files saved to data/delivery/")
