"""
Trace v5 LLM: Real GPT-4o-mini making ALL trading decisions.
Same data providers, same context — but the LLM reads and reasons.
"""
import sys; sys.path.insert(0, '.')
import csv, json, time
from pathlib import Path
from collections import defaultdict

API_KEY = "REPLACE_WITH_YOUR_OPENAI_KEY"

data_dir = Path('C:/Users/HP/Downloads/Temp/Food Ordering All/stock/Stock Market Trading/data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

from app.signals.proven_strategies import GapAndGoSignal, ORBSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.agents.data_providers import build_full_context
from app.agents.llm_agent import agent_decide_with_llm, AGENT_SYSTEM_PROMPT
from app.agents.volatility import VolatilityAgent
from app.signals.base import get_sector

vol_agent = VolatilityAgent()
all_days = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))
all_results = []
all_reasoning = []
llm_calls = 0

for date in all_days:
    # Crowd count
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

    day_positions = {}

    for scan_bar in [6, 12, 15]:
        if len(day_positions) >= 3: break

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

        context = build_full_context(all_data, date, scan_bar, signals, day_positions)

        # REAL LLM CALL
        print(f"  [{date} bar {scan_bar}] Asking LLM with {len(signals)} signals...")
        decision = agent_decide_with_llm(context, api_key=API_KEY)
        llm_calls += 1
        time.sleep(0.5)  # Rate limit

        if decision.raw_reasoning:
            all_reasoning.append({
                "date": date, "bar": scan_bar,
                "signals": len(signals),
                "reasoning": decision.raw_reasoning[:500],
            })

        for entry in decision.entries:
            sym = entry["symbol"]
            if sym in day_positions or len(day_positions) >= 3: continue

            sym_bars = [b for b in all_data.get(sym, []) if b['timestamp'][:10] == date]
            if not sym_bars or len(sym_bars) < scan_bar + 5: continue

            sig_match = next((s for s in signals if s.symbol == sym and s.direction == entry["direction"]), None)
            if not sig_match: continue

            entry_price = sig_match.suggested_entry
            direction = entry["direction"]

            prev_all = [b for b in all_data.get(sym, []) if b['timestamp'][:10] <= date]
            vol_result = vol_agent.get_adjusted_stop(entry_price, direction, prev_all[-30:])

            day_positions[sym] = {
                "direction": direction, "entry": entry_price,
                "entry_bar": scan_bar, "strategy": entry.get("strategy", "llm"),
                "stop": vol_result["stop"], "target": vol_result["target"],
                "initial_risk": abs(entry_price - vol_result["stop"]),
                "reason": entry.get("reason", ""),
            }

    # Simulate positions
    for sym, pos in list(day_positions.items()):
        sym_bars = [b for b in all_data[sym] if b['timestamp'][:10] == date]
        entry_price = pos["entry"]; entry_bar = pos["entry_bar"]
        direction = pos["direction"]
        stop = pos["stop"]; target = pos["target"]
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

            if direction == 'LONG':
                if b['low'] <= cur_stop: exit_price = cur_stop; exit_reason = 'stop'; exit_bar = j; break
                if b['high'] >= target: exit_price = target; exit_reason = 'target'; exit_bar = j; break
            else:
                if b['high'] >= cur_stop: exit_price = cur_stop; exit_reason = 'stop'; exit_bar = j; break
                if b['low'] <= target: exit_price = target; exit_reason = 'target'; exit_bar = j; break

            # LLM monitors every 6 bars
            if bh % 6 == 0 and bh > 0:
                mon_pos = {sym: {"direction": direction, "entry": entry_price, "entry_bar": entry_bar}}
                mon_ctx = build_full_context(all_data, date, j, [], mon_pos)
                mon_dec = agent_decide_with_llm(mon_ctx, api_key=API_KEY)
                llm_calls += 1
                time.sleep(0.3)

                for pa in mon_dec.position_actions:
                    if pa.get("symbol") == sym:
                        if pa["action"] == "close":
                            exit_price = b['close']; exit_reason = 'llm_close'; exit_bar = j
                            break
                        elif pa["action"] == "tighten" and pnl > 0:
                            ir = pos["initial_risk"]
                            new_s = entry_price + max_fav * 0.25 if direction == 'LONG' else entry_price - max_fav * 0.25
                            cur_stop = max(cur_stop, new_s) if direction == 'LONG' else min(cur_stop, new_s)
                        elif pa["action"] == "partial" and not partial_taken:
                            partial_taken = True; partial_pnl = pnl
                            cur_stop = max(cur_stop, entry_price) if direction == 'LONG' else min(cur_stop, entry_price)
                if exit_price: break

        if exit_price is None:
            exit_price = sym_bars[-1]['close']; exit_reason = 'eod'; exit_bar = len(sym_bars) - 1

        final_pnl = ((exit_price - entry_price) / entry_price * 100) if direction == 'LONG' else ((entry_price - exit_price) / entry_price * 100)
        blended = 0.30 * partial_pnl + 0.70 * final_pnl if partial_taken else final_pnl
        mfe_pct = max_fav / entry_price * 100

        all_results.append({
            'date': date, 'symbol': sym, 'direction': direction,
            'strategy': pos["strategy"], 'sector': get_sector(sym),
            'entry': entry_price, 'exit': exit_price, 'exit_reason': exit_reason,
            'pnl_pct': round(final_pnl, 3), 'blended_pnl': round(blended, 3),
            'mfe_pct': round(mfe_pct, 3), 'bars': exit_bar - entry_bar,
            'partial': partial_taken, 'reason': pos.get("reason", ""),
        })
    day_positions.clear()

