"""
Trace v6: Multi-LLM architecture.
Scanner → Risk → Executor → Monitor → Judge
Each LLM has ONE job. No cross-contamination.
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
from app.agents.data_providers import build_full_context, SectorAnalyzer, VolumeProfiler, PriceStructure, PositionTracker
from app.agents.specialized_llms import scanner_rank, risk_check, executor_set_params, monitor_position, judge_trade
from app.agents.volatility import VolatilityAgent
from app.signals.base import get_sector, SECTOR_MAP

vol_agent = VolatilityAgent()
all_days = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))
all_results = []
all_journals = []
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

    for scan_bar in [6, 15]:
        if len(day_positions) >= 3: break

        # Step 0: Generate raw signals
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
                                          crowd_gap_count=crowd, total_symbols=gap_counts["total"])
                if sig: signals.append(sig)

        if not signals: continue

        # Step 1: SCANNER LLM — ranks signals (sees market context + signals only)
        context = build_full_context(all_data, date, scan_bar, signals, day_positions)
        print(f"  [{date} bar {scan_bar}] Scanner: {len(signals)} signals...")
        ranked = scanner_rank(context, API_KEY)
        llm_calls += 1; time.sleep(0.3)

        if not ranked:
            print(f"    Scanner returned nothing")
            continue

        print(f"    Scanner ranked {len(ranked)} signals (score >= 6)")
        for r in ranked[:3]:
            print(f"      {r.get('score',0)}/10 {r.get('direction','')} {r.get('symbol','')} [{r.get('strategy','')}] - {r.get('reason','')[:60]}")

        # Step 2: RISK LLM — vetoes unsafe ones (sees ranked + portfolio only)
        portfolio_str = "None" if not day_positions else json.dumps(
            {s: {"direction": p["direction"], "sector": get_sector(s)} for s, p in day_positions.items()}, indent=2)
        risk_result = risk_check(json.dumps(ranked, indent=2), portfolio_str, API_KEY)
        llm_calls += 1; time.sleep(0.3)

        approved = risk_result.get("approved", [])
        vetoed = risk_result.get("vetoed", [])
        print(f"    Risk: {len(approved)} approved, {len(vetoed)} vetoed")
        for v in vetoed[:2]:
            print(f"      VETO: {v.get('symbol','')} - {v.get('reason','')[:60]}")

        if not approved: continue

        # Step 3: EXECUTOR LLM — sets params (sees approved + ATR data only)
        price_lines = []
        for a in approved:
            sym = a.get("symbol", "")
            sym_bars = [b for b in all_data.get(sym, []) if b['timestamp'][:10] == date]
            prev_all = [b for b in all_data.get(sym, []) if b['timestamp'][:10] <= date]
            if sym_bars and len(sym_bars) > scan_bar:
                p = PriceStructure.compute(sym_bars, scan_bar,
                    prev_all[-2]["close"] if len(prev_all) >= 2 else None)
                v = VolumeProfiler.compute(sym_bars, scan_bar)
                price_lines.append(f"{sym}: price={p.get('price',0)}, ATR={p.get('atr',0)} ({p.get('atr_pct',0)}%), "
                                   f"VWAP={p.get('vwap',0)} ({p.get('vwap_position','')}), gap={p.get('gap_pct',0)}%, "
                                   f"RVOL={v.get('rvol_opening',1)}, vol_trend={v.get('volume_trend_6bar',0)}%")

        trades = executor_set_params(json.dumps(approved, indent=2), "\n".join(price_lines), API_KEY)
        llm_calls += 1; time.sleep(0.3)

        print(f"    Executor: {len(trades)} trades parameterized")

        # Open positions
        for trade in trades:
            sym = trade.get("symbol", "")
            if sym in day_positions or len(day_positions) >= 3: continue

            sig_match = next((s for s in signals if s.symbol == sym), None)
            if not sig_match: continue

            entry_price = sig_match.suggested_entry
            direction = trade.get("direction", sig_match.direction)
            stop = trade.get("stop", 0)
            target = trade.get("target", 0)

            # Fallback if LLM didn't set valid stop/target
            if stop == 0 or target == 0:
                prev_all = [b for b in all_data.get(sym, []) if b['timestamp'][:10] <= date]
                vol_r = vol_agent.get_adjusted_stop(entry_price, direction, prev_all[-30:])
                stop = stop or vol_r["stop"]
                target = target or vol_r["target"]

            strategy = trade.get("strategy", next((a.get("strategy","") for a in approved if a.get("symbol")==sym), "llm"))

            day_positions[sym] = {
                "direction": direction, "entry": entry_price,
                "entry_bar": scan_bar, "strategy": strategy,
                "stop": stop, "target": target,
                "initial_risk": abs(entry_price - stop),
                "reason": trade.get("reason", ""),
            }
            print(f"      OPEN: {direction} {sym} @ {entry_price:.2f} SL={stop:.2f} TP={target:.2f}")

    # Simulate positions bar by bar
    for sym, pos in list(day_positions.items()):
        sym_bars = [b for b in all_data[sym] if b['timestamp'][:10] == date]
        entry_price = pos["entry"]; entry_bar = pos["entry_bar"]
        direction = pos["direction"]
        stop = pos["stop"]; target = pos["target"]
        max_fav = 0; cur_stop = stop; partial_taken = False; partial_pnl = 0
        high_since = entry_price; low_since = entry_price
        exit_price = None; exit_reason = ''; exit_bar = 0

        for j in range(entry_bar + 1, len(sym_bars)):
            b = sym_bars[j]; bh = j - entry_bar
            high_since = max(high_since, b['high'])
            low_since = min(low_since, b['low'])
            if direction == 'LONG':
                pnl = (b['close'] - entry_price) / entry_price * 100
                fav = b['high'] - entry_price
            else:
                pnl = (entry_price - b['close']) / entry_price * 100
                fav = entry_price - b['low']
            max_fav = max(max_fav, fav)

            # Hard stops
            if direction == 'LONG':
                if b['low'] <= cur_stop: exit_price = cur_stop; exit_reason = 'stop'; exit_bar = j; break
                if b['high'] >= target: exit_price = target; exit_reason = 'target'; exit_bar = j; break
            else:
                if b['high'] >= cur_stop: exit_price = cur_stop; exit_reason = 'stop'; exit_bar = j; break
                if b['low'] <= target: exit_price = target; exit_reason = 'target'; exit_bar = j; break

            # Step 4: MONITOR LLM every 6 bars (sees THIS position only)
            if bh % 6 == 0 and bh > 0:
                pt = PositionTracker.compute(entry_price, direction, b['close'], high_since, low_since, bh)
                sector_data = SectorAnalyzer.compute(all_data, date, j)
                sym_sector = get_sector(sym)
                sec_info = sector_data.get(sym_sector, {})
                vol_data = VolumeProfiler.compute(sym_bars, j)

                mon_ctx = (
                    f"POSITION: {direction} {sym} ({sym_sector})\n"
                    f"Entry: {entry_price:.2f} | Current: {b['close']:.2f} | P&L: {pt['pnl_pct']:+.3f}%\n"
                    f"MFE (peak profit): {pt['mfe_pct']:.3f}% | Fade: {pt['fade_pct']:.0f}% given back\n"
                    f"MAE (max adverse): {pt['mae_pct']:.3f}% | Zone: {pt['zone']} | Held: {pt['minutes_held']}min\n"
                    f"Stop: {cur_stop:.2f} | Target: {target:.2f}\n"
                    f"Sector {sym_sector}: leader {sec_info.get('leader','')} {sec_info.get('leader_change_pct',0):+.2f}%, "
                    f"{sec_info.get('laggards_following',0)}/{sec_info.get('laggards_total',0)} following\n"
                    f"Volume: {vol_data.get('divergence','none')}\n"
                )

                mon_result = monitor_position(mon_ctx, API_KEY)
                llm_calls += 1; time.sleep(0.2)

                action = mon_result.get("action", "hold")
                reason = mon_result.get("reason", "")

                if action == "close":
                    exit_price = b['close']; exit_reason = 'monitor'; exit_bar = j
                    print(f"    MONITOR CLOSE {sym}: {reason}")
                    break
                elif action == "tighten" and pnl > 0:
                    new_s = entry_price + max_fav * 0.3 if direction == 'LONG' else entry_price - max_fav * 0.3
                    if direction == 'LONG':
                        cur_stop = max(cur_stop, new_s)
                    else:
                        cur_stop = min(cur_stop, new_s)
                elif action == "partial" and not partial_taken:
                    partial_taken = True; partial_pnl = pnl
                    if direction == 'LONG':
                        cur_stop = max(cur_stop, entry_price)
                    else:
                        cur_stop = min(cur_stop, entry_price)

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

        # Step 5: JUDGE LLM — reviews this trade (stored, never fed back)
        trade_summary = (
            f"TRADE: {direction} {sym} [{pos['strategy']}]\n"
            f"Entry: {entry_price:.2f} | Exit: {exit_price:.2f} ({exit_reason})\n"
            f"P&L: {final_pnl:+.3f}% | MFE: {mfe_pct:.3f}% | Bars held: {exit_bar - entry_bar}\n"
            f"Sector: {get_sector(sym)} | Partial taken: {partial_taken}\n"
        )
        journal = judge_trade(trade_summary, API_KEY)
        llm_calls += 1; time.sleep(0.2)
        journal["symbol"] = sym; journal["date"] = date; journal["pnl"] = final_pnl
        all_journals.append(journal)

    day_positions.clear()

# ─── REPORT ───
print()
print('=' * 120)
print(f'TRACE v6: MULTI-LLM | Scanner->Risk->Executor->Monitor->Judge | {llm_calls} LLM calls')
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
print(f'TOTAL: {len(all_results)} trades | W:{wins} L:{losses} | WR:{wins/n:.0%} | Blended:{total_bl:+.3f}% | Avg:{total_bl/n:+.3f}%/trade | LLM:{llm_calls}')

for label, key in [("STRATEGY", "strategy"), ("EXIT", "exit_reason"), ("SECTOR", "sector")]:
    print(f'\n--- BY {label} ---')
    by = defaultdict(lambda: {'n':0,'pnl':0,'w':0})
    for r in all_results:
        by[r[key]]['n']+=1; by[r[key]]['pnl']+=r['blended_pnl']
        if r['blended_pnl']>0: by[r[key]]['w']+=1
    for k,v in sorted(by.items(), key=lambda x:-x[1]['pnl']):
        wr = v['w']/v['n'] if v['n'] else 0
        print(f'  {str(k):12s}: {v["n"]:3d} trades, WR={wr:.0%}, P&L={v["pnl"]:+.3f}%')

print('\n--- TRADE JOURNAL (Judge LLM reviews) ---')
for j in all_journals:
    emoji = '+' if j.get('pnl', 0) > 0 else '-'
    print(f'  {emoji} {j.get("date","")} {j.get("symbol",""):12s} ({j.get("pnl",0):+.3f}%)')
    if j.get("went_right"): print(f'    Right: {j["went_right"][:80]}')
    if j.get("went_wrong"): print(f'    Wrong: {j["went_wrong"][:80]}')
    if j.get("lesson"): print(f'    Lesson: {j["lesson"][:80]}')

print('\n--- COMPARISON ---')
print(f'  v1 (no agents):       -0.411%')
print(f'  v3 (hardcoded):       +3.376%')
print(f'  v5 (single LLM):     -2.058%')
print(f'  v5 (rule fallback):   +1.504%')
print(f'  v6 (multi-LLM):      {total_bl:+.3f}%')
