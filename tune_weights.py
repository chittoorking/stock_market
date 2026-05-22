"""
Rapid weight tuning for coded MoE.
Tests different expert weight combinations on 121 days.
Each run takes ~5 minutes. Grid search over key parameters.
"""
import sys; sys.path.insert(0, '.')
import csv, time
from pathlib import Path
from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.agents.coded_moe import score_volume, score_sector, score_price, score_momentum, score_market, score_macro
from app.agents.smart_stops import SmartStopCalculator
from app.agents.volatility import VolatilityAgent
from app.signals.base import get_sector

data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

vol_agent = VolatilityAgent()
all_dates = sorted(set(r['timestamp'][:10] for rows in all_data.values() for r in rows))

# Pre-compute all signals once
print("Pre-computing signals for all 121 days...", flush=True)
all_signals = []
for date in all_dates:
    crowd = 0; total_syms = 0
    for sym, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or not db: continue
        total_syms += 1
        gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
        if abs(gap) > 0.3: crowd += 1

    for scan_bar in [6]:
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
                if not sig: continue
                # Pre-compute scores
                direction = sig.direction
                v = score_volume(sym, db[:scan_bar+1], all_data, date, scan_bar)
                s = score_sector(sym, all_data, date, scan_bar, direction)
                p = score_price(sym, db[:scan_bar+1], scan_bar, pc, direction)
                m = score_momentum(sym, db[:scan_bar+1], scan_bar, direction)
                mkt = score_market(all_data, date, scan_bar, direction)
                mac = score_macro(date, direction)
                # Simulate outcome
                entry = sig.suggested_entry
                pa = [b for b in all_data.get(sym,[]) if b['timestamp'][:10] <= date]
                atr = vol_agent.calculate_atr(pa[-30:]) if len(pa) >= 5 else 0
                stop, _ = SmartStopCalculator.calculate(db, scan_bar, entry, direction, atr)
                target, _ = SmartStopCalculator.calculate_target(entry, stop, direction, 2.5)
                exit_price = None
                for j in range(scan_bar+1, len(db)):
                    if direction=='LONG':
                        if db[j]['low']<=stop: exit_price=stop; break
                        if db[j]['high']>=target: exit_price=target; break
                    else:
                        if db[j]['high']>=stop: exit_price=stop; break
                        if db[j]['low']<=target: exit_price=target; break
                if not exit_price: exit_price = db[-1]['close']
                pnl = (exit_price-entry)/entry*100 if direction=='LONG' else (entry-exit_price)/entry*100

                all_signals.append({
                    'date': date, 'sym': sym, 'dir': direction,
                    'strat': sig.strategy_name, 'sector': get_sector(sym),
                    'v': v, 's': s, 'p': p, 'm': m, 'mkt': mkt, 'mac': mac,
                    'pnl': pnl,
                })

print(f"Pre-computed {len(all_signals)} signals. Now testing weight combos...")

# Test weight combinations
best = None
results = []

for wv in [0.8, 1.0, 1.2, 1.5]:
    for ws in [0.5, 0.8, 1.0]:
        for wp in [0.8, 1.0, 1.2]:
            for wm in [1.0, 1.3, 1.5, 1.8]:
                for wmkt in [0.5, 0.8, 1.0]:
                    for wmac in [0.3, 0.5, 0.7, 1.0]:
                        for min_score_pct in [0.55, 0.60, 0.65, 0.70]:
                            # Score all signals
                            max_possible = (wv + ws + wp + wm + wmkt + wmac) * 10
                            min_score = max_possible * min_score_pct

                            # Simulate
                            by_date = {}
                            for sig in all_signals:
                                total = sig['v']*wv + sig['s']*ws + sig['p']*wp + sig['m']*wm + sig['mkt']*wmkt + sig['mac']*wmac
                                if total < min_score: continue
                                d = sig['date']
                                if d not in by_date: by_date[d] = []
                                by_date[d].append((total, sig))

                            # Pick top 3 per day, one per sector
                            trades = []
                            for d in sorted(by_date):
                                day_sigs = sorted(by_date[d], key=lambda x: -x[0])
                                sectors = set()
                                for score, sig in day_sigs:
                                    if sig['sector'] in sectors: continue
                                    if sig['sym'] in [t['sym'] for t in trades if t['date']==d]: continue
                                    sectors.add(sig['sector'])
                                    trades.append(sig)
                                    if len([t for t in trades if t['date']==d]) >= 3: break

                            if len(trades) < 50: continue  # Need enough trades

                            wins = sum(1 for t in trades if t['pnl'] > 0)
                            total_pnl = sum(t['pnl'] for t in trades)
                            wr = wins / len(trades) * 100
                            avg = total_pnl / len(trades)

                            r = {
                                'wv': wv, 'ws': ws, 'wp': wp, 'wm': wm, 'wmkt': wmkt, 'wmac': wmac,
                                'min_pct': min_score_pct,
                                'n': len(trades), 'wr': wr, 'pnl': total_pnl, 'avg': avg,
                            }
                            results.append(r)

                            if best is None or total_pnl > best['pnl']:
                                best = r

# Sort by P&L and show top 10
results.sort(key=lambda x: -x['pnl'])
print(f"\nTOP 10 WEIGHT COMBINATIONS (out of {len(results)} tested):")
print(f"{'V':>4} {'S':>4} {'P':>4} {'M':>4} {'Mkt':>4} {'Mac':>4} {'Min%':>5} | {'N':>4} {'WR':>5} {'P&L':>8} {'Avg':>7}")
print("-" * 70)
for r in results[:10]:
    print(f"{r['wv']:>4.1f} {r['ws']:>4.1f} {r['wp']:>4.1f} {r['wm']:>4.1f} {r['wmkt']:>4.1f} {r['wmac']:>4.1f} {r['min_pct']:>5.2f} | {r['n']:>4} {r['wr']:>4.0f}% {r['pnl']:>+7.2f}% {r['avg']:>+6.3f}%")

print(f"\nBEST: V={best['wv']} S={best['ws']} P={best['wp']} M={best['wm']} Mkt={best['wmkt']} Mac={best['wmac']} min={best['min_pct']:.0%}")
print(f"      {best['n']} trades, {best['wr']:.0f}% WR, {best['pnl']:+.2f}% P&L, {best['avg']:+.3f}% avg")
