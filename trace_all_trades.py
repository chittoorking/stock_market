"""
Trace v3: All fixes + new signals + bar 9 skip + AFT7 tighter + energy penalty.
"""
import sys; sys.path.insert(0, '.')
import csv
from pathlib import Path
from collections import defaultdict

data_dir = Path('C:/Users/HP/Downloads/Temp/Food Ordering All/stock/Stock Market Trading/data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

from app.signals.proven_strategies import GapAndGoSignal, ORBSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.signals.new_signals import GapReversalSignal, MorningMomentumSignal
from app.signals.new_signals_v2 import DriveExhaustionSignal
from app.signals.agent_reasoning import build_snapshot, agent_decide
from app.agents.monitor import PositionMonitorAgent, PositionState
from app.agents.volatility import VolatilityAgent
from app.signals.base import get_sector, SECTOR_MAP

monitor = PositionMonitorAgent()
vol_agent = VolatilityAgent()
all_days = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))
all_results = []

for date in all_days:
    # Crowd gap count
    gap_up_count = 0; gap_down_count = 0; total_with_prev = 0
    for symbol, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or not db: continue
        total_with_prev += 1
        gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
        if gap > 0.3: gap_up_count += 1
        elif gap < -0.3: gap_down_count += 1
    crowd_count = max(gap_up_count, gap_down_count)

    # Pre-calculate sector leader changes for momentum signal
    sector_leader_chg = {}
    for sn, si in SECTOR_MAP.items():
        lb = [b for b in all_data.get(si['leader'], []) if b['timestamp'][:10] == date]
        if lb and len(lb) >= 7:
            sector_leader_chg[sn] = (lb[5]['close'] - lb[0]['open']) / lb[0]['open'] * 100

    day_positions = {}

    # Pre-scan: identify exhaustion stocks (context for later, NOT entry signals)
    exhaustion_stocks = set()
    for symbol, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        if len(db) >= 2:
            sig = DriveExhaustionSignal.scan(symbol, db[:2])
            if sig:
                exhaustion_stocks.add(symbol)

    # Scan at bars 6, 12, 15 — proven entry windows only
    for scan_bar in [6, 12, 15]:
        if len(day_positions) >= 3:
            break

        signals = []
        for symbol, bars in all_data.items():
            if symbol in day_positions: continue
            db = [b for b in bars if b['timestamp'][:10] == date]
            pb = [b for b in bars if b['timestamp'][:10] < date]
            if not pb or len(db) <= scan_bar: continue
            pc = pb[-1]['close']
            bsf = db[:scan_bar + 1]
            sector = get_sector(symbol)

            # NEW: Drive Exhaustion (scan at bar 1 — earliest possible signal)
            if scan_bar == 1:
                sig = DriveExhaustionSignal.scan(symbol, bsf)
                if sig: signals.append(sig)
                continue  # Bar 1: only exhaustion, nothing else ready

            # Gap Reversal (scan at bar 3)
            if scan_bar == 3:
                sig = GapReversalSignal.scan(symbol, bsf, pc)
                if sig: signals.append(sig)
                continue

            # NEW: Morning Momentum (scan at bar 6 only)
            if scan_bar == 6 and len(bsf) >= 8:
                leader_chg = sector_leader_chg.get(sector, 0)
                sig = MorningMomentumSignal.scan(symbol, bsf, leader_chg)
                if sig: signals.append(sig)

            # Existing strategies
            sig = GapAndGoSignal.scan(symbol, bsf[:3], pc)
            if sig: signals.append(sig)

            if len(bsf) >= 5 and scan_bar <= 8:
                sig = ORBSignal.scan(symbol, bsf[:10])
                if sig: signals.append(sig)

            if len(bsf) >= 2:
                sig = LenzSignal.scan(symbol, bsf)
                if sig: signals.append(sig)
                sig = AftershockSignal.scan(symbol, bsf)
                if sig: signals.append(sig)

            if len(bsf) >= 7 and scan_bar == 6:
                sig = GapDecaySignal.scan(symbol, bsf, pc,
                                          crowd_gap_count=crowd_count,
                                          total_symbols=total_with_prev)
                if sig: signals.append(sig)

        if not signals: continue

        remaining_slots = 3 - len(day_positions)
        snapshot = build_snapshot(signals, all_data, date, min(scan_bar, 6))
        decisions = agent_decide(snapshot, max_trades=remaining_slots,
                                 portfolio_positions=day_positions)
        taken = [d for d in decisions if d.take]

        for dec in taken:
            sym = dec.signal.symbol
            sym_bars = [b for b in all_data[sym] if b['timestamp'][:10] == date]
            if len(sym_bars) < scan_bar + 5: continue

            entry = dec.signal.suggested_entry
            entry_bar = scan_bar
            direction = dec.signal.direction

            # Volatility-adjusted stops
            prev_bars = [b for b in all_data[sym] if b['timestamp'][:10] <= date]
            vol_result = vol_agent.get_adjusted_stop(entry, direction, prev_bars[-30:])
            stop = vol_result["stop"]
            target = vol_result["target"]
            ir = abs(entry - stop)

            # Energy sector penalty: reduce size
            sector = get_sector(sym)
            size_mult = 0.5 if sector == "energy" else 1.0

            monitor._profit_states = {}
            pos = PositionState(symbol=sym, direction=direction, entry_price=entry,
                entry_bar=entry_bar, entry_regime='unknown',
                current_stop=stop, current_target=target, initial_risk=ir,
                strategy=dec.signal.strategy_name)

            day_positions[sym] = {"direction": direction, "entry": entry}

            cur_stop = stop; max_fav = 0; partial_taken = False
            exit_price = None; exit_reason = ''; exit_bar = 0; partial_pnl = 0

            for j in range(entry_bar + 1, len(sym_bars)):
                b = sym_bars[j]; bh = j - entry_bar
                if direction == 'LONG':
                    pnl = (b['close'] - entry) / entry * 100
                    fav = b['high'] - entry
                else:
                    pnl = (entry - b['close']) / entry * 100
                    fav = entry - b['low']
                max_fav = max(max_fav, fav)

                pos.bars_held = bh; pos.current_pnl_pct = pnl
                pos.max_favorable = max_fav; pos.current_stop = cur_stop

                if direction == 'LONG':
                    if b['low'] <= cur_stop: exit_price = cur_stop; exit_reason = 'stop'; exit_bar = j; break
                    if b['high'] >= target: exit_price = target; exit_reason = 'target'; exit_bar = j; break
                else:
                    if b['high'] >= cur_stop: exit_price = cur_stop; exit_reason = 'stop'; exit_bar = j; break
                    if b['low'] <= target: exit_price = target; exit_reason = 'target'; exit_bar = j; break

                if bh % 3 == 0 and bh > 0:
                    decision = monitor.evaluate(pos, all_data, date, j)
                    if decision.close_now:
                        exit_price = b['close']; exit_reason = 'agent'; exit_bar = j; break
                    if decision.new_stop and decision.new_stop > cur_stop:
                        cur_stop = decision.new_stop
                    if decision.action.value == 'REDUCE_POSITION' and not partial_taken:
                        partial_taken = True; partial_pnl = pnl

            if exit_price is None:
                exit_price = sym_bars[-1]['close']; exit_reason = 'eod'; exit_bar = len(sym_bars) - 1

            if direction == 'LONG':
                final_pnl_pct = (exit_price - entry) / entry * 100
            else:
                final_pnl_pct = (entry - exit_price) / entry * 100

            blended = 0.30 * partial_pnl + 0.70 * final_pnl_pct if partial_taken else final_pnl_pct

            all_results.append({
                'date': date, 'symbol': sym, 'direction': direction,
                'strategy': dec.signal.strategy_name, 'sector': get_sector(sym),
                'entry': entry, 'exit': exit_price, 'exit_reason': exit_reason,
                'pnl_pct': round(final_pnl_pct, 3), 'blended_pnl': round(blended, 3),
                'mfe_pct': round(max_fav / entry * 100, 3), 'bars': exit_bar - entry_bar,
                'partial': partial_taken, 'conviction': round(dec.final_conviction, 2),
                'stop_pct': vol_result['stop_pct'], 'atr_pct': vol_result['atr_pct'],
                'scan_bar': scan_bar, 'size_mult': size_mult,
            })
            day_positions.pop(sym, None)

