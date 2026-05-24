"""Download India VIX daily data from Upstox free API (4 years)."""
import sys, io, csv, json, time
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from datetime import datetime, timedelta
from pathlib import Path
import urllib.request

BASE = "https://api.upstox.com/v2/historical-candle"
INSTRUMENT = "NSE_INDEX%7CIndia%20VIX"
OUT = Path('data/vix_daily.csv')

all_candles = []
# Download in 6-month chunks
start = datetime(2022, 1, 1)
end = datetime(2026, 5, 24)
chunk = timedelta(days=180)

d = start
while d < end:
    chunk_end = min(d + chunk, end)
    from_str = d.strftime('%Y-%m-%d')
    to_str = chunk_end.strftime('%Y-%m-%d')
    url = f"{BASE}/{INSTRUMENT}/day/{to_str}/{from_str}"
    print(f"  {from_str} to {to_str}...", end=' ', flush=True)

    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        candles = data.get('data', {}).get('candles', [])
        all_candles.extend(candles)
        print(f'{len(candles)} candles')
    except Exception as e:
        print(f'ERROR: {e}')

    d = chunk_end + timedelta(days=1)
    time.sleep(0.5)

# Deduplicate and sort
seen = set()
unique = []
for c in all_candles:
    date = c[0][:10]
    if date not in seen:
        seen.add(date)
        unique.append(c)
unique.sort(key=lambda x: x[0])

# Write CSV
with open(OUT, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['date', 'open', 'high', 'low', 'close'])
    for c in unique:
        w.writerow([c[0][:10], c[1], c[2], c[3], c[4]])

print(f'\nSaved {len(unique)} days to {OUT}')
print(f'Range: {unique[0][0][:10]} to {unique[-1][0][:10]}')
