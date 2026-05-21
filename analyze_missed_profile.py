"""
Compare enrichment profiles: MoE's actual winners vs TOP missed winners vs losers.
Find what distinguishes the big missed winners so MoE can learn to pick them.
"""
import sys; sys.path.insert(0, '.')
import csv
from pathlib import Path
from app.core.signal_enrichment import enrich_signal

data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

groups = {
    "MoE_WINNERS": [
        {"date":"2026-04-30","sym":"TATASTEEL","dir":"SHORT","strat":"gdr4","pnl":0.822},
        {"date":"2026-05-06","sym":"INDUSINDBK","dir":"LONG","strat":"gdr4","pnl":0.863},
        {"date":"2026-05-11","sym":"TITAN","dir":"LONG","strat":"aft7","pnl":1.193},
        {"date":"2026-05-14","sym":"HCLTECH","dir":"SHORT","strat":"gdr4","pnl":1.585},
        {"date":"2026-05-13","sym":"CIPLA","dir":"LONG","strat":"aft7","pnl":0.965},
    ],
    "MISSED_WINNERS": [
        {"date":"2026-05-12","sym":"ADANIPORTS","dir":"SHORT","strat":"gdr4","pnl":2.614},
        {"date":"2026-04-30","sym":"HINDALCO","dir":"SHORT","strat":"gdr4","pnl":2.263},
        {"date":"2026-05-07","sym":"HDFCLIFE","dir":"LONG","strat":"gdr4","pnl":2.244},
        {"date":"2026-05-14","sym":"CIPLA","dir":"LONG","strat":"gdr4","pnl":2.093},
        {"date":"2026-05-12","sym":"ONGC","dir":"LONG","strat":"gdr4","pnl":1.823},
    ],
    "MoE_LOSERS": [
        {"date":"2026-05-07","sym":"EICHERMOT","dir":"LONG","strat":"gdr4","pnl":-0.494},
        {"date":"2026-05-07","sym":"TATASTEEL","dir":"LONG","strat":"gdr4","pnl":-0.504},
        {"date":"2026-05-08","sym":"BPCL","dir":"SHORT","strat":"gdr4","pnl":-0.694},
        {"date":"2026-05-11","sym":"BPCL","dir":"SHORT","strat":"gdr4","pnl":-0.997},
        {"date":"2026-05-11","sym":"AXISBANK","dir":"SHORT","strat":"gdr4","pnl":-0.653},
    ],
}

for group_name, trades in groups.items():
    print(f"\n{'='*80}")
    print(f"{group_name}")
    print(f"{'='*80}")

    total_red = 0; total_green = 0; total_yellow = 0
    for t in trades:
        e = enrich_signal(t["sym"], t["dir"], t["strat"], all_data, t["date"], 6)
        red = [f for f in e.flags if f.startswith("RED")]
        green = [f for f in e.flags if f.startswith("GREEN")]
        yellow = [f for f in e.flags if f.startswith("YELLOW")]
        total_red += len(red); total_green += len(green); total_yellow += len(yellow)

        print(f"\n  {t['date']} {t['sym']:>12} {t['dir']:>5} P&L={t['pnl']:+.3f}%")
        print(f"    RED={len(red)} GREEN={len(green)} YELLOW={len(yellow)}")
        print(f"    Volume: {e.volume_health} | Sector: {e.sector_alignment} | Price: {e.price_quality}")
        print(f"    RVOL: {e.rvol:.1f}x | ATR: {e.atr_pct:.2f}%")
        # Show key flags
        for f in green[:3]:
            print(f"    + {f[6:][:80]}")
        for f in red[:3]:
            print(f"    - {f[4:][:80]}")

    n = len(trades)
    print(f"\n  AVG: RED={total_red/n:.1f} GREEN={total_green/n:.1f} YELLOW={total_yellow/n:.1f}")
