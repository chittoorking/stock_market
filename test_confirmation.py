"""
Quick test: What did bars 7-8 look like for winners vs losers?
If losers show no follow-through at bar 7-8, a confirmation check could filter them.
"""
import sys; sys.path.insert(0, '.')
import csv
from pathlib import Path

data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

# From MoE results — all 21 trades
trades = [
    # Winners (target hits)
    {"date":"2026-04-30","sym":"TATASTEEL","dir":"SHORT","entry":218.90,"bar":6,"result":"W","mfe":0.822},
    {"date":"2026-04-30","sym":"MARUTI","dir":"SHORT","entry":12218.50,"bar":6,"result":"W","mfe":0.622},
    {"date":"2026-05-02","sym":"HINDUNILVR","dir":"SHORT","entry":2387.80,"bar":6,"result":"W","mfe":0.531},
    {"date":"2026-05-05","sym":"RELIANCE","dir":"SHORT","entry":1445.95,"bar":6,"result":"W","mfe":0.457},
    {"date":"2026-05-05","sym":"CIPLA","dir":"LONG","entry":1502.25,"bar":6,"result":"W","mfe":0.457},
    {"date":"2026-05-06","sym":"INDUSINDBK","dir":"LONG","entry":927.30,"bar":6,"result":"W","mfe":1.348},
    {"date":"2026-05-06","sym":"TCS","dir":"SHORT","entry":2436.90,"bar":6,"result":"W","mfe":0.755},
    {"date":"2026-05-11","sym":"TITAN","dir":"LONG","entry":4199.90,"bar":6,"result":"W","mfe":1.193},
    {"date":"2026-05-12","sym":"TCS","dir":"SHORT","entry":2306.10,"bar":6,"result":"W","mfe":0.694},
    {"date":"2026-05-13","sym":"CIPLA","dir":"LONG","entry":1305.90,"bar":6,"result":"W","mfe":2.374},
    {"date":"2026-05-14","sym":"BHARTIARTL","dir":"LONG","entry":1823.20,"bar":6,"result":"W","mfe":0.998},
    {"date":"2026-05-14","sym":"HCLTECH","dir":"SHORT","entry":1122.80,"bar":6,"result":"W","mfe":1.585},
    # Stop-hit winners
    {"date":"2026-04-30","sym":"BPCL","dir":"SHORT","entry":299.35,"bar":6,"result":"W","mfe":1.269},
    {"date":"2026-05-05","sym":"BPCL","dir":"SHORT","entry":298.95,"bar":6,"result":"W","mfe":1.890},
    # Losers (all stop hits)
    {"date":"2026-05-05","sym":"RELIANCE","dir":"SHORT","entry":1452.50,"bar":6,"result":"L","mfe":0.103},
    {"date":"2026-05-07","sym":"EICHERMOT","dir":"LONG","entry":7458.50,"bar":6,"result":"L","mfe":0.154},
    {"date":"2026-05-07","sym":"TATASTEEL","dir":"LONG","entry":218.21,"bar":6,"result":"L","mfe":0.399},
    {"date":"2026-05-07","sym":"GRASIM","dir":"LONG","entry":2952.60,"bar":6,"result":"L","mfe":0.071},
    {"date":"2026-05-08","sym":"BPCL","dir":"SHORT","entry":302.50,"bar":6,"result":"L","mfe":0.099},
    {"date":"2026-05-11","sym":"AXISBANK","dir":"SHORT","entry":1254.60,"bar":6,"result":"L","mfe":0.191},
    {"date":"2026-05-11","sym":"BPCL","dir":"SHORT","entry":295.00,"bar":6,"result":"L","mfe":0.305},
    {"date":"2026-05-13","sym":"JSWSTEEL","dir":"LONG","entry":1276.80,"bar":6,"result":"L","mfe":0.439},
]

print("=" * 120)
print(f"{'Date':>10} {'Sym':>12} {'Dir':>5} {'Result':>6} {'MFE%':>6} | {'Bar7 move':>10} {'Bar8 move':>10} {'Bar7 vol chg':>12} {'Bar7+8 net':>10} | {'Confirm?':>8}")
print("-" * 120)

