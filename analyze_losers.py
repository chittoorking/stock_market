"""
Analyze which RED flags the 8 MoE v1 losers had vs the 13 winners.
Find flags that are present in losers but NOT in winners = safe to hard-veto.
"""
import sys; sys.path.insert(0, '.')
import csv
from pathlib import Path
from collections import Counter

from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.core.signal_enrichment import enrich_signal
from app.signals.base import get_sector

data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

# MoE v1 trades
trades = [
    # Winners
    {"date":"2026-04-30","sym":"TATASTEEL","dir":"SHORT","strat":"gdr4","bar":6,"result":"W","pnl":0.822},
    {"date":"2026-04-30","sym":"MARUTI","dir":"SHORT","strat":"gdr4","bar":6,"result":"W","pnl":0.622},
    {"date":"2026-04-30","sym":"BPCL","dir":"SHORT","strat":"gdr4","bar":6,"result":"W","pnl":0.381},
    {"date":"2026-05-02","sym":"HINDUNILVR","dir":"SHORT","strat":"gdr4","bar":6,"result":"W","pnl":0.531},
    {"date":"2026-05-05","sym":"BPCL","dir":"SHORT","strat":"gdr4","bar":6,"result":"W","pnl":0.567},
    {"date":"2026-05-05","sym":"CIPLA","dir":"LONG","strat":"lnz3","bar":6,"result":"W","pnl":0.457},
    {"date":"2026-05-05","sym":"RELIANCE","dir":"SHORT","strat":"gdr4","bar":6,"result":"W","pnl":0.457},
    {"date":"2026-05-06","sym":"INDUSINDBK","dir":"LONG","strat":"gdr4","bar":6,"result":"W","pnl":0.863},
    {"date":"2026-05-06","sym":"TCS","dir":"SHORT","strat":"aft7","bar":6,"result":"W","pnl":0.755},
    {"date":"2026-05-11","sym":"TITAN","dir":"LONG","strat":"aft7","bar":6,"result":"W","pnl":1.193},
    {"date":"2026-05-12","sym":"TCS","dir":"SHORT","strat":"gdr4","bar":6,"result":"W","pnl":0.655},
    {"date":"2026-05-13","sym":"CIPLA","dir":"LONG","strat":"aft7","bar":6,"result":"W","pnl":0.965},
    {"date":"2026-05-14","sym":"BHARTIARTL","dir":"LONG","strat":"gdr4","bar":6,"result":"W","pnl":0.921},
    {"date":"2026-05-14","sym":"HCLTECH","dir":"SHORT","strat":"gdr4","bar":6,"result":"W","pnl":1.585},
    # Losers
    {"date":"2026-05-05","sym":"RELIANCE","dir":"SHORT","strat":"aft7","bar":6,"result":"L","pnl":-0.275},
    {"date":"2026-05-07","sym":"EICHERMOT","dir":"LONG","strat":"gdr4","bar":6,"result":"L","pnl":-0.494},
    {"date":"2026-05-07","sym":"TATASTEEL","dir":"LONG","strat":"gdr4","bar":6,"result":"L","pnl":-0.504},
    {"date":"2026-05-07","sym":"GRASIM","dir":"LONG","strat":"lnz3","bar":6,"result":"L","pnl":-0.529},
    {"date":"2026-05-08","sym":"BPCL","dir":"SHORT","strat":"gdr4","bar":6,"result":"L","pnl":-0.694},
    {"date":"2026-05-11","sym":"AXISBANK","dir":"SHORT","strat":"gdr4","bar":6,"result":"L","pnl":-0.653},
    {"date":"2026-05-11","sym":"BPCL","dir":"SHORT","strat":"gdr4","bar":6,"result":"L","pnl":-0.997},
    {"date":"2026-05-13","sym":"JSWSTEEL","dir":"LONG","strat":"aft7","bar":6,"result":"L","pnl":-0.392},
]

print("=" * 120)
print("ENRICHMENT FLAG ANALYSIS: Winners vs Losers")
print("=" * 120)

winner_flags = Counter()
loser_flags = Counter()
winner_red_counts = []
loser_red_counts = []

for t in trades:
    e = enrich_signal(t["sym"], t["dir"], t["strat"], all_data, t["date"], t["bar"])
    red_flags = [f for f in e.flags if f.startswith("RED")]
    green_flags = [f for f in e.flags if f.startswith("GREEN")]
    yellow_flags = [f for f in e.flags if f.startswith("YELLOW")]

    # Categorize flags (strip specific numbers to group similar flags)
    def simplify(flag):
        # Remove specific numbers but keep the pattern
        import re
        return re.sub(r'[0-9]+\.[0-9]+', 'X', flag).split('—')[0].strip()

    print(f"\n{'W' if t['result']=='W' else 'L'} {t['date']} {t['sym']:>12} {t['dir']:>5} {t['strat']:>5} P&L={t['pnl']:+.3f}%")
    print(f"  RED({len(red_flags)}) GREEN({len(green_flags)}) YELLOW({len(yellow_flags)})")
    for f in red_flags:
        print(f"    {f[:100]}")

    if t["result"] == "W":
        winner_red_counts.append(len(red_flags))
        for f in red_flags:
            winner_flags[simplify(f)] += 1
    else:
        loser_red_counts.append(len(red_flags))
        for f in red_flags:
            loser_flags[simplify(f)] += 1

print("\n" + "=" * 120)
print("RED FLAG FREQUENCY: Losers vs Winners")
print("=" * 120)
all_flags = set(list(winner_flags.keys()) + list(loser_flags.keys()))
print(f"\n{'Flag':>70} {'Losers':>8} {'Winners':>8} {'Safe to veto?':>15}")
print("-" * 110)
for f in sorted(all_flags, key=lambda x: loser_flags.get(x,0) - winner_flags.get(x,0), reverse=True):
    lc = loser_flags.get(f, 0)
    wc = winner_flags.get(f, 0)
    safe = "YES" if lc > 0 and wc == 0 else "MAYBE" if lc > wc * 2 else ""
    print(f"{f[:70]:>70} {lc:>8} {wc:>8} {safe:>15}")

print(f"\nAvg RED flags per winner: {sum(winner_red_counts)/len(winner_red_counts):.1f}")
print(f"Avg RED flags per loser:  {sum(loser_red_counts)/len(loser_red_counts):.1f}")

# Find flags ONLY in losers
print("\n" + "=" * 80)
print("FLAGS ONLY IN LOSERS (never in winners — safest to hard-veto):")
print("=" * 80)
for f in sorted(loser_flags.keys()):
    if f not in winner_flags:
        print(f"  [{loser_flags[f]}x] {f[:90]}")
