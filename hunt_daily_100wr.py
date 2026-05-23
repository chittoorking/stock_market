"""
Goal: 1 trade per day, 100% WR.
Uses ALL 15 strategies + multiple scan bars + multiple R:R.
For each day, find the BEST trade that would have won.
Then find what filter catches those winners without catching any losers.
"""
import sys; sys.path.insert(0, '.')
import csv
from pathlib import Path
from collections import defaultdict
from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.signals.mega_strategies import (OpeningRangeBreakout, PivotBreakout, EMA9_21Cross,
    SuperTrendSignal, VWAPCrossSignal, MomentumBurst, RSIExtreme, BollingerBounce,
    EngulfingPattern, InsideBarBreakout, HammerShootingStar, VolumeSpike, DayHighLowBreak)
from app.agents.coded_moe import score_volume, score_sector, score_price, score_momentum, score_market, score_macro
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
all_dates = sorted(set(r['timestamp'][:10] for rows in all_data.values() for r in rows))
EXIT_BAR = 69  # 3:00 PM

print(f'Scanning {len(all_dates)} days with ALL 15 strategies...', flush=True)

# For each day, collect ALL signals with scores and outcomes
daily_best = {}  # date -> best winning trade
daily_signals = defaultdict(list)  # date -> all signals

for date in all_dates:
    crowd = 0; total_syms = 0
    for sym, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or not db: continue
        total_syms += 1
        gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
        if abs(gap) > 0.3: crowd += 1

    for scan_bar in [6, 10, 15]:
        for sym in all_data:
            db = [b for b in all_data[sym] if b['timestamp'][:10] == date]
            pb = [b for b in all_data[sym] if b['timestamp'][:10] < date]
            if not pb or len(db) <= scan_bar: continue
            pc = pb[-1]['close']; bsf = db[:scan_bar+1]

            scanners = [
                lambda: GapAndGoSignal.scan(sym, bsf[:3], pc),
                lambda: LenzSignal.scan(sym, bsf) if len(bsf)>=2 else None,
                lambda: AftershockSignal.scan(sym, bsf) if len(bsf)>=2 else None,
                lambda: GapDecaySignal.scan(sym, bsf, pc, crowd, total_syms) if len(bsf)>=4 else None,
                lambda: OpeningRangeBreakout.scan(sym, bsf, pc) if len(bsf)>=7 else None,
                lambda: PivotBreakout.scan(sym, bsf, pc) if len(bsf)>=4 else None,
                lambda: EMA9_21Cross.scan(sym, bsf) if len(bsf)>=22 else None,
                lambda: SuperTrendSignal.scan(sym, bsf) if len(bsf)>=12 else None,
                lambda: VWAPCrossSignal.scan(sym, bsf) if len(bsf)>=5 else None,
                lambda: MomentumBurst.scan(sym, bsf) if len(bsf)>=6 else None,
                lambda: RSIExtreme.scan(sym, bsf) if len(bsf)>=15 else None,
                lambda: BollingerBounce.scan(sym, bsf) if len(bsf)>=20 else None,
                lambda: EngulfingPattern.scan(sym, bsf) if len(bsf)>=3 else None,
                lambda: InsideBarBreakout.scan(sym, bsf) if len(bsf)>=3 else None,
                lambda: HammerShootingStar.scan(sym, bsf) if len(bsf)>=5 else None,
                lambda: VolumeSpike.scan(sym, bsf) if len(bsf)>=6 else None,
                lambda: DayHighLowBreak.scan(sym, bsf) if len(bsf)>=5 else None,
            ]
            for scanner in scanners:
                try:
                    sig = scanner()
                except: continue
                if not sig: continue
                direction = sig.direction

                # Score
                v = score_volume(sym, db[:scan_bar+1], all_data, date, scan_bar)
                s = score_sector(sym, all_data, date, scan_bar, direction)
                p = score_price(sym, db[:scan_bar+1], scan_bar, pc, direction)
                m = score_momentum(sym, db[:scan_bar+1], scan_bar, direction)
                mkt = score_market(all_data, date, scan_bar, direction)
                mac = score_macro(date, direction)

                # Simulate with 3PM exit
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

                daily_signals[date].append({
                    'sym':sym,'dir':direction,'strat':sig.strategy_name,'scan':scan_bar,
                    'v':v,'s':s,'p':p,'m':m,'mkt':mkt,'mac':mac,
                    'pnl':pnl,'win':pnl>0
                })

