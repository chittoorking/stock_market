"""
Run o3-mini reasoning + Verifier + MTF pipeline with parallel day processing.

Each day runs independently in its own thread.
o3-mini does chain-of-thought reasoning for all agents.

Usage:
    python run_reasoning.py                    # Last 10 days
    python run_reasoning.py --start -3 --days 3
"""
import sys; sys.path.insert(0, '.')
import csv, json, time, copy
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

API_KEY = "REPLACE_WITH_YOUR_OPENAI_KEY"

data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.agents.data_providers import SectorAnalyzer, VolumeProfiler, PriceStructure
from app.agents.graph_llms import risk_with_graph, executor_with_graph, judge_with_graph
from app.agents.crewai_debate import debate_scanner, debate_monitor
from app.agents.volatility import VolatilityAgent
from app.core.trade_dev_graph import TradeDevGraph, TradeNode, BarSnapshot
from app.core.market_memory import MarketMemory, DayMemory
from app.core.signal_enrichment import enrich_all_signals, format_enriched_signals
from app.core.setup_development import observe_setup_to_graph
from app.core.multi_timeframe import write_mtf_to_graph
from app.signals.base import get_sector, SECTOR_MAP

import argparse
_parser = argparse.ArgumentParser()
_parser.add_argument('--start', type=int, default=-10)
_parser.add_argument('--days', type=int, default=10)
_parser.add_argument('--workers', type=int, default=3, help='Parallel days')
_args, _ = _parser.parse_known_args()

all_dates = sorted(set(r['timestamp'][:10] for rows in all_data.values() for r in rows))
if _args.start < 0:
    test_dates = all_dates[_args.start:_args.start + _args.days] if _args.start + _args.days < 0 else all_dates[_args.start:]
else:
    test_dates = all_dates[_args.start:_args.start + _args.days]
print(f"o3-mini REASONING | {len(test_dates)} days | {_args.workers} parallel workers")
print(f"Dates: {test_dates[0]} to {test_dates[-1]} | Stocks: {len(all_data)}")


