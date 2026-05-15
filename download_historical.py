"""
Bulk Historical Data Downloader for Backtesting.

Downloads 6 months of 5-minute candles for all NIFTY 50 stocks.
Uses the same Upstox API pattern from your working upstox_downloader_v2.py.

Usage:
    python download_historical.py

    Or with custom token:
    python download_historical.py --token YOUR_TOKEN --months 6

Output: data/5min/{SYMBOL}_5min.csv
"""

import os
import sys
import time
import argparse
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# ─── Instrument Keys (from your verified downloader) ───

NIFTY_50 = {
    'ADANIENT': 'NSE_EQ|INE423A01024',
    'ADANIPORTS': 'NSE_EQ|INE742F01042',
    'APOLLOHOSP': 'NSE_EQ|INE437A01024',
    'ASIANPAINT': 'NSE_EQ|INE021A01026',
    'AXISBANK': 'NSE_EQ|INE238A01034',
    'BAJAJ-AUTO': 'NSE_EQ|INE917I01010',
    'BAJFINANCE': 'NSE_EQ|INE296A01024',
    'BAJAJFINSV': 'NSE_EQ|INE918I01018',
    'BPCL': 'NSE_EQ|INE029A01011',
    'BHARTIARTL': 'NSE_EQ|INE397D01024',
    'BRITANNIA': 'NSE_EQ|INE216A01030',
    'CIPLA': 'NSE_EQ|INE059A01026',
    'COALINDIA': 'NSE_EQ|INE522F01014',
    'DIVISLAB': 'NSE_EQ|INE361B01024',
    'DRREDDY': 'NSE_EQ|INE089A01023',
    'EICHERMOT': 'NSE_EQ|INE066A01021',
    'GRASIM': 'NSE_EQ|INE047A01021',
    'HCLTECH': 'NSE_EQ|INE860A01027',
    'HDFCBANK': 'NSE_EQ|INE040A01034',
    'HDFCLIFE': 'NSE_EQ|INE795G01014',
    'HEROMOTOCO': 'NSE_EQ|INE158A01026',
    'HINDALCO': 'NSE_EQ|INE038A01020',
    'HINDUNILVR': 'NSE_EQ|INE030A01027',
    'ICICIBANK': 'NSE_EQ|INE090A01021',
    'ITC': 'NSE_EQ|INE154A01025',
    'INDUSINDBK': 'NSE_EQ|INE095A01012',
    'INFY': 'NSE_EQ|INE009A01021',
    'JSWSTEEL': 'NSE_EQ|INE019A01038',
    'KOTAKBANK': 'NSE_EQ|INE237A01028',
    'LT': 'NSE_EQ|INE018A01030',
    'M&M': 'NSE_EQ|INE101A01026',
    'MARUTI': 'NSE_EQ|INE585B01010',
    'NTPC': 'NSE_EQ|INE733E01010',
    'NESTLEIND': 'NSE_EQ|INE239A01024',
    'ONGC': 'NSE_EQ|INE213A01029',
    'POWERGRID': 'NSE_EQ|INE752E01010',
    'RELIANCE': 'NSE_EQ|INE002A01018',
    'SBILIFE': 'NSE_EQ|INE123W01016',
    'SBIN': 'NSE_EQ|INE062A01020',
    'SUNPHARMA': 'NSE_EQ|INE044A01036',
    'TCS': 'NSE_EQ|INE467B01029',
    'TATACONSUM': 'NSE_EQ|INE192A01025',
    'TATAMOTORS': 'NSE_EQ|INE155A01022',
    'TATASTEEL': 'NSE_EQ|INE081A01020',
    'TECHM': 'NSE_EQ|INE669C01036',
    'TITAN': 'NSE_EQ|INE280A01028',
    'UPL': 'NSE_EQ|INE628A01036',
    'ULTRACEMCO': 'NSE_EQ|INE481G01011',
    'WIPRO': 'NSE_EQ|INE075A01022',
}

INDICES = {
    'NIFTY_50': 'NSE_INDEX|Nifty 50',
    'NIFTY_BANK': 'NSE_INDEX|Nifty Bank',
}

