"""
Exhaustive hunt for ALL 100% WR setups across:
- Different scan bars (6, 10, 15)
- Different R:R ratios (1.5, 2.0, 2.5, 3.0)
- 3:00 PM exit
- All score threshold combos
"""
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
EXIT_BAR = 69  # 3:00 PM

all_setups = []

for scan_bar in [6, 10, 15]:
    for rr in [1.5, 2.0, 2.5, 3.0]:
        print(f'Scanning bar={scan_bar} R:R={rr}...', flush=True)
        results = []
        for date in all_dates:
            crowd = 0; total_syms = 0
            for sym, bars in all_data.items():
                db = [b for b in bars if b['timestamp'][:10] == date]
                pb = [b for b in bars if b['timestamp'][:10] < date]
                if not pb or not db: continue
                total_syms += 1
                gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
                if abs(gap) > 0.3: crowd += 1
            for sym in all_data:
                db = [b for b in all_data[sym] if b['timestamp'][:10] == date]
                pb = [b for b in all_data[sym] if b['timestamp'][:10] < date]
                if not pb or len(db) <= scan_bar: continue
                pc = pb[-1]['close']; bsf = db[:scan_bar+1]
                for scanner in [
                    lambda: GapAndGoSignal.scan(sym, bsf[:3], pc),
                    lambda: LenzSignal.scan(sym, bsf) if len(bsf)>=2 else None,
                    lambda: AftershockSignal.scan(sym, bsf) if len(bsf)>=2 else None,
                    lambda: GapDecaySignal.scan(sym, bsf, pc, crowd, total_syms) if len(bsf)>=4 else None,
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
                    entry = sig.suggested_entry
                    pa = [b for b in all_data[sym] if b['timestamp'][:10] <= date]
                    atr = vol_agent.calculate_atr(pa[-30:]) if len(pa) >= 5 else 0
                    stop, _ = SmartStopCalculator.calculate(db, scan_bar, entry, direction, atr)
                    target, _ = SmartStopCalculator.calculate_target(entry, stop, direction, rr)
                    exit_price = None
                    for j in range(scan_bar+1, min(len(db), EXIT_BAR+1)):
                        if direction=='LONG':
                            if db[j]['low']<=stop: exit_price=stop; break
                            if db[j]['high']>=target: exit_price=target; break
                        else:
                            if db[j]['high']>=stop: exit_price=stop; break
                            if db[j]['low']<=target: exit_price=target; break
                    if not exit_price:
                        exit_price = db[min(EXIT_BAR, len(db)-1)]['close']
                    pnl = (exit_price-entry)/entry*100 if direction=='LONG' else (entry-exit_price)/entry*100
                    results.append({'v':v,'s':s,'p':p,'m':m,'mkt':mkt,'mac':mac,
                        'strat':sig.strategy_name,'pnl':pnl,'win':pnl>0,
                        'date':date,'sym':sym,'dir':direction})

        # Search for 100% WR combos
        for vmin in [5, 6, 7, 8]:
            for smin in [4, 5, 6, 7]:
                for pmin in [5, 6, 7, 8]:
                    for mmin in [5, 6, 7, 8, 9]:
                        for mktmin in [4, 5, 6, 7]:
                            sub = [r for r in results if r['v']>=vmin and r['s']>=smin and r['p']>=pmin and r['m']>=mmin and r['mkt']>=mktmin]
                            if len(sub) < 5: continue
                            seen = set()
                            unique = []
                            for r in sub:
                                key = (r['date'], r['sym'])
                                if key in seen: continue
                                seen.add(key)
                                unique.append(r)
                            if len(unique) < 5: continue
                            w = sum(1 for r in unique if r['win'])
                            if w == len(unique):
                                total_pnl = sum(r['pnl'] for r in unique)
                                all_setups.append({
                                    'scan':scan_bar,'rr':rr,
                                    'vmin':vmin,'smin':smin,'pmin':pmin,'mmin':mmin,'mktmin':mktmin,
                                    'n':len(unique),'pnl':total_pnl,'avg':total_pnl/len(unique)
                                })

# Deduplicate and sort
seen = set()
unique_setups = []
for c in sorted(all_setups, key=lambda x: (-x['n'], -x['pnl'])):
    key = (c['scan'], c['rr'], c['n'], round(c['pnl'], 1))
    if key in seen: continue
    seen.add(key)
    unique_setups.append(c)

print(f'\n{"="*80}')
print(f'ALL 100% WR SETUPS FOUND: {len(unique_setups)}')
print(f'{"="*80}')
print(f"{'Bar':>4} {'R:R':>4} {'V>=':>4} {'S>=':>4} {'P>=':>4} {'M>=':>4} {'Mkt>=':>5} | {'N':>3} {'P&L':>8} {'Avg':>7}")
print('-'*60)
for c in unique_setups[:30]:
    print(f"{c['scan']:>4} {c['rr']:>4.1f} {c['vmin']:>4} {c['smin']:>4} {c['pmin']:>4} {c['mmin']:>4} {c['mktmin']:>5} | {c['n']:>3} {c['pnl']:>+7.2f}% {c['avg']:>+6.3f}%")

# Show setups that DON'T overlap with locked setup A
print(f'\n{"="*80}')
print(f'NON-OVERLAPPING 100% WR SETUPS (different scan bar or R:R):')
print(f'{"="*80}')
for c in unique_setups:
    if c['scan'] != 6 or c['rr'] != 2.5:
        print(f"  Bar {c['scan']} R:R {c['rr']}: V>={c['vmin']} S>={c['smin']} P>={c['pmin']} M>={c['mmin']} Mkt>={c['mktmin']} | {c['n']} trades, {c['pnl']:+.2f}%, {c['avg']:+.3f}%/trade")
