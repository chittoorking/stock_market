"""
New approach: instead of one static filter, find the BEST SINGLE TRADE
per day using a RANKING system. Pick the #1 ranked signal each day.
Test: does always picking #1 give high WR?

Also: analyze what score the best winner has on each day.
Maybe the winning signal is always the HIGHEST scored one.
"""
import sys; sys.path.insert(0, '.')
import csv
from pathlib import Path
from collections import defaultdict
from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.signals.mega_strategies import *
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
EXIT_BAR = 69

print('Computing ALL signals with scores for 121 days...', flush=True)

daily_results = {}
for date in all_dates:
    crowd = 0; total_syms = 0
    for sym, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or not db: continue
        total_syms += 1
        gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
        if abs(gap) > 0.3: crowd += 1

    signals = []
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
                lambda: GapDecaySignal.scan(sym, bsf, pc, crowd, total_syms) if len(bsf)>=4 else None,
                lambda: OpeningRangeBreakout.scan(sym, bsf, pc) if len(bsf)>=7 else None,
                lambda: PivotBreakout.scan(sym, bsf, pc) if len(bsf)>=4 else None,
                lambda: VWAPCrossSignal.scan(sym, bsf) if len(bsf)>=5 else None,
                lambda: MomentumBurst.scan(sym, bsf) if len(bsf)>=6 else None,
                lambda: EngulfingPattern.scan(sym, bsf) if len(bsf)>=3 else None,
                lambda: DayHighLowBreak.scan(sym, bsf) if len(bsf)>=5 else None,
                lambda: VolumeSpike.scan(sym, bsf) if len(bsf)>=6 else None,
                lambda: HammerShootingStar.scan(sym, bsf) if len(bsf)>=5 else None,
            ]:
                try:
                    sig = scanner()
                except: continue
                if not sig: continue
                direction = sig.direction
                v = score_volume(sym, db[:scan_bar+1], all_data, date, scan_bar)
                s = score_sector(sym, all_data, date, scan_bar, direction)
                p = score_price(sym, db[:scan_bar+1], scan_bar, pc, direction)
                m = score_momentum(sym, db[:scan_bar+1], scan_bar, direction)
                mkt = score_market(all_data, date, scan_bar, direction)
                mac = score_macro(date, direction)

                # Weighted score (from grid search: V*1.5 + M*1.3 + P*0.8 + S*0.5 + Mkt*0.5 + Mac*0.5)
                total_score = v*1.5 + m*1.3 + p*0.8 + s*0.5 + mkt*0.5 + mac*0.5

                # Simulate 3PM exit
                entry = sig.suggested_entry
                pa = [b for b in all_data[sym] if b['timestamp'][:10] <= date]
                atr = vol_agent.calculate_atr(pa[-30:]) if len(pa) >= 5 else 0
                stop, _ = SmartStopCalculator.calculate(db, scan_bar, entry, direction, atr)
                target, _ = SmartStopCalculator.calculate_target(entry, stop, direction, 2.5)
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

                signals.append({
                    'sym':sym,'dir':direction,'strat':sig.strategy_name,
                    'v':v,'s':s,'p':p,'m':m,'mkt':mkt,'mac':mac,
                    'score':total_score,'pnl':pnl,'win':pnl>0
                })

    # Deduplicate by symbol (keep highest scored)
    by_sym = {}
    for sig in signals:
        key = sig['sym']
        if key not in by_sym or sig['score'] > by_sym[key]['score']:
            by_sym[key] = sig
    daily_results[date] = sorted(by_sym.values(), key=lambda x: -x['score'])

# ANALYSIS 1: If we always pick the #1 ranked signal, what's WR?
print('\nSTRATEGY: Pick #1 ranked signal each day')
print('='*60)
top1_wins = 0; top1_total = 0; top1_pnl = 0
for date in sorted(daily_results):
    sigs = daily_results[date]
    if not sigs: continue
    top = sigs[0]
    top1_total += 1
    if top['win']: top1_wins += 1
    top1_pnl += top['pnl']

print(f'  {top1_total} days | W:{top1_wins} L:{top1_total-top1_wins} | WR:{top1_wins/top1_total*100:.0f}% | P&L:{top1_pnl:+.2f}%')

# ANALYSIS 2: Pick #1 but only if score > threshold
print('\nSTRATEGY: Pick #1 only if score > threshold')
print('='*60)
for threshold in [30, 33, 35, 37, 39, 40, 42, 45]:
    wins = 0; total = 0; pnl = 0
    for date in sorted(daily_results):
        sigs = daily_results[date]
        if not sigs: continue
        top = sigs[0]
        if top['score'] < threshold: continue
        total += 1
        if top['win']: wins += 1
        pnl += top['pnl']
    if total < 5: continue
    wr = wins/total*100
    avg = pnl/total
    marker = ' <<<' if wr >= 60 else ''
    print(f'  Score>{threshold}: {total} days | W:{wins} L:{total-wins} | WR:{wr:.0f}% | P&L:{pnl:+.2f}% | Avg:{avg:+.3f}%{marker}')

# ANALYSIS 3: Was the best winner also the highest scored?
print('\nANALYSIS: Is the best winner always the #1 ranked?')
print('='*60)
top1_is_best = 0; top3_has_best = 0; total_days = 0
for date in sorted(daily_results):
    sigs = daily_results[date]
    if not sigs: continue
    winners = [s for s in sigs if s['win']]
    if not winners: continue
    total_days += 1
    best_winner = max(winners, key=lambda x: x['pnl'])
    if sigs[0] == best_winner: top1_is_best += 1
    if best_winner in sigs[:3]: top3_has_best += 1

print(f'  Days with winners: {total_days}')
print(f'  #1 ranked IS the best winner: {top1_is_best}/{total_days} ({top1_is_best/total_days*100:.0f}%)')
print(f'  Best winner in top 3: {top3_has_best}/{total_days} ({top3_has_best/total_days*100:.0f}%)')