def run_single_day(date, prev_dates_memory):
    """Run one day independently. Returns list of trade results."""
    vol_agent = VolatilityAgent()
    trade_graph = TradeDevGraph()
    memory = MarketMemory()
    # Seed memory from previous days (passed in)
    for pm in prev_dates_memory:
        memory.record_day(pm)

    results = []
    llm_calls = 0

    # Crowd count
    crowd = 0; total_syms = 0
    for sym, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or not db: continue
        total_syms += 1
        gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
        if abs(gap) > 0.3: crowd += 1

    for scan_bar in [6, 15]:
        if len(trade_graph.get_all_active()) >= 3: break

        signals = []
        for sym, bars in all_data.items():
            if trade_graph.get_active(sym): continue
            db = [b for b in bars if b['timestamp'][:10] == date]
            pb = [b for b in bars if b['timestamp'][:10] < date]
            if not pb or len(db) <= scan_bar: continue
            pc = pb[-1]['close']; bsf = db[:scan_bar+1]

            for scanner in [
                lambda: GapAndGoSignal.scan(sym, bsf[:3], pc),
                lambda: LenzSignal.scan(sym, bsf) if len(bsf)>=2 else None,
                lambda: AftershockSignal.scan(sym, bsf) if len(bsf)>=2 else None,
                lambda: GapDecaySignal.scan(sym, bsf, pc, crowd, total_syms) if len(bsf)>=7 and scan_bar==6 else None,
            ]:
                sig = scanner()
                if sig: signals.append(sig)

        if not signals: continue

        # Enrichment
        enrichments = enrich_all_signals(signals, all_data, date, scan_bar, memory)
        red_count = sum(1 for sig in signals if enrichments.get(sig.id) and enrichments[sig.id].red_flag_count >= 1)

        sector_ctx = SectorAnalyzer.compute(all_data, date, scan_bar)
        signals_text = format_enriched_signals(signals, enrichments)
        sector_text = "\n".join(
            f"{sn}: {sd['leader']} {sd['leader_change_pct']:+.2f}%, {sd['laggards_following']}/{sd['laggards_total']} following"
            for sn, sd in sorted(sector_ctx.items(), key=lambda x: -abs(x[1]['leader_change_pct']))
        )

        # Write setup + MTF events to graph
        trade_graph.clear_signal_events()
        for sig in signals[:10]:
            observe_setup_to_graph(sig.symbol, sig.direction, sig.strategy_name,
                                  all_data, date, scan_bar, trade_graph)
            write_mtf_to_graph(all_data, sig.symbol, sig.direction, date, scan_bar, trade_graph)

        setup_facts = []
        for sig in signals[:10]:
            facts = trade_graph.query_signal_development(sig.symbol)
            if facts and "No development" not in facts:
                setup_facts.append(f"{sig.direction} {sig.symbol} [{sig.strategy_name}]:\n{facts}")
        setup_text = "\n".join(setup_facts) if setup_facts else ""

        # DEBATE with o3-mini reasoning
        ranked, trace = debate_scanner(signals_text, sector_text, memory, API_KEY,
                                       setup_verification=setup_text, reasoning=True)
        llm_calls += 4

        if not ranked: continue
        for r in ranked: r["sector"] = get_sector(r.get("symbol", ""))

        # Risk (still gpt-4o-mini — fast enough)
        risk_result = risk_with_graph(ranked, trade_graph, memory, API_KEY)
        llm_calls += 1
        approved = risk_result.get("approved", [])
        if not approved: continue

        # Executor
        price_lines = []
        for a in approved:
            s = a.get("symbol", "")
            sb = [b for b in all_data.get(s,[]) if b['timestamp'][:10]==date]
            pa = [b for b in all_data.get(s,[]) if b['timestamp'][:10]<=date]
            if sb and len(sb)>scan_bar:
                p = PriceStructure.compute(sb, scan_bar, pa[-2]['close'] if len(pa)>=2 else None)
                price_lines.append(f"{s}: price={p.get('price',0)} ATR={p.get('atr',0)}({p.get('atr_pct',0)}%) gap={p.get('gap_pct',0)}%")
        trades = executor_with_graph(approved, "\n".join(price_lines), API_KEY)
        llm_calls += 1

        opened_sectors = set()
        for t in trades:
            sym = t.get("symbol","")
            if trade_graph.get_active(sym) or len(trade_graph.get_all_active())>=3: continue
            sig = next((s for s in signals if s.symbol==sym), None)
            if not sig: continue
            sym_sector = get_sector(sym)
            if sym_sector in opened_sectors: continue
            opened_sectors.add(sym_sector)

            entry_price = sig.suggested_entry
            direction = t.get("direction", sig.direction)

            from app.agents.smart_stops import SmartStopCalculator
            sb_for_stop = [b for b in all_data.get(sym,[]) if b['timestamp'][:10]==date]
            pa = [b for b in all_data.get(sym,[]) if b['timestamp'][:10]<=date]
            atr = vol_agent.calculate_atr(pa[-30:]) if len(pa) >= 5 else 0
            stop, _ = SmartStopCalculator.calculate(sb_for_stop, scan_bar, entry_price, direction, atr)
            target, _ = SmartStopCalculator.calculate_target(entry_price, stop, direction, 2.5)

            llm_stop = t.get("stop", 0); llm_target = t.get("target", 0)
            if llm_stop and abs(llm_stop - entry_price) / entry_price * 100 > 0.20:
                if direction == "LONG": stop = min(stop, llm_stop)
                else: stop = max(stop, llm_stop)
            if llm_target:
                if direction == "LONG" and llm_target > entry_price: target = llm_target
                elif direction == "SHORT" and llm_target < entry_price: target = llm_target

            strat = t.get("strategy", sig.strategy_name)
            sec_data = sector_ctx.get(get_sector(sym), {})
            sb = [b for b in all_data.get(sym,[]) if b['timestamp'][:10]==date]
            p_struct = PriceStructure.compute(sb, scan_bar, None)

            node = TradeNode(
                trade_id=f"{date}-{sym}", symbol=sym, direction=direction,
                strategy=strat, sector=get_sector(sym),
                entry_price=entry_price, entry_bar=scan_bar,
                entry_signal_confidence=sig.raw_confidence,
                entry_sector_flow=sec_data.get("leader_change_pct", 0),
                entry_sector_strength=sec_data.get("strength", 0),
                entry_atr_pct=p_struct.get("atr_pct", 0),
            )
            node.stop = stop; node.target = target
            trade_graph.open_trade(node)

    # Simulate positions
    for sym, node in list(trade_graph.get_all_active().items()):
        sb = [b for b in all_data.get(sym,[]) if b['timestamp'][:10]==date]
        if not sb: continue
        entry_price = node.entry_price; entry_bar = node.entry_bar
        direction = node.direction; stop = node.stop; target = node.target
        max_fav = 0; cur_stop = stop
        high_since = entry_price; low_since = entry_price
        exit_price = None; exit_reason = ''; exit_bar = 0
        partial_taken = False; partial_pnl = 0

        for j in range(entry_bar+1, len(sb)):
            b = sb[j]; bh = j - entry_bar
            high_since = max(high_since, b['high']); low_since = min(low_since, b['low'])
            if direction=='LONG':
                pnl = (b['close']-entry_price)/entry_price*100; fav = b['high']-entry_price
            else:
                pnl = (entry_price-b['close'])/entry_price*100; fav = entry_price-b['low']
            max_fav = max(max_fav, fav)
            mfe = max_fav/entry_price*100

            # Hard stops/targets
            if direction=='LONG':
                if b['low']<=cur_stop: exit_price=cur_stop; exit_reason='stop'; exit_bar=j; break
                if b['high']>=target: exit_price=target; exit_reason='target'; exit_bar=j; break
            else:
                if b['high']>=cur_stop: exit_price=cur_stop; exit_reason='stop'; exit_bar=j; break
                if b['low']<=target: exit_price=target; exit_reason='target'; exit_bar=j; break

            # Monitor every 6 bars (still gpt-4o-mini for speed)
            if bh % 6 == 0 and bh > 0:
                from app.agents.candle_patterns import detect_patterns, format_patterns_for_graph
                from app.agents.chart_narratives import NarrativeBuilder
                candle_pats = detect_patterns(sb, j)
                candle_text = format_patterns_for_graph(candle_pats)
                narratives = NarrativeBuilder.build(sb, j, direction, entry_price)
                narrative_text = NarrativeBuilder.format_for_llm(narratives, direction)
                full_tech = f"{candle_text}\n\n{narrative_text}"

                mon, _ = debate_monitor(node, memory, API_KEY, technical_data=full_tech)
                llm_calls += 3
                act = mon.get("action","hold")
                if act=="close":
                    exit_price=b['close']; exit_reason='monitor'; exit_bar=j; break
                elif act=="tighten" and pnl>0:
                    ns = entry_price+max_fav*0.3 if direction=='LONG' else entry_price-max_fav*0.3
                    cur_stop = max(cur_stop,ns) if direction=='LONG' else min(cur_stop,ns)
                elif act=="partial" and not partial_taken:
                    partial_taken=True; partial_pnl=pnl
                    cur_stop = max(cur_stop,entry_price) if direction=='LONG' else min(cur_stop,entry_price)

        if not exit_price:
            exit_price=sb[-1]['close']; exit_reason='eod'; exit_bar=len(sb)-1

        closed = trade_graph.close_trade(sym, exit_price, exit_bar, exit_reason)
        if closed:
            blended = 0.3*partial_pnl + 0.7*closed.final_pnl_pct if partial_taken else closed.final_pnl_pct
            results.append({
                'date': date, 'symbol': sym, 'direction': direction,
                'strategy': node.strategy, 'sector': node.sector,
                'entry': entry_price, 'exit': exit_price, 'exit_reason': exit_reason,
                'pnl': round(closed.final_pnl_pct,3), 'blended': round(blended,3),
                'mfe': round(closed.max_mfe_pct,3), 'bars': closed.bars_held,
                'partial': partial_taken,
            })

    return date, results, llm_calls


