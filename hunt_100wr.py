"""Hunt for near-100% WR filter combinations on 121 days."""
import sys; sys.path.insert(0, '.')
import csv
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

results = []
print("Scanning 121 days...", flush=True)
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
        for sym in all_data:
            db = [b for b in all_data[sym] if b['timestamp'][:10] == date]
            pb = [b for b in all_data[sym] if b['timestamp'][:10] < date]
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
                direction = sig.direction
                v = score_volume(sym, db[:scan_bar+1], all_data, date, scan_bar)
                s = score_sector(sym, all_data, date, scan_bar, direction)
                p = score_price(sym, db[:scan_bar+1], scan_bar, pc, direction)
                m = score_momentum(sym, db[:scan_bar+1], scan_bar, direction)
                mkt = score_market(all_data, date, scan_bar, direction)
                mac = score_macro(date, direction)

                # Kill conditions
                if sig.strategy_name == 'gdr4' and v < 5: continue
                if sig.strategy_name == 'aft7' and m < 6: continue
                if sig.strategy_name == 'lnz3' and m < 7: continue
                if mkt < 4: continue
                if v < 5 and m < 6: continue

                entry = sig.suggested_entry
                pa = [b for b in all_data[sym] if b['timestamp'][:10] <= date]
                atr = vol_agent.calculate_atr(pa[-30:]) if len(pa) >= 5 else 0
                stop, _ = SmartStopCalculator.calculate(db, scan_bar, entry, direction, atr)
                target, _ = SmartStopCalculator.calculate_target(entry, stop, direction, 2.5)
                exit_price = None; exit_reason = 'eod'
                for j in range(scan_bar+1, len(db)):
                    if direction=='LONG':
                        if db[j]['low']<=stop: exit_price=stop; exit_reason='stop'; break
                        if db[j]['high']>=target: exit_price=target; exit_reason='target'; break
                    else:
                        if db[j]['high']>=stop: exit_price=stop; exit_reason='stop'; break
                        if db[j]['low']<=target: exit_price=target; exit_reason='target'; break
                if not exit_price: exit_price = db[-1]['close']
                pnl = (exit_price-entry)/entry*100 if direction=='LONG' else (entry-exit_price)/entry*100

                results.append({
                    'date':date,'sym':sym,'dir':direction,
                    'v':v,'s':s,'p':p,'m':m,'mkt':mkt,'mac':mac,
                    'strat':sig.strategy_name,'pnl':pnl,'win':pnl>0,
                    'exit':exit_reason,
                })

print(f'Total: {len(results)} signals, {sum(1 for r in results if r["win"])} winners')

# Exhaustive search: try every combo of minimum thresholds
print("\nHUNTING FOR HIGHEST WR COMBOS (min 10 trades):")
print("=" * 90)

best_combos = []
for vmin in [5, 6, 7, 8]:
    for smin in [4, 5, 6, 7, 8]:
        for pmin in [5, 6, 7, 8]:
            for mmin in [6, 7, 8, 9, 10]:
                for mktmin in [4, 5, 6, 7]:
                    sub = [r for r in results if r['v']>=vmin and r['s']>=smin and r['p']>=pmin and r['m']>=mmin and r['mkt']>=mktmin]
                    if len(sub) < 10: continue
                    w = sum(1 for r in sub if r['win'])
                    wr = w / len(sub) * 100
                    avg = sum(r['pnl'] for r in sub) / len(sub)
                    total_pnl = sum(r['pnl'] for r in sub)
                    if wr >= 50:
                        best_combos.append({
                            'vmin':vmin,'smin':smin,'pmin':pmin,'mmin':mmin,'mktmin':mktmin,
                            'n':len(sub),'w':w,'wr':wr,'avg':avg,'total':total_pnl
                        })

best_combos.sort(key=lambda x: (-x['wr'], -x['total']))
print(f"{'V>=':>4} {'S>=':>4} {'P>=':>4} {'M>=':>4} {'Mkt>=':>5} | {'N':>4} {'W':>4} {'WR':>5} {'Avg':>7} {'Total':>8}")
print("-" * 65)
for c in best_combos[:20]:
    marker = ' <<<' if c['wr'] >= 60 else ''
    print(f"{c['vmin']:>4} {c['smin']:>4} {c['pmin']:>4} {c['mmin']:>4} {c['mktmin']:>5} | {c['n']:>4} {c['w']:>4} {c['wr']:>4.0f}% {c['avg']:>+6.3f}% {c['total']:>+7.2f}%{marker}")

if best_combos:
    b = best_combos[0]
    print(f"\nBEST: V>={b['vmin']} S>={b['smin']} P>={b['pmin']} M>={b['mmin']} Mkt>={b['mktmin']}")
    print(f"      {b['n']} trades, {b['wr']:.0f}% WR, {b['total']:+.2f}% total, {b['avg']:+.3f}% avg")

    # Show the actual trades
    sub = [r for r in results if r['v']>=b['vmin'] and r['s']>=b['smin'] and r['p']>=b['pmin'] and r['m']>=b['mmin'] and r['mkt']>=b['mktmin']]
    print(f"\nTrades:")
    for r in sorted(sub, key=lambda x: x['date']):
        w = 'W' if r['win'] else 'L'
        print(f"  {r['date']} {r['sym']:>12} {r['dir']:>5} {r['strat']:>5} P&L={r['pnl']:+.3f}% [{r['exit']}] V:{r['v']:.0f} S:{r['s']:.0f} P:{r['p']:.0f} M:{r['m']:.0f} Mkt:{r['mkt']:.0f} {w}")
