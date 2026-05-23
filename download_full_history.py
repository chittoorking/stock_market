"""
Download FULL history from Upstox free API: Jan 2022 → May 2025.
This gives us ~3.5 years of data on top of existing 12 months.
Total: ~4 years = ~1000 trading days for rock-solid backtesting.

Upstox free API: 1-minute candles, no auth, goes back to 2022.
We aggregate to 5-minute and merge with existing data.

This is a LONG download (~2-3 hours for all 45 stocks).
Saves progress per stock so can be restarted.
"""
import os, sys, time, requests
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
PROGRESS_FILE = Path('data/download_progress.json')


def download_chunk(instrument_key, from_date, to_date, retries=3):
    url = f"{BASE_URL}/{instrument_key}/1minute/{to_date}/{from_date}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers={'Accept': 'application/json'}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('status') == 'success':
                    return data.get('data', {}).get('candles', [])
            elif resp.status_code == 429:
                wait = 3 * (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
    return []


def aggregate_to_5min(candles_1min):
    if not candles_1min: return pd.DataFrame()
    df = pd.DataFrame(candles_1min, columns=['timestamp','open','high','low','close','volume','oi'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp')
    df['ts_5min'] = df['timestamp'].dt.floor('5min')
    agg = df.groupby('ts_5min').agg({
        'open':'first','high':'max','low':'min','close':'last','volume':'sum','oi':'last'
    }).reset_index()
    agg.rename(columns={'ts_5min':'timestamp'}, inplace=True)
    return agg


def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return set(json.loads(f.read()))
    return set()

import json
def save_progress(done_symbols):
    with open(PROGRESS_FILE, 'w') as f:
        f.write(json.dumps(list(done_symbols)))


def main():
    out_dir = Path('data/5min')
    out_dir.mkdir(parents=True, exist_ok=True)

    # Full range: Jan 2022 → May 16, 2025 (before our existing data starts)
    start_date = datetime(2022, 1, 3)
    end_date = datetime(2025, 5, 16)

    done = load_progress()
    total = len(NIFTY_50)
    remaining = [s for s in NIFTY_50 if s not in done]

    print(f"Downloading FULL history: {start_date.date()} to {end_date.date()}")
    print(f"Stocks: {total} ({len(done)} done, {len(remaining)} remaining)")
    print(f"Estimated time: ~{len(remaining) * 3:.0f} minutes")
    print("=" * 60, flush=True)

    for i, symbol in enumerate(remaining, 1):
        inst_key = NIFTY_50[symbol]
        print(f"\n[{len(done)+i}/{total}] {symbol}...", flush=True)

        all_candles = []
        chunk_days = 5
        current = start_date
        chunks_done = 0
        total_chunks = int((end_date - start_date).days / chunk_days) + 1

        while current < end_date:
            chunk_end = min(current + timedelta(days=chunk_days), end_date)
            from_str = current.strftime('%Y-%m-%d')
            to_str = chunk_end.strftime('%Y-%m-%d')

            candles = download_chunk(inst_key, from_str, to_str)
            if candles:
                all_candles.extend(candles)

            current = chunk_end + timedelta(days=1)
            chunks_done += 1
            time.sleep(0.35)

            if chunks_done % 50 == 0:
                print(f"  {chunks_done}/{total_chunks} chunks, {len(all_candles)} candles so far...", flush=True)

        if not all_candles:
            print(f"  No data for {symbol}")
            done.add(symbol)
            save_progress(done)
            continue

        # Aggregate to 5min
        df_5min = aggregate_to_5min(all_candles)
        if df_5min.empty:
            done.add(symbol)
            save_progress(done)
            continue

        # Merge with existing
        out_file = out_dir / f"{symbol}_5min.csv"
        if out_file.exists():
            existing = pd.read_csv(out_file)
            existing['timestamp'] = pd.to_datetime(existing['timestamp'])
            combined = pd.concat([df_5min, existing], ignore_index=True)
            combined = combined.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
            combined.to_csv(out_file, index=False)
            total_candles = len(combined)
        else:
            df_5min.to_csv(out_file, index=False)
            total_candles = len(df_5min)

        first = df_5min['timestamp'].iloc[0]
        last = df_5min['timestamp'].iloc[-1]
        print(f"  OK: {len(df_5min)} new + merged = {total_candles} total | {first} to {last}")

        done.add(symbol)
        save_progress(done)

    print("\n" + "=" * 60)
    print("DONE! Full history downloaded.")

    # Summary
    for sym in list(NIFTY_50.keys())[:5]:
        f = out_dir / f"{sym}_5min.csv"
        if f.exists():
            df = pd.read_csv(f)
            print(f"  {sym}: {len(df)} candles, {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")


if __name__ == "__main__":
    main()