# ─── REPORT ───
print()
print('=' * 120)
print(f'TRACE v5 LLM: REAL GPT-4o-mini DECISIONS | {llm_calls} LLM calls')
print('=' * 120)
print(f'{"Date":>10} {"Symbol":>12} {"Dir":>5} {"Strat":>8} {"Sector":>8} {"Entry":>8} {"Exit":>8} {"P&L%":>7} {"Blend":>7} {"MFE%":>6} {"Bars":>4} {"P":>2} {"Exit":>10}')
print('-' * 120)

total_bl = 0; wins = 0; losses = 0
for r in all_results:
    w = 'W' if r['blended_pnl'] > 0 else 'L'
    if r['blended_pnl'] > 0: wins += 1
    else: losses += 1
    total_bl += r['blended_pnl']
    p = 'Y' if r['partial'] else ''
    print(f'{r["date"]:>10} {r["symbol"]:>12} {r["direction"]:>5} {r["strategy"]:>8} {r["sector"]:>8} {r["entry"]:8.2f} {r["exit"]:8.2f} {r["pnl_pct"]:+6.3f}% {r["blended_pnl"]:+6.3f}% {r["mfe_pct"]:5.3f}% {r["bars"]:4d} {p:>2} {r["exit_reason"]:>10} {w}')

n = max(len(all_results), 1)
print('-' * 120)
print(f'TOTAL: {len(all_results)} trades | W:{wins} L:{losses} | WR:{wins/n:.0%} | Blended:{total_bl:+.3f}% | Avg:{total_bl/n:+.3f}%/trade | LLM calls:{llm_calls}')

for label, key in [("STRATEGY", "strategy"), ("EXIT", "exit_reason"), ("SECTOR", "sector")]:
    print(f'\n--- BY {label} ---')
    by = defaultdict(lambda: {'n':0,'pnl':0,'w':0})
    for r in all_results:
        by[r[key]]['n']+=1; by[r[key]]['pnl']+=r['blended_pnl']
        if r['blended_pnl']>0: by[r[key]]['w']+=1
    for k,v in sorted(by.items(), key=lambda x:-x[1]['pnl']):
        wr = v['w']/v['n'] if v['n'] else 0
        print(f'  {str(k):12s}: {v["n"]:3d} trades, WR={wr:.0%}, P&L={v["pnl"]:+.3f}%')

print('\n--- PROFIT GAVE-BACK ---')
gb = [r for r in all_results if r['mfe_pct'] > 0.4 and r['pnl_pct'] < 0]
for r in gb: print(f'  {r["date"]} {r["symbol"]:12s} MFE=+{r["mfe_pct"]:.3f}% -> P&L={r["pnl_pct"]:+.3f}%')
if not gb: print('  NONE')

print('\n--- LLM REASONING ---')
for r in all_reasoning[:5]:
    print(f'\n  [{r["date"]} bar {r["bar"]}] {r["signals"]} signals:')
    try:
        parsed = json.loads(r["reasoning"])
        for e in parsed.get("entry_decisions", []):
            print(f'    TAKE: {e.get("direction","")} {e.get("symbol","")} [{e.get("strategy","")}] - {e.get("reason","")}')
        for s in parsed.get("skip_reasons", [])[:3]:
            print(f'    SKIP: {s}')
    except:
        print(f'    {r["reasoning"][:200]}')

print('\n--- COMPARISON ---')
print(f'  v1 (no agents):      -0.411%')
print(f'  v2 (first agents):   +1.510%')
print(f'  v3 (hardcoded):      +3.376%')
print(f'  v5 (rule fallback):  +1.504%')
print(f'  v5 LLM (this run):   {total_bl:+.3f}%')
