"""
Analyze signals that existed but MoE v1 did NOT pick.
Check which of those WOULD have been winners if traded.
If there are missed winners, the improvement path is taking MORE signals, not filtering harder.
"""
import sys; sys.path.insert(0, '.')
import csv
from pathlib import Path

from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.agents.smart_stops import SmartStopCalculator
from app.agents.volatility import VolatilityAgent

data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

vol_agent = VolatilityAgent()

# MoE v1 traded these symbols on these dates
moe_picks = {
    "2026-04-30": ["TATASTEEL", "MARUTI", "BPCL"],
    "2026-05-02": ["HINDUNILVR"],
    "2026-05-05": ["BPCL", "CIPLA", "RELIANCE"],
    "2026-05-06": ["INDUSINDBK", "TCS"],
    "2026-05-07": ["EICHERMOT", "TATASTEEL", "GRASIM"],
    "2026-05-08": ["BPCL"],
    "2026-05-11": ["AXISBANK", "BPCL", "TITAN"],
    "2026-05-12": ["TCS"],
    "2026-05-13": ["CIPLA", "JSWSTEEL"],
    "2026-05-14": ["BHARTIARTL", "HCLTECH"],
}

all_dates = sorted(set(r['timestamp'][:10] for rows in all_data.values() for r in rows))
test_dates = all_dates[-10:]

print("=" * 120)
print("MISSED SIGNALS ANALYSIS: What would have happened if MoE picked other signals?")
print("=" * 120)

missed_winners = []
missed_losers = []

for date in test_dates:
    picked = moe_picks.get(date, [])

    # Crowd count
    crowd = 0; total_syms = 0
    for sym, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or not db: continue
        total_syms += 1
        gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
        if abs(gap) > 0.3: crowd += 1

    for scan_bar in [6]:
        signals = []
        for sym, bars in all_data.items():
            db = [b for b in bars if b['timestamp'][:10] == date]
            pb = [b for b in bars if b['timestamp'][:10] < date]
            if not pb or len(db) <= scan_bar: continue
            pc = pb[-1]['close']; bsf = db[:scan_bar+1]

            for scanner in [
                lambda: GapAndGoSignal.scan(sym, bsf[:3], pc),
                lambda: LenzSignal.scan(sym, bsf) if len(bsf)>=2 else None,
                lambda: AftershockSignal.scan(sym, bsf) if len(bsf)>=2 else None,
                lambda: GapDecaySignal.scan(sym, bsf, pc, crowd, total_syms) if len(bsf)>=7 else None,
            ]:
                sig = scanner()
                if sig: signals.append(sig)

        # Find signals MoE DIDN'T pick
        for sig in signals:
            if sig.symbol in picked:
                continue  # Already picked

            # Simulate this trade
            sb = [b for b in all_data.get(sig.symbol, []) if b['timestamp'][:10] == date]
            pa = [b for b in all_data.get(sig.symbol, []) if b['timestamp'][:10] <= date]
            if not sb or len(sb) <= scan_bar:
                continue

            entry_price = sig.suggested_entry
            direction = sig.direction
            atr = vol_agent.calculate_atr(pa[-30:]) if len(pa) >= 5 else 0
            stop, _ = SmartStopCalculator.calculate(sb, scan_bar, entry_price, direction, atr)
            target, _ = SmartStopCalculator.calculate_target(entry_price, stop, direction, 2.5)

            # Simulate bar by bar
            exit_price = None; exit_reason = ''
            max_fav = 0
            for j in range(scan_bar + 1, len(sb)):
                if direction == 'LONG':
                    fav = sb[j]['high'] - entry_price
                    if sb[j]['low'] <= stop: exit_price = stop; exit_reason = 'stop'; break
                    if sb[j]['high'] >= target: exit_price = target; exit_reason = 'target'; break
                else:
                    fav = entry_price - sb[j]['low']
                    if sb[j]['high'] >= stop: exit_price = stop; exit_reason = 'stop'; break
                    if sb[j]['low'] <= target: exit_price = target; exit_reason = 'target'; break
                max_fav = max(max_fav, fav)

            if not exit_price:
                exit_price = sb[-1]['close']
                exit_reason = 'eod'

            if direction == 'LONG':
                pnl = (exit_price - entry_price) / entry_price * 100
            else:
                pnl = (entry_price - exit_price) / entry_price * 100

            mfe = max_fav / entry_price * 100
            result = "W" if pnl > 0 else "L"

            record = {
                "date": date, "sym": sig.symbol, "dir": direction,
                "strat": sig.strategy_name, "pnl": pnl, "mfe": mfe,
                "exit": exit_reason, "result": result,
            }

            if pnl > 0:
                missed_winners.append(record)
            else:
                missed_losers.append(record)

print(f"\nTotal missed signals: {len(missed_winners) + len(missed_losers)}")
print(f"Missed WINNERS: {len(missed_winners)}")
print(f"Missed LOSERS: {len(missed_losers)}")

if missed_winners:
    print(f"\n--- TOP MISSED WINNERS (signals MoE skipped that would have been profitable) ---")
    for r in sorted(missed_winners, key=lambda x: -x['pnl'])[:15]:
        print(f"  {r['date']} {r['sym']:>12} {r['dir']:>5} {r['strat']:>5} P&L={r['pnl']:+.3f}% MFE={r['mfe']:.3f}% exit={r['exit']}")

    total_missed_pnl = sum(r['pnl'] for r in missed_winners)
    print(f"\n  Total missed profit: +{total_missed_pnl:.3f}% across {len(missed_winners)} trades")

if missed_losers:
    print(f"\n--- MISSED LOSERS (correctly avoided) ---")
    total_avoided = sum(r['pnl'] for r in missed_losers)
    print(f"  Total avoided loss: {total_avoided:.3f}% across {len(missed_losers)} trades")

# Net analysis
if missed_winners or missed_losers:
    all_missed = missed_winners + missed_losers
    total_pnl = sum(r['pnl'] for r in all_missed)
    wr = len(missed_winners) / len(all_missed) * 100
    print(f"\n--- IF MoE TOOK ALL SIGNALS (no filtering) ---")
    print(f"  Extra trades: {len(all_missed)} | WR: {wr:.0f}% | Net P&L: {total_pnl:+.3f}%")
