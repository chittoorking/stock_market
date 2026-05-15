"""
Walk-forward: Run pipeline on a window, then dissect every loss.
For each loss, show what the chart looked like at the stop-out moment
and what signal could have kept us in.
"""
import sys; sys.path.insert(0, '.')
import csv
from pathlib import Path
from collections import defaultdict

data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

dates = sorted(set(r['timestamp'][:10] for rows in all_data.values() for r in rows))

# Window from args
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--start', type=int, default=0)
parser.add_argument('--days', type=int, default=10)
args = parser.parse_args()

window = dates[args.start:args.start + args.days]
print(f'WINDOW: {window[0]} to {window[-1]} ({len(window)} days)')

from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.agents.volatility import VolatilityAgent
from app.agents.chart_narratives import NarrativeBuilder
from app.signals.base import get_sector, SECTOR_MAP

vol_agent = VolatilityAgent()
results = []

for date in window:
    # Simple: scan at bar 6, take top 3 by raw confidence, use vol-adjusted stops
    crowd = 0; total_syms = 0
    for sym, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or not db: continue
        total_syms += 1
        if abs((db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100) > 0.3: crowd += 1

    signals = []
    for sym, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or len(db) < 7: continue
        pc = pb[-1]['close']; bsf = db[:7]

        for scanner in [
            lambda: LenzSignal.scan(sym, bsf),
            lambda: AftershockSignal.scan(sym, bsf),
            lambda: GapDecaySignal.scan(sym, bsf, pc, crowd, total_syms) if len(bsf) >= 7 else None,
        ]:
            sig = scanner()
            if sig: signals.append(sig)

    # Sort by confidence, take top 3
    signals.sort(key=lambda s: -s.raw_confidence)
    taken_sectors = set()
    day_trades = []

    for sig in signals[:10]:
        if len(day_trades) >= 3: break
        sec = get_sector(sig.symbol)
        if sec in taken_sectors and sec != 'unknown': continue

        sym_bars = [b for b in all_data[sig.symbol] if b['timestamp'][:10] == date]
        prev_all = [b for b in all_data[sig.symbol] if b['timestamp'][:10] <= date]
        vol_r = vol_agent.get_adjusted_stop(sig.suggested_entry, sig.direction, prev_all[-30:])

        entry = sig.suggested_entry
        stop = vol_r['stop']
        target = vol_r['target']
        direction = sig.direction
        entry_bar = 6

        # Simulate
        max_fav = 0; exit_price = None; exit_reason = ''; exit_bar = 0
        for j in range(entry_bar + 1, len(sym_bars)):
            b = sym_bars[j]
            if direction == 'LONG':
                fav = b['high'] - entry
                if b['low'] <= stop: exit_price = stop; exit_reason = 'stop'; exit_bar = j; break
                if b['high'] >= target: exit_price = target; exit_reason = 'target'; exit_bar = j; break
            else:
                fav = entry - b['low']
                if b['high'] >= stop: exit_price = stop; exit_reason = 'stop'; exit_bar = j; break
                if b['low'] <= target: exit_price = target; exit_reason = 'target'; exit_bar = j; break
            max_fav = max(max_fav, fav)

        if not exit_price:
            exit_price = sym_bars[-1]['close']; exit_reason = 'eod'; exit_bar = len(sym_bars) - 1

        pnl = ((exit_price - entry) / entry * 100) if direction == 'LONG' else ((entry - exit_price) / entry * 100)
        mfe = max_fav / entry * 100

        day_trades.append({
            'date': date, 'symbol': sig.symbol, 'direction': direction,
            'strategy': sig.strategy_name, 'sector': sec,
            'entry': entry, 'stop': stop, 'target': target,
            'exit': exit_price, 'exit_reason': exit_reason,
            'pnl': round(pnl, 3), 'mfe': round(mfe, 3),
            'bars': exit_bar - entry_bar, 'entry_bar': entry_bar, 'exit_bar': exit_bar,
        })
        taken_sectors.add(sec)
        results.append(day_trades[-1])

# Summary
print(f'\n{"="*100}')
wins = [r for r in results if r['pnl'] > 0]
losses = [r for r in results if r['pnl'] <= 0]
total_pnl = sum(r['pnl'] for r in results)
print(f'Total: {len(results)} trades | W:{len(wins)} L:{len(losses)} | WR:{len(wins)/max(len(results),1):.0%} | P&L:{total_pnl:+.3f}%')

# Now dissect EVERY stop-out loss
stop_losses = [r for r in results if r['exit_reason'] == 'stop' and r['pnl'] < 0]
print(f'\n{"="*100}')
print(f'DISSECTING {len(stop_losses)} STOP-OUT LOSSES')
print(f'{"="*100}')

for r in stop_losses:
    sym = r['symbol']
    date = r['date']
    sb = [b for b in all_data[sym] if b['timestamp'][:10] == date]
    entry = r['entry']; stop = r['stop']; target = r['target']
    direction = r['direction']; entry_bar = r['entry_bar']; exit_bar = r['exit_bar']

    print(f'\n--- {sym} {direction} {date} | P&L={r["pnl"]:+.3f}% | MFE={r["mfe"]:.3f}% ---')
    print(f'  Entry: {entry:.2f} | Stop: {stop:.2f} | Target: {target:.2f}')

    # What was the nearest swing low/high BEFORE entry?
    pre_entry = sb[:entry_bar+1]
    if direction == 'LONG':
        swing_lows = sorted(set(b['low'] for b in pre_entry))[:3]
        nearest_swing = max(sl for sl in swing_lows if sl < entry) if any(sl < entry for sl in swing_lows) else stop
        print(f'  Swing lows: {[round(s,2) for s in swing_lows]}')
        print(f'  ATR stop: {stop:.2f} | Swing low stop: {nearest_swing:.2f} | Diff: {(nearest_swing-stop)/entry*100:+.3f}%')
    else:
        swing_highs = sorted(set(b['high'] for b in pre_entry), reverse=True)[:3]
        nearest_swing = min(sh for sh in swing_highs if sh > entry) if any(sh > entry for sh in swing_highs) else stop
        print(f'  Swing highs: {[round(s,2) for s in swing_highs]}')
        print(f'  ATR stop: {stop:.2f} | Swing high stop: {nearest_swing:.2f}')

    # If stop was at swing level instead, would we have survived?
    survived = True
    for j in range(entry_bar + 1, min(exit_bar + 20, len(sb))):
        b = sb[j]
        if direction == 'LONG' and b['low'] <= nearest_swing:
            survived = False; break
        if direction == 'SHORT' and b['high'] >= nearest_swing:
            survived = False; break

    # What happened after our stop was hit?
    post_stop = sb[exit_bar:min(exit_bar+20, len(sb))]
    if post_stop:
        if direction == 'LONG':
            post_max = max(b['high'] for b in post_stop)
            post_move = (post_max - entry) / entry * 100
        else:
            post_min = min(b['low'] for b in post_stop)
            post_move = (entry - post_min) / entry * 100
        hit_target = post_max >= target if direction == 'LONG' else post_min <= target
    else:
        post_move = 0; hit_target = False

    # Chart at the stop moment
    narratives = NarrativeBuilder.build(sb, exit_bar, direction, entry)
    narr_text = '; '.join(f'{n.setup_type}({n.direction_bias})' for n in narratives) if narratives else 'none'

    # EMA at stop moment
    closes = [b['close'] for b in sb[:exit_bar+1]]
    ema9 = sum(closes[-9:])/9 if len(closes) >= 9 else 0
    ema21 = sum(closes[-21:])/21 if len(closes) >= 21 else 0

    print(f'  After stop: price went {post_move:+.3f}% | Hit target: {hit_target}')
    print(f'  Swing stop would survive: {survived}')
    print(f'  Chart at stop: {narr_text}')
    print(f'  EMA at stop: 9={ema9:.2f} 21={ema21:.2f} price={sb[exit_bar]["close"]:.2f}')

    # THE LEARNING
    if hit_target and not survived:
        print(f'  LEARNING: Even swing stop would not help. Need to skip this trade entirely.')
    elif hit_target and survived:
        print(f'  LEARNING: SWING STOP WOULD HAVE SAVED THIS. ATR stop too tight by {abs(nearest_swing-stop)/entry*100:.3f}%')
    elif not hit_target:
        print(f'  LEARNING: Trade was wrong. Stop was correct to cut it. Signal quality issue.')

    # What ADDITIONAL signal at entry would have warned us?
    entry_b = sb[entry_bar]
    prev_b = sb[entry_bar - 1] if entry_bar > 0 else entry_b
    vol_spike = (entry_b['volume'] - prev_b['volume']) / prev_b['volume'] * 100 if prev_b['volume'] > 0 else 0
    entry_body = abs(entry_b['close'] - entry_b['open'])
    entry_range = entry_b['high'] - entry_b['low']
    entry_body_ratio = entry_body / entry_range * 100 if entry_range > 0 else 0

    # RSI at entry
    if len(closes) >= 15:
        gains = [max(0, closes[i]-closes[i-1]) for i in range(1,len(closes))]
        loss_l = [max(0, closes[i-1]-closes[i]) for i in range(1,len(closes))]
        ag = sum(gains[-14:])/14; al = sum(loss_l[-14:])/14
        rsi = 100 - 100/(1+ag/al) if al > 0 else 100
    else:
        rsi = 50

    # Sector at entry
    from app.agents.data_providers import SectorAnalyzer
    sec_data = SectorAnalyzer.compute(all_data, date, entry_bar)
    sec_info = sec_data.get(r['sector'], {})
    leader_chg = sec_info.get('leader_change_pct', 0)
    strength = sec_info.get('strength', 0)

    print(f'  Entry signals: vol_spike={vol_spike:+.0f}% body={entry_body_ratio:.0f}% RSI={rsi:.0f} sector={leader_chg:+.2f}%({strength:.0%})')