for t in trades:
    db = [b for b in all_data.get(t["sym"], []) if b['timestamp'][:10] == t["date"]]
    entry_bar = t["bar"]
    entry_price = t["entry"]

    if len(db) <= entry_bar + 2:
        continue

    # Bar 7 (first bar after entry)
    bar7 = db[entry_bar + 1]
    bar7_move = (bar7['close'] - db[entry_bar]['close']) / db[entry_bar]['close'] * 100

    # Bar 8
    bar8 = db[entry_bar + 2]
    bar8_move = (bar8['close'] - bar7['close']) / bar7['close'] * 100

    # Bar 7 volume vs bar 6 volume
    bar6_vol = db[entry_bar]['volume']
    bar7_vol = bar7['volume']
    vol_change = (bar7_vol - bar6_vol) / bar6_vol * 100 if bar6_vol > 0 else 0

    # Net move over bar 7+8 in signal direction
    net_move = (bar8['close'] - db[entry_bar]['close']) / db[entry_bar]['close'] * 100
    if t["dir"] == "SHORT":
        bar7_move = -bar7_move
        bar8_move = -bar8_move
        net_move = -net_move

    # Would confirmation work? (bar 7 moves in signal direction)
    confirm = "YES" if bar7_move > 0 else "NO"

    print(f'{t["date"]:>10} {t["sym"]:>12} {t["dir"]:>5} {t["result"]:>6} {t["mfe"]:>5.3f}% | {bar7_move:>+9.3f}% {bar8_move:>+9.3f}% {vol_change:>+11.0f}% {net_move:>+9.3f}% | {confirm:>8}')

# Summary
print("\n" + "=" * 80)
print("CONFIRMATION BAR ANALYSIS:")
winners_with_confirm = 0
winners_without_confirm = 0
losers_with_confirm = 0
losers_without_confirm = 0

for t in trades:
    db = [b for b in all_data.get(t["sym"], []) if b['timestamp'][:10] == t["date"]]
    entry_bar = t["bar"]
    if len(db) <= entry_bar + 1:
        continue

    bar7 = db[entry_bar + 1]
    bar7_move = (bar7['close'] - db[entry_bar]['close']) / db[entry_bar]['close'] * 100
    if t["dir"] == "SHORT":
        bar7_move = -bar7_move

    confirmed = bar7_move > 0

    if t["result"] == "W" and confirmed:
        winners_with_confirm += 1
    elif t["result"] == "W" and not confirmed:
        winners_without_confirm += 1
    elif t["result"] == "L" and confirmed:
        losers_with_confirm += 1
    else:
        losers_without_confirm += 1

print(f"  Winners confirmed at bar 7:     {winners_with_confirm}")
print(f"  Winners NOT confirmed at bar 7:  {winners_without_confirm}")
print(f"  Losers confirmed at bar 7:       {losers_with_confirm}")
print(f"  Losers NOT confirmed at bar 7:   {losers_without_confirm}")
print()
if winners_with_confirm + losers_with_confirm > 0:
    print(f"  If we ONLY took confirmed trades:")
    total_c = winners_with_confirm + losers_with_confirm
    print(f"    Trades: {total_c} | WR: {winners_with_confirm/total_c:.0%}")
if winners_without_confirm + losers_without_confirm > 0:
    print(f"  Trades we'd SKIP (not confirmed):")
    total_s = winners_without_confirm + losers_without_confirm
    print(f"    Trades: {total_s} | Winners lost: {winners_without_confirm} | Losers avoided: {losers_without_confirm}")

# Also test bar 7+8 combined net move
print("\n" + "=" * 80)
print("TWO-BAR CONFIRMATION (bar 7+8 net move in direction):")
w_c2 = 0; w_nc2 = 0; l_c2 = 0; l_nc2 = 0
for t in trades:
    db = [b for b in all_data.get(t["sym"], []) if b['timestamp'][:10] == t["date"]]
    entry_bar = t["bar"]
    if len(db) <= entry_bar + 2:
        continue
    bar8 = db[entry_bar + 2]
    net = (bar8['close'] - db[entry_bar]['close']) / db[entry_bar]['close'] * 100
    if t["dir"] == "SHORT": net = -net
    confirmed = net > 0
    if t["result"] == "W" and confirmed: w_c2 += 1
    elif t["result"] == "W": w_nc2 += 1
    elif t["result"] == "L" and confirmed: l_c2 += 1
    else: l_nc2 += 1

print(f"  Winners confirmed (2-bar):     {w_c2}")
print(f"  Winners NOT confirmed (2-bar):  {w_nc2}")
print(f"  Losers confirmed (2-bar):       {l_c2}")
print(f"  Losers NOT confirmed (2-bar):   {l_nc2}")
if w_c2 + l_c2 > 0:
    total_c2 = w_c2 + l_c2
    print(f"  If ONLY took 2-bar confirmed: {total_c2} trades, WR: {w_c2/total_c2:.0%}")
    print(f"  Skipped: {w_nc2} winners lost, {l_nc2} losers avoided")
