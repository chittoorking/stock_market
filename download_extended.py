"""
Download 12+ months of 1-minute data from Upstox (FREE, no auth needed).
Aggregate to 5-minute candles. Extend our 6-month dataset to 12+ months.

Upstox free API supports: 1minute, 30minute, day, week, month
We'll use 1minute and aggregate to 5minute ourselves.
"""
import os, sys, time, json, requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

NIFTY_50 = {
    'ADANIENT': 'NSE_EQ|INE423A01024', 'ADANIPORTS': 'NSE_EQ|INE742F01042',
    'APOLLOHOSP': 'NSE_EQ|INE437A01024', 'ASIANPAINT': 'NSE_EQ|INE021A01026',
    'AXISBANK': 'NSE_EQ|INE238A01034', 'BAJAJ-AUTO': 'NSE_EQ|INE917I01010',
    'BPCL': 'NSE_EQ|INE029A01011', 'BHARTIARTL': 'NSE_EQ|INE397D01024',
    'BRITANNIA': 'NSE_EQ|INE216A01030', 'CIPLA': 'NSE_EQ|INE059A01026',
    'COALINDIA': 'NSE_EQ|INE522F01014', 'DIVISLAB': 'NSE_EQ|INE361B01024',
    'EICHERMOT': 'NSE_EQ|INE066A01021', 'GRASIM': 'NSE_EQ|INE047A01021',
    'HCLTECH': 'NSE_EQ|INE860A01027', 'HDFCBANK': 'NSE_EQ|INE040A01034',
    'HDFCLIFE': 'NSE_EQ|INE795G01014', 'HEROMOTOCO': 'NSE_EQ|INE158A01026',
    'HINDALCO': 'NSE_EQ|INE038A01020', 'HINDUNILVR': 'NSE_EQ|INE030A01027',
    'ICICIBANK': 'NSE_EQ|INE090A01021', 'ITC': 'NSE_EQ|INE154A01025',
    'INDUSINDBK': 'NSE_EQ|INE095A01012', 'INFY': 'NSE_EQ|INE009A01021',
    'JSWSTEEL': 'NSE_EQ|INE019A01038', 'LT': 'NSE_EQ|INE018A01030',
    'M&M': 'NSE_EQ|INE101A01026', 'MARUTI': 'NSE_EQ|INE585B01010',
    'NTPC': 'NSE_EQ|INE733E01010', 'NESTLEIND': 'NSE_EQ|INE239A01024',
    'ONGC': 'NSE_EQ|INE213A01029', 'POWERGRID': 'NSE_EQ|INE752E01010',
    'RELIANCE': 'NSE_EQ|INE002A01018', 'SBILIFE': 'NSE_EQ|INE123W01016',
    'SBIN': 'NSE_EQ|INE062A01020', 'SUNPHARMA': 'NSE_EQ|INE044A01036',
    'TCS': 'NSE_EQ|INE467B01029', 'TATACONSUM': 'NSE_EQ|INE192A01025',
    'TATAMOTORS': 'NSE_EQ|INE155A01022', 'TATASTEEL': 'NSE_EQ|INE081A01020',
    'TECHM': 'NSE_EQ|INE669C01036', 'TITAN': 'NSE_EQ|INE280A01028',
    'UPL': 'NSE_EQ|INE628A01036', 'ULTRACEMCO': 'NSE_EQ|INE481G01011',
    'WIPRO': 'NSE_EQ|INE075A01022',
}

BASE_URL = "https://api.upstox.com/v2/historical-candle"


