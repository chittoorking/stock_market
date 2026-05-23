"""
Download remaining stocks that don't have full history.
Checks actual CSV file size (>50K candles = done).
Simple, no progress file, no bugs.
"""
import sys, time, requests, json
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

NIFTY_50 = {
    'ONGC': 'NSE_EQ|INE213A01029', 'POWERGRID': 'NSE_EQ|INE752E01010',
    'RELIANCE': 'NSE_EQ|INE002A01018', 'SBILIFE': 'NSE_EQ|INE123W01016',
    'SBIN': 'NSE_EQ|INE062A01020', 'SUNPHARMA': 'NSE_EQ|INE044A01036',
    'TATACONSUM': 'NSE_EQ|INE192A01025', 'TATAMOTORS': 'NSE_EQ|INE155A01022',
    'TATASTEEL': 'NSE_EQ|INE081A01020', 'TCS': 'NSE_EQ|INE467B01029',
    'TECHM': 'NSE_EQ|INE669C01036', 'TITAN': 'NSE_EQ|INE280A01028',
    'UPL': 'NSE_EQ|INE628A01036', 'ULTRACEMCO': 'NSE_EQ|INE481G01011',
    'WIPRO': 'NSE_EQ|INE075A01022',
}

BASE_URL = "https://api.upstox.com/v2/historical-candle"
OUT_DIR = Path('data/5min')
START = datetime(2022, 1, 3)
END = datetime(2025, 5, 16)

def download_chunk(inst_key, from_d, to_d):
    url = f"{BASE_URL}/{inst_key}/1minute/{to_d}/{from_d}"
    for attempt in range(3):
        try:
            r = requests.get(url, headers={'Accept': 'application/json'}, timeout=15)
            if r.status_code == 200:
                d = r.json()
                if d.get('status') == 'success':
                    return d.get('data', {}).get('candles', [])
            elif r.status_code == 429:
                time.sleep(3 * (attempt + 1))
        except:
            time.sleep(2)
    return []

def agg_5min(candles):
    if not candles: return pd.DataFrame()
    df = pd.DataFrame(candles, columns=['timestamp','open','high','low','close','volume','oi'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp')
    df['ts5'] = df['timestamp'].dt.floor('5min')
    return df.groupby('ts5').agg(
        {'open':'first','high':'max','low':'min','close':'last','volume':'sum','oi':'last'}
    ).reset_index().rename(columns={'ts5':'timestamp'})

# Find which stocks need downloading
need = {}
for sym, key in NIFTY_50.items():
    f = OUT_DIR / f"{sym}_5min.csv"
    if f.exists() and len(pd.read_csv(f)) >= 50000:
        continue
    need[sym] = key

if not need:
    print("All stocks already have full history!")
    sys.exit(0)

print(f"{len(need)} stocks need full history: {list(need.keys())}")
print(f"Range: {START.date()} to {END.date()}")
print("=" * 60, flush=True)

for i, (sym, inst_key) in enumerate(need.items(), 1):
    print(f"\n[{i}/{len(need)}] {sym}...", flush=True)
    all_candles = []
    current = START
    total_chunks = int((END - START).days / 5) + 1
    done_chunks = 0

    while current < END:
        chunk_end = min(current + timedelta(days=5), END)
        candles = download_chunk(inst_key, current.strftime('%Y-%m-%d'), chunk_end.strftime('%Y-%m-%d'))
        if candles:
            all_candles.extend(candles)
        current = chunk_end + timedelta(days=1)
        done_chunks += 1
        time.sleep(0.35)
        if done_chunks % 50 == 0:
            print(f"  {done_chunks}/{total_chunks} chunks, {len(all_candles)} 1min candles", flush=True)

    if not all_candles:
        print(f"  No data"); continue

    df_new = agg_5min(all_candles)
    out_file = OUT_DIR / f"{sym}_5min.csv"

    if out_file.exists():
        existing = pd.read_csv(out_file)
        existing['timestamp'] = pd.to_datetime(existing['timestamp'])
        combined = pd.concat([df_new, existing]).drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
        combined.to_csv(out_file, index=False)
        print(f"  OK: {len(df_new)} new, {len(combined)} total")
    else:
        df_new.to_csv(out_file, index=False)
        print(f"  OK: {len(df_new)} candles")

print("\nDone!")