# Stats
days_with_signals = len(daily_signals)
days_with_winners = sum(1 for d in daily_signals if any(s['win'] for s in daily_signals[d]))
total_signals = sum(len(sigs) for sigs in daily_signals.values())
total_winners = sum(sum(1 for s in sigs if s['win']) for sigs in daily_signals.values())

print(f'Days with signals: {days_with_signals}/{len(all_dates)}')
print(f'Days with at least 1 winner: {days_with_winners}')
print(f'Total signals: {total_signals}, Winners: {total_winners}')

# For each day, what's the best winning trade?
print(f'\nPER-DAY BEST WINNER:')
no_winner_days = []
for date in sorted(all_dates):
    sigs = daily_signals.get(date, [])
    winners = [s for s in sigs if s['win']]
    if winners:
        best = max(winners, key=lambda x: x['pnl'])
    else:
        no_winner_days.append(date)

print(f'Days with NO winners: {len(no_winner_days)} ({len(no_winner_days)/len(all_dates)*100:.0f}%)')
if no_winner_days:
    print(f'  Dates: {", ".join(no_winner_days[:10])}...')

# NOW: find filter that picks ONLY winners, ideally 1 per day
# Strategy: for each threshold combo, check if it produces 100% WR
print(f'\nSEARCHING for daily 100% WR filters (all 15 strategies, 3PM exit)...')
print('='*80, flush=True)

best_setups = []
for vmin in [5, 6, 7, 8]:
    for smin in [4, 5, 6, 7]:
        for pmin in [5, 6, 7, 8]:
            for mmin in [6, 7, 8, 9]:
                for mktmin in [5, 6, 7]:
                    # Filter all signals
                    filtered = [s for sigs in daily_signals.values() for s in sigs
                               if s['v']>=vmin and s['s']>=smin and s['p']>=pmin and s['m']>=mmin and s['mkt']>=mktmin]
                    if len(filtered) < 5: continue

                    # Deduplicate by date+sym
                    seen = set()
                    unique = []
                    for s in filtered:
                        # Find the date for this signal
                        for d, sigs in daily_signals.items():
                            if s in sigs:
                                key = (d, s['sym'])
                                if key not in seen:
                                    seen.add(key)
                                    unique.append(s)
                                break
                    if len(unique) < 5: continue

                    w = sum(1 for s in unique if s['win'])
                    wr = w / len(unique) * 100
                    total_pnl = sum(s['pnl'] for s in unique)

                    if wr >= 90:  # 90%+ WR
                        # Count unique days
                        days = set()
                        for s in unique:
                            for d, sigs in daily_signals.items():
                                if s in sigs:
                                    days.add(d)
                                    break

                        best_setups.append({
                            'vmin':vmin,'smin':smin,'pmin':pmin,'mmin':mmin,'mktmin':mktmin,
                            'n':len(unique),'w':w,'wr':wr,'pnl':total_pnl,
                            'avg':total_pnl/len(unique),'days':len(days)
                        })

best_setups.sort(key=lambda x: (-x['wr'], -x['n']))
print(f'Found {len(best_setups)} setups with 90%+ WR')
print(f"\n{'V>=':>4} {'S>=':>4} {'P>=':>4} {'M>=':>4} {'Mkt>=':>5} | {'N':>4} {'W':>3} {'WR':>5} {'Days':>5} {'P&L':>8} {'Avg':>7}")
print('-'*65)
for c in best_setups[:25]:
    marker = ' ***' if c['wr'] == 100 else ''
    print(f"{c['vmin']:>4} {c['smin']:>4} {c['pmin']:>4} {c['mmin']:>4} {c['mktmin']:>5} | {c['n']:>4} {c['w']:>3} {c['wr']:>4.0f}% {c['days']:>5} {c['pnl']:>+7.2f}% {c['avg']:>+6.3f}%{marker}")