def download_chunk(instrument_key, from_date, to_date):
    """Download 1-min candles for a date range (max ~7 days)"""
    url = f"{BASE_URL}/{instrument_key}/1minute/{to_date}/{from_date}"
    try:
        resp = requests.get(url, headers={'Accept': 'application/json'}, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'success' and data.get('data', {}).get('candles'):
                return data['data']['candles']
        elif resp.status_code == 429:
            print("  Rate limited, waiting 3s...", flush=True)
            time.sleep(3)
            return download_chunk(instrument_key, from_date, to_date)  # Retry
        else:
            pass  # Silent fail for non-trading days
    except Exception as e:
        print(f"  Error: {e}", flush=True)
    return []


def aggregate_to_5min(candles_1min):
    """Aggregate 1-minute candles to 5-minute"""
    if not candles_1min:
        return pd.DataFrame()

    df = pd.DataFrame(candles_1min, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)

    # Group by 5-minute intervals
    df['ts_5min'] = df['timestamp'].dt.floor('5min')

    agg = df.groupby('ts_5min').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'oi': 'last',
    }).reset_index()
    agg.rename(columns={'ts_5min': 'timestamp'}, inplace=True)
    return agg


def download_symbol(symbol, instrument_key, start_date, end_date, out_dir):
    """Download full range for one symbol"""
    all_candles = []
    chunk_days = 5  # 5 days per request to stay safe
    current = start_date

    while current < end_date:
        chunk_end = min(current + timedelta(days=chunk_days), end_date)
        from_str = current.strftime('%Y-%m-%d')
        to_str = chunk_end.strftime('%Y-%m-%d')

        candles = download_chunk(instrument_key, from_str, to_str)
        if candles:
            all_candles.extend(candles)

        current = chunk_end + timedelta(days=1)
        time.sleep(0.35)  # Rate limit: ~3 req/sec

    if not all_candles:
        return 0

    # Aggregate to 5min
    df_5min = aggregate_to_5min(all_candles)
    if df_5min.empty:
        return 0

    # Save
    out_file = out_dir / f"{symbol}_5min.csv"

    # If existing file, merge
    if out_file.exists():
        existing = pd.read_csv(out_file)
        existing['timestamp'] = pd.to_datetime(existing['timestamp'])
        combined = pd.concat([existing, df_5min], ignore_index=True)
        combined = combined.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
        combined.to_csv(out_file, index=False)
        return len(combined)
    else:
        df_5min.to_csv(out_file, index=False)
        return len(df_5min)


def main():
    out_dir = Path('data/5min')
    out_dir.mkdir(parents=True, exist_ok=True)

    # Current data: Nov 17, 2025 → May 14, 2026
    # Download: May 17, 2025 → Nov 16, 2025 (6 months earlier)
    # This gives us 12 months total
    start_date = datetime(2025, 5, 17)
    end_date = datetime(2025, 11, 16)

    total = len(NIFTY_50)
    print(f"Downloading 6 months of 1-min data: {start_date.date()} to {end_date.date()}")
    print(f"Will aggregate to 5-min candles and merge with existing data")
    print(f"Stocks: {total}")
    print("=" * 60, flush=True)

    for i, (symbol, inst_key) in enumerate(NIFTY_50.items(), 1):
        print(f"[{i}/{total}] {symbol}...", end=" ", flush=True)
        count = download_symbol(symbol, inst_key, start_date, end_date, out_dir)
        if count > 0:
            print(f"OK ({count} total 5min candles)")
        else:
            print("no data")

    # Also download May 15 → May 23 2026 (recent data we might be missing)
    print(f"\nAlso downloading recent data: 2026-05-15 to 2026-05-23...")
    start2 = datetime(2026, 5, 15)
    end2 = datetime(2026, 5, 23)
    for i, (symbol, inst_key) in enumerate(NIFTY_50.items(), 1):
        print(f"[{i}/{total}] {symbol}...", end=" ", flush=True)
        count = download_symbol(symbol, inst_key, start2, end2, out_dir)
        print(f"{count} candles" if count else "no data")

    print("\n" + "=" * 60)
    print("Done! Check data/5min/ for merged files.")

    # Verify
    for sym in list(NIFTY_50.keys())[:3]:
        f = out_dir / f"{sym}_5min.csv"
        if f.exists():
            df = pd.read_csv(f)
            print(f"  {sym}: {len(df)} candles, {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")


if __name__ == "__main__":
    main()
