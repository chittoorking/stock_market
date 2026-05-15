"""
Trace v5: Full agent architecture.
- Data providers compute context
- LLM agent (rule-based fallback) reads ALL data and decides
- No hardcoded if/else in the pipeline itself
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
from app.agents.data_providers import build_full_context
from app.agents.llm_agent import agent_decide_with_llm
from app.agents.volatility import VolatilityAgent
from app.signals.base import get_sector

vol_agent = VolatilityAgent()
all_days = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))
all_results = []

for date in all_days:
    # Crowd count for GDR4
    gap_counts = {"up": 0, "down": 0, "total": 0}
    for symbol, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or not db: continue
        gap_counts["total"] += 1
        gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
        if gap > 0.3: gap_counts["up"] += 1
        elif gap < -0.3: gap_counts["down"] += 1
    crowd = max(gap_counts["up"], gap_counts["down"])

    day_positions = {}  # {symbol: {direction, entry, entry_bar, strategy}}

    # Scan at bars 6, 12, 15
    for scan_bar in [6, 12, 15]:
        # Generate all signals
        signals = []
        for symbol, bars in all_data.items():
            if symbol in day_positions: continue
            db = [b for b in bars if b['timestamp'][:10] == date]
            pb = [b for b in bars if b['timestamp'][:10] < date]
            if not pb or len(db) <= scan_bar: continue
            pc = pb[-1]['close']
            bsf = db[:scan_bar + 1]

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
                                          crowd_gap_count=crowd,
                                          total_symbols=gap_counts["total"])
                if sig: signals.append(sig)

        if not signals: continue

        # Build FULL context — all data in one string
        context = build_full_context(all_data, date, scan_bar, signals, day_positions)

        # Agent reads context and decides
        decision = agent_decide_with_llm(context)

        # Execute entries
        for entry in decision.entries:
            sym = entry["symbol"]
            if sym in day_positions or len(day_positions) >= 3:
                continue

            sym_bars = [b for b in all_data[sym] if b['timestamp'][:10] == date]
            if len(sym_bars) < scan_bar + 5: continue

            # Find the actual signal to get entry price
            sig_match = next((s for s in signals if s.symbol == sym and s.direction == entry["direction"]), None)
            if not sig_match: continue

            entry_price = sig_match.suggested_entry
            direction = entry["direction"]

            # Volatility-adjusted stops
            prev_all = [b for b in all_data[sym] if b['timestamp'][:10] <= date]
            vol_result = vol_agent.get_adjusted_stop(entry_price, direction, prev_all[-30:])

            day_positions[sym] = {
                "direction": direction, "entry": entry_price,
                "entry_bar": scan_bar, "strategy": entry["strategy"],
                "stop": vol_result["stop"], "target": vol_result["target"],
                "initial_risk": abs(entry_price - vol_result["stop"]),
                "score": entry.get("score", 0), "reason": entry.get("reason", ""),
            }

    # Now simulate the day bar-by-bar for all positions
    for sym, pos in list(day_positions.items()):
        sym_bars = [b for b in all_data[sym] if b['timestamp'][:10] == date]
        entry_price = pos["entry"]
        entry_bar = pos["entry_bar"]
        direction = pos["direction"]
        stop = pos["stop"]
        target = pos["target"]
        ir = pos["initial_risk"]

        max_fav = 0; cur_stop = stop; partial_taken = False; partial_pnl = 0
        exit_price = None; exit_reason = ''; exit_bar = 0

        for j in range(entry_bar + 1, len(sym_bars)):
            b = sym_bars[j]; bh = j - entry_bar
            if direction == 'LONG':
                pnl = (b['close'] - entry_price) / entry_price * 100
                fav = b['high'] - entry_price
            else:
                pnl = (entry_price - b['close']) / entry_price * 100
                fav = entry_price - b['low']
            max_fav = max(max_fav, fav)

            # Stop/target check
            if direction == 'LONG':
                if b['low'] <= cur_stop: exit_price = cur_stop; exit_reason = 'stop'; exit_bar = j; break
                if b['high'] >= target: exit_price = target; exit_reason = 'target'; exit_bar = j; break
            else:
                if b['high'] >= cur_stop: exit_price = cur_stop; exit_reason = 'stop'; exit_bar = j; break
                if b['low'] <= target: exit_price = target; exit_reason = 'target'; exit_bar = j; break

            # Agent re-evaluates every 6 bars (30 min)
            if bh % 6 == 0 and bh > 0:
                # Build position context for monitoring
                high_since = max(sb['high'] for sb in sym_bars[entry_bar:j+1])
                low_since = min(sb['low'] for sb in sym_bars[entry_bar:j+1])
                mfe_pct = max_fav / entry_price * 100
                fade = 0
                if max_fav > 0:
                    current_fav = (b['close'] - entry_price) if direction == 'LONG' else (entry_price - b['close'])
                    fade = max(0, (max_fav - max(0, current_fav)) / max_fav * 100)

                # Build monitoring context
                mon_positions = {sym: {
                    "direction": direction, "entry": entry_price, "entry_bar": entry_bar,
                }}
                mon_context = build_full_context(all_data, date, j, [], mon_positions)
                mon_decision = agent_decide_with_llm(mon_context)

                for pa in mon_decision.position_actions:
                    if pa["symbol"] == sym:
                        if pa["action"] == "close":
                            exit_price = b['close']; exit_reason = 'agent'; exit_bar = j; break
                        elif pa["action"] == "tighten":
                            # Lock in some profit
                            if pnl > 0:
                                new_stop = entry_price + max_fav * 0.25 if direction == 'LONG' else entry_price - max_fav * 0.25
                                cur_stop = max(cur_stop, new_stop) if direction == 'LONG' else min(cur_stop, new_stop) if new_stop < cur_stop else cur_stop
                        elif pa["action"] == "partial" and not partial_taken:
                            partial_taken = True; partial_pnl = pnl
                            # Move stop to breakeven
                            cur_stop = max(cur_stop, entry_price) if direction == 'LONG' else min(cur_stop, entry_price)

                if exit_price: break

        if exit_price is None:
            exit_price = sym_bars[-1]['close']; exit_reason = 'eod'; exit_bar = len(sym_bars) - 1

        if direction == 'LONG':
            final_pnl = (exit_price - entry_price) / entry_price * 100
        else:
            final_pnl = (entry_price - exit_price) / entry_price * 100

        blended = 0.30 * partial_pnl + 0.70 * final_pnl if partial_taken else final_pnl
        mfe_pct = max_fav / entry_price * 100

        all_results.append({
            'date': date, 'symbol': sym, 'direction': direction,
            'strategy': pos["strategy"], 'sector': get_sector(sym),
            'entry': entry_price, 'exit': exit_price, 'exit_reason': exit_reason,
            'pnl_pct': round(final_pnl, 3), 'blended_pnl': round(blended, 3),
            'mfe_pct': round(mfe_pct, 3), 'bars': exit_bar - entry_bar,
            'partial': partial_taken, 'score': pos.get("score", 0),
            'scan_bar': pos["entry_bar"], 'reason': pos.get("reason", ""),
        })

    day_positions.clear()

# ─── REPORT ───
print('=' * 120)
print(f'TRACE v5: AGENT ARCHITECTURE | Data providers -> Full context -> LLM reasoning -> Decisions')
print('=' * 120)
print(f'{"Date":>10} {"Symbol":>12} {"Dir":>5} {"Strat":>6} {"Sector":>8} {"Entry":>8} {"Exit":>8} {"P&L%":>7} {"Blend":>7} {"MFE%":>6} {"Bars":>4} {"P":>2} {"SB":>3} {"Exit":>8} {"Score":>5}')
print('-' * 120)

total_bl = 0; wins = 0; losses = 0
for r in all_results:
    w = 'W' if r['blended_pnl'] > 0 else 'L'
    if r['blended_pnl'] > 0: wins += 1
    else: losses += 1
    total_bl += r['blended_pnl']
    p = 'Y' if r['partial'] else ''
    print(f'{r["date"]:>10} {r["symbol"]:>12} {r["direction"]:>5} {r["strategy"]:>6} {r["sector"]:>8} {r["entry"]:8.2f} {r["exit"]:8.2f} {r["pnl_pct"]:+6.3f}% {r["blended_pnl"]:+6.3f}% {r["mfe_pct"]:5.3f}% {r["bars"]:4d} {p:>2} {r["scan_bar"]:3d} {r["exit_reason"]:>8} {r["score"]:5.2f} {w}')

n = max(len(all_results), 1)
print('-' * 120)
print(f'TOTAL: {len(all_results)} trades | W:{wins} L:{losses} | WR:{wins/n:.0%} | Blended:{total_bl:+.3f}% | Avg:{total_bl/n:+.3f}%/trade')

# Breakdowns
for label, key in [("STRATEGY", "strategy"), ("EXIT REASON", "exit_reason"), ("SECTOR", "sector"), ("SCAN BAR", "scan_bar")]:
    print(f'\n--- BY {label} ---')
    by = defaultdict(lambda: {'n':0,'pnl':0,'w':0})
    for r in all_results:
        by[r[key]]['n']+=1; by[r[key]]['pnl']+=r['blended_pnl']
        if r['blended_pnl']>0: by[r[key]]['w']+=1
    for k,v in sorted(by.items(), key=lambda x:-x[1]['pnl']):
        wr = v['w']/v['n'] if v['n'] else 0
        kstr = f'Bar {k}' if label == "SCAN BAR" else str(k)
        print(f'  {kstr:12s}: {v["n"]:3d} trades, WR={wr:.0%}, P&L={v["pnl"]:+.3f}%')

print('\n--- PROFIT GAVE-BACK ---')
gave_back = [r for r in all_results if r['mfe_pct'] > 0.4 and r['pnl_pct'] < 0]
for r in gave_back:
    print(f'  {r["date"]} {r["symbol"]:12s} MFE=+{r["mfe_pct"]:.3f}% -> P&L={r["pnl_pct"]:+.3f}% exit={r["exit_reason"]}')
if not gave_back:
    print('  NONE')

print('\n--- COMPARISON ---')
print(f'  v1 (no agents):     -0.411%')
print(f'  v2 (first agents):  +1.510%')
print(f'  v3 (new signals):   +3.376%')
print(f'  v4 (more signals):  +2.705%')
print(f'  v5 (agent arch):    {total_bl:+.3f}%')

# Show a few agent reasoning examples
print('\n--- AGENT REASONING SAMPLES ---')
for r in all_results[:3]:
    print(f'  {r["date"]} {r["symbol"]} [{r["strategy"]}]: {r["reason"][:80]}')