# ─── REPORT ───
print('=' * 120)
print(f'TRACE v4: {len(all_days)} DAYS | +Exhaustion +VolDiv agent | -Bar9 | AFT7 1.5x | Energy 0.5x')
print('=' * 120)
print(f'{"Date":>10} {"Symbol":>12} {"Dir":>5} {"Strat":>8} {"Sector":>8} {"Entry":>8} {"Exit":>8} {"P&L%":>7} {"Blend":>7} {"MFE%":>6} {"Bars":>4} {"P":>2} {"SL%":>5} {"SB":>3} {"Exit":>8}')
print('-' * 120)

total_bl = 0; wins = 0; losses = 0; weighted_pnl = 0
for r in all_results:
    w = 'W' if r['blended_pnl'] > 0 else 'L'
    if r['blended_pnl'] > 0: wins += 1
    else: losses += 1
    total_bl += r['blended_pnl']
    weighted_pnl += r['blended_pnl'] * r['size_mult']
    p = 'Y' if r['partial'] else ''
    print(f'{r["date"]:>10} {r["symbol"]:>12} {r["direction"]:>5} {r["strategy"]:>8} {r["sector"]:>8} {r["entry"]:8.2f} {r["exit"]:8.2f} {r["pnl_pct"]:+6.3f}% {r["blended_pnl"]:+6.3f}% {r["mfe_pct"]:5.3f}% {r["bars"]:4d} {p:>2} {r["stop_pct"]:4.2f}% {r["scan_bar"]:3d} {r["exit_reason"]:>8} {w}')

