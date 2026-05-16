"""Test setup development tracker on known winners vs losers."""
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

from app.core.setup_development import track_setup_development

trades = [
    # LOSERS
    {'sym':'JSWSTEEL','date':'2026-04-30','dir':'SHORT','bar':6,'pnl':-0.334,'result':'L'},
    {'sym':'MARUTI','date':'2026-05-04','dir':'LONG','bar':6,'pnl':-0.542,'result':'L'},
    {'sym':'HDFCBANK','date':'2026-05-05','dir':'SHORT','bar':6,'pnl':-0.483,'result':'L'},
    {'sym':'HEROMOTOCO','date':'2026-05-05','dir':'SHORT','bar':6,'pnl':-0.403,'result':'L'},
    {'sym':'EICHERMOT','date':'2026-05-07','dir':'LONG','bar':6,'pnl':-0.494,'result':'L'},
    {'sym':'TATASTEEL','date':'2026-05-07','dir':'LONG','bar':6,'pnl':-0.504,'result':'L'},
    {'sym':'GRASIM','date':'2026-05-07','dir':'LONG','bar':15,'pnl':-0.529,'result':'L'},
    {'sym':'BPCL','date':'2026-05-08','dir':'SHORT','bar':6,'pnl':-0.694,'result':'L'},
    {'sym':'COALINDIA','date':'2026-05-11','dir':'SHORT','bar':15,'pnl':-0.477,'result':'L'},
    {'sym':'POWERGRID','date':'2026-05-13','dir':'SHORT','bar':6,'pnl':-0.671,'result':'L'},
    # WINNERS
    {'sym':'EICHERMOT','date':'2026-04-30','dir':'SHORT','bar':6,'pnl':+0.398,'result':'W'},
    {'sym':'RELIANCE','date':'2026-04-30','dir':'SHORT','bar':6,'pnl':+0.463,'result':'W'},
    {'sym':'HINDUNILVR','date':'2026-05-04','dir':'LONG','bar':6,'pnl':+0.347,'result':'W'},
    {'sym':'CIPLA','date':'2026-05-04','dir':'LONG','bar':6,'pnl':+0.453,'result':'W'},
    {'sym':'BPCL','date':'2026-05-05','dir':'SHORT','bar':6,'pnl':+0.970,'result':'W'},
    {'sym':'SBILIFE','date':'2026-05-06','dir':'LONG','bar':6,'pnl':+0.544,'result':'W'},
    {'sym':'INDUSINDBK','date':'2026-05-06','dir':'LONG','bar':6,'pnl':+0.755,'result':'W'},
    {'sym':'TCS','date':'2026-05-06','dir':'SHORT','bar':15,'pnl':+0.694,'result':'W'},
    {'sym':'TECHM','date':'2026-05-11','dir':'LONG','bar':15,'pnl':+0.685,'result':'W'},
    {'sym':'TCS','date':'2026-05-12','dir':'SHORT','bar':6,'pnl':+0.655,'result':'W'},
    {'sym':'CIPLA','date':'2026-05-13','dir':'LONG','bar':6,'pnl':+0.995,'result':'W'},
    {'sym':'CIPLA','date':'2026-05-14','dir':'LONG','bar':6,'pnl':+0.914,'result':'W'},
    {'sym':'HCLTECH','date':'2026-05-14','dir':'SHORT','bar':6,'pnl':+0.980,'result':'W'},
]

print("=" * 100)
print("SETUP DEVELOPMENT: WINNERS vs LOSERS")
print("=" * 100)

w_finals = []; l_finals = []
w_building = 0; l_building = 0
w_decaying = 0; l_decaying = 0

for t in trades:
    setup = track_setup_development(t['sym'], t['dir'], '', all_data, t['date'], t['bar'])

    traj = setup.trajectory
    final = setup.final_convergence
    peak = setup.peak_convergence
    building = setup.is_building
    decaying = setup.is_decaying

    if t['result'] == 'W':
        w_finals.append(final)
        if building: w_building += 1
        if decaying: w_decaying += 1
    else:
        l_finals.append(final)
        if building: l_building += 1
        if decaying: l_decaying += 1

    bld = "BUILD" if building else "DECAY" if decaying else "MIXED"
    print(f"  {t['result']} {t['date']} {t['sym']:12s} {t['dir']:5s} {t['pnl']:+.3f}% | "
          f"traj={traj} | final={final:+d} peak={peak} {bld}")

print()
print("=" * 100)
print("SUMMARY")
print("=" * 100)

n_w = len(w_finals); n_l = len(l_finals)
print(f"Winners avg final convergence: {sum(w_finals)/n_w:+.1f} (of {n_w})")
print(f"Losers  avg final convergence: {sum(l_finals)/n_l:+.1f} (of {n_l})")
print(f"Winners building: {w_building}/{n_w} ({w_building/n_w*100:.0f}%)")
print(f"Losers  building: {l_building}/{n_l} ({l_building/n_l*100:.0f}%)")
print(f"Winners decaying: {w_decaying}/{n_w} ({w_decaying/n_w*100:.0f}%)")
print(f"Losers  decaying: {l_decaying}/{n_l} ({l_decaying/n_l*100:.0f}%)")

# Test threshold rules
print()
print("THRESHOLD TESTS:")
for threshold in [0, 1, 2, 3]:
    w_pass = sum(1 for f in w_finals if f >= threshold)
    l_pass = sum(1 for f in l_finals if f >= threshold)
    total = w_pass + l_pass
    wr = w_pass / total * 100 if total > 0 else 0
    print(f"  final >= {threshold}: {w_pass}W + {l_pass}L = {total} trades, WR={wr:.0f}%")

# Show full development for a couple examples
print()
print("=" * 100)
print("EXAMPLE: BEST WINNER vs WORST LOSER")
print("=" * 100)

best_w = max(trades, key=lambda t: t['pnl'] if t['result'] == 'W' else -999)
worst_l = min(trades, key=lambda t: t['pnl'] if t['result'] == 'L' else 999)

for label, t in [("WINNER", best_w), ("LOSER", worst_l)]:
    setup = track_setup_development(t['sym'], t['dir'], '', all_data, t['date'], t['bar'])
    print(f"\n{label}: {t['sym']} {t['dir']} {t['pnl']:+.3f}%")
    print(setup.format_for_llm())