BASE_URL = "https://api.upstox.com/v2"


def download_candles(token: str, instrument_key: str, interval: str, from_date: str, to_date: str) -> pd.DataFrame:
    """Download candle data for a date range. Upstox limits ~30 days per request for intraday."""
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    url = f"{BASE_URL}/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}"

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'success' and data.get('data', {}).get('candles'):
                df = pd.DataFrame(
                    data['data']['candles'],
                    columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'],
                )
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df = df.sort_values('timestamp').reset_index(drop=True)
                return df
        elif resp.status_code == 429:
            print("    Rate limited, waiting 2s...")
            time.sleep(2)
        else:
            print(f"    Error {resp.status_code}: {resp.text[:80]}")
    except Exception as e:
        print(f"    Exception: {e}")

    return pd.DataFrame()


def download_symbol(token: str, symbol: str, instrument_key: str, interval: str, months: int, out_dir: Path) -> bool:
    """Download N months of data for a single symbol, chunked into 7-day requests."""
    all_chunks = []
    end_date = datetime.now()
    start_date = end_date - timedelta(days=months * 30)

    # Upstox allows ~7 days per request for minute-level data
    chunk_days = 7
    current_start = start_date

    while current_start < end_date:
        current_end = min(current_start + timedelta(days=chunk_days), end_date)

        from_str = current_start.strftime('%Y-%m-%d')
        to_str = current_end.strftime('%Y-%m-%d')

        df = download_candles(token, instrument_key, interval, from_str, to_str)
        if not df.empty:
            all_chunks.append(df)

        current_start = current_end + timedelta(days=1)
        time.sleep(0.3)  # Rate limiting

    if not all_chunks:
        return False

    combined = pd.concat(all_chunks, ignore_index=True)
    combined = combined.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)

    out_file = out_dir / f"{symbol}_5min.csv"
    combined.to_csv(out_file, index=False)
    return True


def main():
    parser = argparse.ArgumentParser(description="Download historical data for backtesting")
    parser.add_argument("--token", default=None, help="Upstox access token")
    parser.add_argument("--months", type=int, default=6, help="Months of history (default: 6)")
    parser.add_argument("--interval", default="5minute", help="Candle interval (default: 5minute)")
    parser.add_argument("--output", default="data/5min", help="Output directory")
    args = parser.parse_args()

    # Get token
    token = args.token
    if not token:
        token_file = Path("upstox_token.txt")
        if token_file.exists():
            token = token_file.read_text().strip()
        else:
            # Try the hardcoded one from upstox_downloader_v2.py (may be expired)
            print("No token provided. Use --token YOUR_TOKEN or save to upstox_token.txt")
            sys.exit(1)

    # Create output directory
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_instruments = {**NIFTY_50, **INDICES}
    total = len(all_instruments)

    print(f"Downloading {args.months} months of {args.interval} data")
    print(f"Symbols: {total} | Output: {out_dir}")
    print("=" * 60)

    success = 0
    failed = 0

    for i, (symbol, inst_key) in enumerate(all_instruments.items(), 1):
        # Skip if already downloaded
        out_file = out_dir / f"{symbol}_5min.csv"
        if out_file.exists():
            existing = pd.read_csv(out_file)
            if len(existing) > 1000:  # Already has substantial data
                print(f"[{i}/{total}] {symbol} — already have {len(existing)} candles, skipping")
                success += 1
                continue

        print(f"[{i}/{total}] {symbol}...", end=" ", flush=True)

        ok = download_symbol(token, symbol, inst_key, args.interval, args.months, out_dir)
        if ok:
            df = pd.read_csv(out_file)
            print(f"OK ({len(df)} candles)")
            success += 1
        else:
            print("FAILED")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Done: {success} success, {failed} failed")
    print(f"Data saved to: {out_dir.absolute()}")
    print(f"\nTo run backtest:")
    print(f"  curl -X POST http://localhost:8100/auto/backtest \\")
    print(f'    -H "Content-Type: application/json" \\')
    print(f'    -d \'{{"data_dir": "{out_dir.absolute()}"}}\'')


if __name__ == "__main__":
    main()
