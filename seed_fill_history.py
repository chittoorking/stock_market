"""Seed fill_history.json from NSE bhavcopy data.
Simulates gap fill outcomes for last 200 trading days to bootstrap rolling WR.
Run once: python seed_fill_history.py"""
import sys, requests, csv, io, json, time
sys.path.insert(0, ".")
from live import indmoney_client as api
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path("data")
FILL_HISTORY_FILE = DATA_DIR / "fill_history.json"
GAP_HISTORY_FILE = DATA_DIR / "gap_history.json"

nse_h = {"User-Agent": "Mozilla/5.0"}
n500 = set(api.SCRIP_CODES.keys())

# Generate last 200 trading days
dates = []
d = datetime(2026, 6, 26)
while len(dates) < 200:
    if d.weekday() < 5:
        dates.append(d)
    d -= timedelta(days=1)
dates.reverse()

print(f"Fetching {len(dates)} bhavcopies...")
bhav = {}
for dt in dates:
    dt_str = dt.strftime("%d%m%Y")
    url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{dt_str}.csv"
    try:
        r = requests.get(url, headers=nse_h, timeout=30)
        if r.status_code == 200 and len(r.text) > 1000:
            data = {}
            for row in csv.DictReader(io.StringIO(r.text)):
                sym = row.get("SYMBOL", "").strip()
                series = row.get(" SERIES", "").strip()
                if series == "EQ" and sym in n500:
                    try:
                        data[sym] = {
                            "o": float(row.get(" OPEN_PRICE", "0").strip()),
                            "c": float(row.get(" CLOSE_PRICE", "0").strip()),
                            "h": float(row.get(" HIGH_PRICE", "0").strip()),
                            "l": float(row.get(" LOW_PRICE", "0").strip()),
                        }
                    except:
                        pass
            bhav[dt.strftime("%Y-%m-%d")] = data
            if len(bhav) % 20 == 0:
                print(f"  {len(bhav)} days loaded...")
        # Skip holidays silently
    except:
        pass
    time.sleep(0.1)

date_list = sorted(bhav.keys())
print(f"Loaded {len(bhav)} trading days\n")

# Simulate gap fill outcomes using daily OHLCV
# Simple: did price retrace to prev_close during the day?
fill_history = {}  # sym -> [[date, pnl_pct], ...]
gap_history = {}   # sym -> [gap_abs, ...]

SL_ATR_MULT = 0.20
total_trades = 0
total_wins = 0

for i in range(1, len(date_list)):
    dt = date_list[i]
    prev_dt = date_list[i - 1]
    today = bhav[dt]
    prev = bhav[prev_dt]

    for sym in n500:
        if sym not in today or sym not in prev:
            continue
        pc = prev[sym]["c"]
        if pc <= 0:
            continue
        gap = (today[sym]["o"] - pc) / pc * 100
        gap_abs = abs(gap)

        # Track gap history for all gaps >= 0.5%
        if gap_abs >= 0.5:
            if sym not in gap_history:
                gap_history[sym] = []
            gap_history[sym].append(round(gap_abs, 4))
            gap_history[sym] = gap_history[sym][-10:]

        if gap_abs < 1.0:
            continue

        # ATR from prev 10 days
        ranges = []
        for j in range(max(0, i - 10), i):
            pk = date_list[j]
            if pk in bhav and sym in bhav[pk]:
                b = bhav[pk][sym]
                ranges.append(b["h"] - b["l"])
        atr = sum(ranges) / len(ranges) if ranges else pc * 0.02

        entry = today[sym]["o"]
        target = pc
        sl_abs = atr * SL_ATR_MULT
        h, l, c = today[sym]["h"], today[sym]["l"], today[sym]["c"]

        if gap > 0:  # SHORT
            sl = entry + sl_abs
            if h >= sl and l > target:
                pnl_pct = -(sl - entry) / entry * 100  # SL hit
            elif l <= target:
                pnl_pct = (entry - target) / entry * 100  # FILL
            else:
                pnl_pct = (entry - c) / entry * 100  # EOD
        else:  # LONG
            sl = entry - sl_abs
            if l <= sl and h < target:
                pnl_pct = -(entry - sl) / entry * 100  # SL hit
            elif h >= target:
                pnl_pct = (target - entry) / entry * 100  # FILL
            else:
                pnl_pct = (c - entry) / entry * 100  # EOD

        if sym not in fill_history:
            fill_history[sym] = []
        fill_history[sym].append([dt, round(pnl_pct, 4)])

        total_trades += 1
        if pnl_pct > 0:
            total_wins += 1

# Keep only last 50 entries per stock (match live code)
for sym in fill_history:
    fill_history[sym] = fill_history[sym][-50:]

# Stats
stocks_with_history = sum(1 for h in fill_history.values() if len(h) >= 5)
avg_entries = sum(len(h) for h in fill_history.values()) / len(fill_history) if fill_history else 0
overall_wr = total_wins / total_trades * 100 if total_trades else 0

print(f"Results:")
print(f"  Total trades simulated: {total_trades}")
print(f"  Overall WR: {overall_wr:.1f}%")
print(f"  Stocks with fill history: {len(fill_history)}")
print(f"  Stocks with >= 5 entries: {stocks_with_history}")
print(f"  Avg entries per stock: {avg_entries:.1f}")
print(f"  Gap history stocks: {len(gap_history)}")

# Show top/bottom WR stocks
wr_list = []
for sym, hist in fill_history.items():
    if len(hist) >= 5:
        w = sum(1 for _, p in hist if p > 0)
        wr_list.append((sym, w / len(hist), len(hist)))
wr_list.sort(key=lambda x: -x[1])

print(f"\nTop 10 WR stocks (>= 5 trades):")
for sym, wr, n in wr_list[:10]:
    print(f"  {sym:15s} WR={wr:.0%} ({n} trades)")

print(f"\nBottom 10 WR stocks:")
for sym, wr, n in wr_list[-10:]:
    print(f"  {sym:15s} WR={wr:.0%} ({n} trades)")

# Save
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILL_HISTORY_FILE.write_text(json.dumps(fill_history, indent=2))
GAP_HISTORY_FILE.write_text(json.dumps(gap_history, indent=2))
print(f"\nSaved to {FILL_HISTORY_FILE} and {GAP_HISTORY_FILE}")
