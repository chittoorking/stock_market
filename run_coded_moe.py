"""
Run Coded MoE — zero LLM calls, 121 days in seconds.
Same domain expertise as the 5 LLM experts, encoded as math.
"""
import sys; sys.path.insert(0, '.')
import csv, time
from pathlib import Path
from collections import defaultdict

data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.agents.coded_moe import coded_moe_scan
from app.agents.smart_stops import SmartStopCalculator
from app.agents.volatility import VolatilityAgent
from app.signals.base import get_sector

vol_agent = VolatilityAgent()

import argparse
_parser = argparse.ArgumentParser()
_parser.add_argument('--start', type=int, default=-10)
_parser.add_argument('--days', type=int, default=10)
_parser.add_argument('--min-score', type=float, default=35.0)
_args, _ = _parser.parse_known_args()

all_dates = sorted(set(r['timestamp'][:10] for rows in all_data.values() for r in rows))
if _args.start < 0:
    test_dates = all_dates[_args.start:_args.start + _args.days] if _args.start + _args.days < 0 else all_dates[_args.start:]
else:
    test_dates = all_dates[_args.start:_args.start + _args.days]
print(f"CODED MoE | {len(test_dates)} days: {test_dates[0]} to {test_dates[-1]} | min_score={_args.min_score}")
print(f"Stocks: {len(all_data)}")

all_results = []
t0 = time.time()

for date in test_dates:
    crowd = 0; total_syms = 0
    for sym, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or not db: continue
        total_syms += 1
        gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
        if abs(gap) > 0.3: crowd += 1

    day_pnl = 0; day_trades = 0; opened_today = []

    for scan_bar in [6, 15]:
        if len(opened_today) >= 3: break

        signals = []
        for sym, bars in all_data.items():
            if sym in [t['symbol'] for t in opened_today]: continue
            db = [b for b in bars if b['timestamp'][:10] == date]
            pb = [b for b in bars if b['timestamp'][:10] < date]
            if not pb or len(db) <= scan_bar: continue
            pc = pb[-1]['close']; bsf = db[:scan_bar+1]

            for scanner in [
                lambda: GapAndGoSignal.scan(sym, bsf[:3], pc),
                lambda: LenzSignal.scan(sym, bsf) if len(bsf)>=2 else None,
                lambda: AftershockSignal.scan(sym, bsf) if len(bsf)>=2 else None,
                lambda: GapDecaySignal.scan(sym, bsf, pc, crowd, total_syms) if len(bsf)>=7 and scan_bar==6 else None,
            ]:
                sig = scanner()
                if sig: signals.append(sig)

        if not signals: continue

        # Coded MoE selection — instant
        ranked = coded_moe_scan(signals, all_data, date, scan_bar, max_trades=3 - len(opened_today), min_score=_args.min_score)

        for r in ranked:
            sym = r['symbol']
            direction = r['direction']
            sig = next((s for s in signals if s.symbol == sym), None)
            if not sig: continue

            entry_price = sig.suggested_entry
            sb = [b for b in all_data.get(sym, []) if b['timestamp'][:10] == date]
            pa = [b for b in all_data.get(sym, []) if b['timestamp'][:10] <= date]
            atr = vol_agent.calculate_atr(pa[-30:]) if len(pa) >= 5 else 0
            stop, _ = SmartStopCalculator.calculate(sb, scan_bar, entry_price, direction, atr)
            target, _ = SmartStopCalculator.calculate_target(entry_price, stop, direction, 2.5)

            # Simulate
            exit_price = None; exit_reason = ''
            for j in range(scan_bar + 1, len(sb)):
                if direction == 'LONG':
                    if sb[j]['low'] <= stop: exit_price = stop; exit_reason = 'stop'; break
                    if sb[j]['high'] >= target: exit_price = target; exit_reason = 'target'; break
                else:
                    if sb[j]['high'] >= stop: exit_price = stop; exit_reason = 'stop'; break
                    if sb[j]['low'] <= target: exit_price = target; exit_reason = 'target'; break

            if not exit_price:
                exit_price = sb[-1]['close']; exit_reason = 'eod'

            pnl = (exit_price - entry_price) / entry_price * 100 if direction == 'LONG' else (entry_price - exit_price) / entry_price * 100

            day_pnl += pnl; day_trades += 1
            opened_today.append({
                'date': date, 'symbol': sym, 'direction': direction,
                'strategy': r['strategy'], 'sector': get_sector(sym),
                'entry': entry_price, 'exit': exit_price, 'exit_reason': exit_reason,
                'pnl': round(pnl, 3), 'score': r['score'], 'reason': r['reason'],
            })

    all_results.extend(opened_today)
    trades_str = " | ".join(f"{t['symbol']} {t['direction']} {t['strategy']} {t['pnl']:+.3f}% [{t['exit_reason']}] ({t['reason']})" for t in opened_today) if opened_today else "no trades"
    print(f"  {date}: {day_trades} trades, P&L={day_pnl:+.3f}% | {trades_str}")

elapsed = time.time() - t0

# ═══ REPORT ═══
print()
print('=' * 120)
print(f'CODED MoE | {len(test_dates)} DAYS | {elapsed:.1f}s | ZERO LLM calls')
print('=' * 120)
n = max(len(all_results), 1)
wins = sum(1 for r in all_results if r['pnl'] > 0)
losses = n - wins
total = sum(r['pnl'] for r in all_results)
print(f'TOTAL: {n} trades | W:{wins} L:{losses} | WR:{wins/n:.0%} | P&L:{total:+.3f}% | Avg:{total/n:+.3f}%')

for label, key in [("STRATEGY", "strategy"), ("EXIT", "exit_reason"), ("SECTOR", "sector")]:
    print(f'\n--- {label} ---')
    by = defaultdict(lambda: {'n': 0, 'p': 0, 'w': 0})
    for r in all_results:
        by[r[key]]['n'] += 1; by[r[key]]['p'] += r['pnl']
        if r['pnl'] > 0: by[r[key]]['w'] += 1
    for k, v in sorted(by.items(), key=lambda x: -x[1]['p']):
        print(f'  {str(k):12s}: {v["n"]:3d} trades, WR={v["w"]/v["n"]:.0%}, P&L={v["p"]:+.3f}%')

print(f'\n  Baseline MoE v1 (LLM): ~60% WR, +4-6% (10 days)')
print(f'  This run (coded):      {wins/n:.0%} WR, {total:+.3f}% ({len(test_dates)} days, {elapsed:.1f}s)')