# === RUN ALL DAYS IN PARALLEL ===
print(f"\nStarting {len(test_dates)} days with {_args.workers} workers...")

all_results = []
total_llm_calls = 0

with ThreadPoolExecutor(max_workers=_args.workers) as executor:
    futures = {}
    for date in test_dates:
        # Each day gets empty memory (no cross-day leakage in parallel)
        f = executor.submit(run_single_day, date, [])
        futures[f] = date

    for future in as_completed(futures):
        date, day_results, day_llm = future.result()
        all_results.extend(day_results)
        total_llm_calls += day_llm
        day_pnl = sum(r['blended'] for r in day_results)
        print(f"  Day {date}: {len(day_results)} trades, P&L={day_pnl:+.3f}%, LLM calls={day_llm}")

# Sort by date
all_results.sort(key=lambda r: r['date'])

# Report
print()
print('='*100)
print(f'o3-mini REASONING | {len(test_dates)} DAYS | {total_llm_calls} LLM calls | {_args.workers} workers')
print('='*100)
print(f'{"Date":>10} {"Symbol":>12} {"Dir":>5} {"Strat":>6} {"Entry":>8} {"Exit":>8} {"P&L%":>7} {"Blend":>7} {"MFE%":>6} {"Bars":>4} {"Exit":>8}')
print('-'*100)
total=0; wins=0; losses=0
for r in all_results:
    w='W' if r['blended']>0 else 'L'
    if r['blended']>0: wins+=1
    else: losses+=1
    total+=r['blended']
    p='Y' if r['partial'] else ' '
    print(f'{r["date"]:>10} {r["symbol"]:>12} {r["direction"]:>5} {r["strategy"]:>6} {r["entry"]:8.2f} {r["exit"]:8.2f} {r["pnl"]:+6.3f}% {r["blended"]:+6.3f}% {r["mfe"]:5.3f}% {r["bars"]:4d} {r["exit_reason"]:>8} {w}{p}')
n=max(len(all_results),1)
print('-'*100)
print(f'TOTAL: {n} trades | W:{wins} L:{losses} | WR:{wins/n:.0%} | Blended:{total:+.3f}% | Avg:{total/n:+.3f}%')

for label,key in [("STRATEGY","strategy"),("EXIT","exit_reason"),("SECTOR","sector")]:
    print(f'\n--- {label} ---')
    by=defaultdict(lambda:{'n':0,'p':0,'w':0})
    for r in all_results:
        by[r[key]]['n']+=1; by[r[key]]['p']+=r['blended']
        if r['blended']>0: by[r[key]]['w']+=1
    for k,v in sorted(by.items(),key=lambda x:-x[1]['p']):
        print(f'  {str(k):12s}: {v["n"]:3d} trades, WR={v["w"]/v["n"]:.0%}, P&L={v["p"]:+.3f}%')

print(f'\n--- COMPARISON ---')
print(f'  Baseline (one-shot):          57% WR, ~break-even')
print(f'  CrewAI debate (gpt-4o-mini):  57% WR, +3.016%')
print(f'  o3-mini reasoning (this):     {wins/n:.0%} WR, {total:+.3f}%')