n = max(len(all_results), 1)
print('-' * 120)
print(f'TOTAL: {len(all_results)} trades | W:{wins} L:{losses} | WR:{wins/n:.0%} | Blended:{total_bl:+.3f}% | Weighted:{weighted_pnl:+.3f}% | Avg:{total_bl/n:+.3f}%/trade')

print('\n--- BY STRATEGY ---')
by = defaultdict(lambda: {'n':0,'pnl':0,'w':0})
for r in all_results:
    by[r['strategy']]['n']+=1; by[r['strategy']]['pnl']+=r['blended_pnl']
    if r['blended_pnl']>0: by[r['strategy']]['w']+=1
for k,v in sorted(by.items(), key=lambda x:-x[1]['pnl']):
    wr = v['w']/v['n'] if v['n'] else 0
    print(f'  {k:10s}: {v["n"]:3d} trades, WR={wr:.0%}, P&L={v["pnl"]:+.3f}%')

print('\n--- BY EXIT REASON ---')
by = defaultdict(lambda: {'n':0,'pnl':0,'w':0})
for r in all_results:
    by[r['exit_reason']]['n']+=1; by[r['exit_reason']]['pnl']+=r['blended_pnl']
    if r['blended_pnl']>0: by[r['exit_reason']]['w']+=1
for k,v in sorted(by.items(), key=lambda x:-x[1]['n']):
    print(f'  {k:10s}: {v["n"]:3d} trades, WR={v["w"]/v["n"]:.0%}, P&L={v["pnl"]:+.3f}%')

print('\n--- BY SECTOR ---')
by = defaultdict(lambda: {'n':0,'pnl':0})
for r in all_results:
    by[r['sector']]['n']+=1; by[r['sector']]['pnl']+=r['blended_pnl']
for k,v in sorted(by.items(), key=lambda x:-x[1]['pnl']):
    print(f'  {k:10s}: {v["n"]:3d} trades, P&L={v["pnl"]:+.3f}%')

print('\n--- BY SCAN BAR ---')
by = defaultdict(lambda: {'n':0,'pnl':0,'w':0})
for r in all_results:
    by[r['scan_bar']]['n']+=1; by[r['scan_bar']]['pnl']+=r['blended_pnl']
    if r['blended_pnl']>0: by[r['scan_bar']]['w']+=1
for k,v in sorted(by.items()):
    print(f'  Bar {k:2d}: {v["n"]:3d} trades, WR={v["w"]/v["n"]:.0%}, P&L={v["pnl"]:+.3f}%')

print('\n--- PROFIT GAVE-BACK ---')
gave_back = [r for r in all_results if r['mfe_pct'] > 0.4 and r['pnl_pct'] < 0]
if gave_back:
    for r in gave_back:
        print(f'  {r["date"]} {r["symbol"]:12s} MFE=+{r["mfe_pct"]:.3f}% -> P&L={r["pnl_pct"]:+.3f}% exit={r["exit_reason"]}')
else:
    print('  NONE')

print('\n--- COMPARISON ---')
print(f'  v1 (no agents):    -0.411%')
print(f'  v2 (first agents): +1.510%')
print(f'  v3 (new signals):  +3.376%')
print(f'  v4 (this run):     {total_bl:+.3f}%')
print(f'  v4 weighted:       {weighted_pnl:+.3f}% (energy at 0.5x)')
