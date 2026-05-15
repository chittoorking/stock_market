"""
Trace v7: Graph-backed multi-LLM.
System writes → Graphs store → LLMs query → LLMs decide → System executes → Graphs update
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

from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.agents.data_providers import build_full_context, SectorAnalyzer, VolumeProfiler, PriceStructure
from app.agents.graph_llms import scanner_with_graph, risk_with_graph, executor_with_graph, monitor_with_graph, judge_with_graph
from app.agents.volatility import VolatilityAgent
from app.core.trade_dev_graph import TradeDevGraph, TradeNode, BarSnapshot
from app.core.market_memory import MarketMemory, DayMemory
from app.signals.base import get_sector

vol_agent = VolatilityAgent()
trade_graph = TradeDevGraph()
memory = MarketMemory()

all_days = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))
all_results = []
llm_calls = 0

for date in all_days:
    day_pnl = 0; day_trades = 0; day_best = ""; day_worst = ""

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

        # Generate signals
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

        # ENRICHMENT: system computes data quality flags — ALL signals annotated, NONE blocked
        # The LLM sees everything. Red flags are DATA, not filters.
        from app.core.signal_enrichment import enrich_all_signals, format_enriched_signals
        enrichments = enrich_all_signals(signals, all_data, date, scan_bar, memory)

        red_flagged = sum(1 for sig in signals if enrichments.get(sig.id, None) and enrichments[sig.id].red_flag_count >= 1)
        if red_flagged:
            print(f"  [{date} bar {scan_bar}] {red_flagged} signals have red flags (LLM sees them as data)")

        # Build context with ALL signals + enrichment flags — LLM decides
        sector_ctx = SectorAnalyzer.compute(all_data, date, scan_bar)
        signals_text = format_enriched_signals(signals, enrichments)
        sector_text = "\n".join(
            f"{sn}: {sd['leader']} {sd['leader_change_pct']:+.2f}%, {sd['laggards_following']}/{sd['laggards_total']} following"
            for sn, sd in sorted(sector_ctx.items(), key=lambda x: -abs(x[1]['leader_change_pct']))
        )

        # SCANNER LLM (sees enriched signals + memory)
        print(f"  [{date} bar {scan_bar}] Scanner: {len(signals)} signals ({red_flagged} red-flagged)...")
        ranked = scanner_with_graph(signals_text, sector_text, memory, API_KEY)
        llm_calls += 1; time.sleep(0.3)
        if not ranked: continue
        ranked_str = ', '.join(str(r.get('symbol',''))+'('+str(r.get('score',0))+')' for r in ranked[:4])
        print(f"    Ranked {len(ranked)}: {ranked_str}")

        # Inject sector into ranked for risk
        for r in ranked:
            r["sector"] = get_sector(r.get("symbol", ""))

        # RISK LLM (queries trade graph for exposure, memory for history)
        risk_result = risk_with_graph(ranked, trade_graph, memory, API_KEY)
        llm_calls += 1; time.sleep(0.3)
        approved = risk_result.get("approved", [])
        vetoed = risk_result.get("vetoed", [])
        if vetoed: print(f"    Vetoed: {', '.join(v.get('symbol','') for v in vetoed)}")
        if not approved: continue

        # EXECUTOR LLM (gets ATR data from system)
        price_lines = []
        for a in approved:
            s = a.get("symbol", "")
            sb = [b for b in all_data.get(s,[]) if b['timestamp'][:10]==date]
            pa = [b for b in all_data.get(s,[]) if b['timestamp'][:10]<=date]
            if sb and len(sb)>scan_bar:
                p = PriceStructure.compute(sb, scan_bar, pa[-2]['close'] if len(pa)>=2 else None)
                price_lines.append(f"{s}: price={p.get('price',0)} ATR={p.get('atr',0)}({p.get('atr_pct',0)}%) gap={p.get('gap_pct',0)}%")
        trades = executor_with_graph(approved, "\n".join(price_lines), API_KEY)
        llm_calls += 1; time.sleep(0.3)

        # Open positions
        for t in trades:
            sym = t.get("symbol","")
            if trade_graph.get_active(sym) or len(trade_graph.get_all_active())>=3: continue
            sig = next((s for s in signals if s.symbol==sym), None)
            if not sig: continue

            entry_price = sig.suggested_entry
            direction = t.get("direction", sig.direction)
            stop = t.get("stop", 0); target = t.get("target", 0)

            if not stop or not target:
                pa = [b for b in all_data.get(sym,[]) if b['timestamp'][:10]<=date]
                vr = vol_agent.get_adjusted_stop(entry_price, direction, pa[-30:])
                stop = stop or vr["stop"]; target = target or vr["target"]

            strat = t.get("strategy", next((a.get("strategy","") for a in approved if a.get("symbol")==sym), sig.strategy_name))
            sec_data = sector_ctx.get(get_sector(sym), {})
            sb = [b for b in all_data.get(sym,[]) if b['timestamp'][:10]==date]
            p_struct = PriceStructure.compute(sb, scan_bar, None)

            node = TradeNode(
                trade_id=f"{date}-{sym}", symbol=sym, direction=direction,
                strategy=strat, sector=get_sector(sym),
                entry_price=entry_price, entry_bar=scan_bar, entry_time=date,
                entry_signal_confidence=sig.raw_confidence,
                entry_sector_flow=sec_data.get("leader_change_pct", 0),
                entry_sector_strength=sec_data.get("strength", 0),
                entry_rvol=sig.rvol, entry_regime="unknown",
                entry_vwap_position=p_struct.get("vwap_position",""),
                entry_atr_pct=p_struct.get("atr_pct", 0),
                entry_gap_pct=p_struct.get("gap_pct", 0),
            )
            node.stop = stop; node.target = target
            trade_graph.open_trade(node)
            print(f"    OPEN: {direction} {sym} @ {entry_price:.2f} SL={stop:.2f} TP={target:.2f}")

    # ─── Simulate all active positions ───
    for sym, node in list(trade_graph.get_all_active().items()):
        sb = [b for b in all_data.get(sym,[]) if b['timestamp'][:10]==date]
        if not sb: continue
        entry_price = node.entry_price; entry_bar = node.entry_bar
        direction = node.direction; stop = node.stop; target = node.target
        max_fav = 0; cur_stop = stop; partial_taken = False; partial_pnl = 0
        high_since = entry_price; low_since = entry_price
        exit_price = None; exit_reason = ''; exit_bar = 0

        for j in range(entry_bar+1, len(sb)):
            b = sb[j]; bh = j - entry_bar
            high_since = max(high_since, b['high']); low_since = min(low_since, b['low'])
            if direction=='LONG':
                pnl = (b['close']-entry_price)/entry_price*100; fav = b['high']-entry_price
            else:
                pnl = (entry_price-b['close'])/entry_price*100; fav = entry_price-b['low']
            max_fav = max(max_fav, fav)
            mfe = max_fav/entry_price*100
            mae = (entry_price-low_since)/entry_price*100 if direction=='LONG' else (high_since-entry_price)/entry_price*100
            fade = max(0, (max_fav-max(0,fav))/max_fav*100) if max_fav>0 else 0

            zone = "strong_win" if pnl>=0.75 else "decent_win" if pnl>=0.4 else "small_win" if pnl>=0.15 else "scratch" if pnl>=0 else "loss"

            # Write bar snapshot to trade graph
            sec_data = SectorAnalyzer.compute(all_data, date, j)
            sec_info = sec_data.get(node.sector, {})
            vol_data = VolumeProfiler.compute(sb, j)

            snap = BarSnapshot(
                bar_index=j, timestamp=b.get('timestamp','').split(' ')[1][:5],
                price=b['close'], high=b['high'], low=b['low'], volume=b['volume'],
                pnl_pct=round(pnl,3), mfe_pct=round(mfe,3), mae_pct=round(mae,3),
                fade_pct=round(fade,1), zone=zone,
                sector_leader_change=sec_info.get('leader_change_pct',0),
                sector_strength=sec_info.get('strength',0),
                volume_divergence=vol_data.get('divergence','none'),
            )
            node.add_snapshot(snap)

            # Write observations
            if "BEARISH" in vol_data.get('divergence',''):
                node.add_observation(j, "volume", f"Bearish divergence: {vol_data['divergence']}")
            if fade > 50 and mfe > 0.2:
                node.add_observation(j, "fade", f"Gave back {fade:.0f}% of {mfe:.2f}% peak")

            # Hard stops
            if direction=='LONG':
                if b['low']<=cur_stop: exit_price=cur_stop; exit_reason='stop'; exit_bar=j; break
                if b['high']>=target: exit_price=target; exit_reason='target'; exit_bar=j; break
            else:
                if b['high']>=cur_stop: exit_price=cur_stop; exit_reason='stop'; exit_bar=j; break
                if b['low']<=target: exit_price=target; exit_reason='target'; exit_bar=j; break

            # MONITOR LLM every 6 bars (queries trade dev graph for this trade's history)
            if bh%6==0 and bh>0:
                mon = monitor_with_graph(node, memory, API_KEY)
                llm_calls += 1; time.sleep(0.2)
                act = mon.get("action","hold")

                if act=="close":
                    exit_price=b['close']; exit_reason='monitor'; exit_bar=j
                    node.add_observation(j,"monitor",f"CLOSE: {mon.get('reason','')}")
                    break
                elif act=="tighten" and pnl>0:
                    ns = entry_price+max_fav*0.3 if direction=='LONG' else entry_price-max_fav*0.3
                    cur_stop = max(cur_stop,ns) if direction=='LONG' else min(cur_stop,ns)
                    node.add_observation(j,"monitor",f"TIGHTEN: {mon.get('reason','')}")
                elif act=="partial" and not partial_taken:
                    partial_taken=True; partial_pnl=pnl
                    cur_stop = max(cur_stop,entry_price) if direction=='LONG' else min(cur_stop,entry_price)
                    node.add_observation(j,"monitor",f"PARTIAL: {mon.get('reason','')}")

        if not exit_price:
            exit_price=sb[-1]['close']; exit_reason='eod'; exit_bar=len(sb)-1

        # Close trade in graph
        closed = trade_graph.close_trade(sym, exit_price, exit_bar, exit_reason)
        if closed:
            blended = 0.3*partial_pnl + 0.7*closed.final_pnl_pct if partial_taken else closed.final_pnl_pct

            # Write to market memory
            memory.record_trade({
                "symbol": sym, "strategy": node.strategy, "direction": direction,
                "sector": node.sector, "pnl": closed.final_pnl_pct,
                "mfe": closed.max_mfe_pct, "exit_reason": exit_reason,
                "bars_held": closed.bars_held, "date": date, "scan_bar": entry_bar,
            })

            # Judge reviews (writes to journal, never fed back to other LLMs)
            journal = judge_with_graph(closed, API_KEY)
            llm_calls += 1; time.sleep(0.2)

            day_pnl += blended; day_trades += 1
            if blended > 0: day_best = f"{sym} +{blended:.3f}%"
            else: day_worst = f"{sym} {blended:+.3f}%"

            all_results.append({
                'date': date, 'symbol': sym, 'direction': direction,
                'strategy': node.strategy, 'sector': node.sector,
                'entry': entry_price, 'exit': exit_price, 'exit_reason': exit_reason,
                'pnl_pct': round(closed.final_pnl_pct,3), 'blended': round(blended,3),
                'mfe_pct': round(closed.max_mfe_pct,3), 'bars': closed.bars_held,
                'partial': partial_taken, 'journal': journal,
            })
            print(f"    CLOSED: {direction} {sym} P&L={closed.final_pnl_pct:+.3f}% ({exit_reason}) MFE={closed.max_mfe_pct:.3f}%")

    # Record day in memory
    memory.record_day(DayMemory(date=date, trades_taken=day_trades, total_pnl=round(day_pnl,3),
                                best_trade=day_best, worst_trade=day_worst))

# ─── REPORT ───
print()
print('='*120)
print(f'v7 GRAPH-BACKED MULTI-LLM | {llm_calls} LLM calls | Graphs: {len(trade_graph._closed)} trades in memory')
print('='*120)
print(f'{"Date":>10} {"Symbol":>12} {"Dir":>5} {"Strat":>6} {"Sector":>8} {"Entry":>8} {"Exit":>8} {"P&L%":>7} {"Blend":>7} {"MFE%":>6} {"Bars":>4} {"P":>2} {"Exit":>8}')
print('-'*120)
total=0; wins=0; losses=0
for r in all_results:
    w='W' if r['blended']>0 else 'L'
    if r['blended']>0: wins+=1
    else: losses+=1
    total+=r['blended']
    p='Y' if r['partial'] else ''
    print(f'{r["date"]:>10} {r["symbol"]:>12} {r["direction"]:>5} {r["strategy"]:>6} {r["sector"]:>8} {r["entry"]:8.2f} {r["exit"]:8.2f} {r["pnl_pct"]:+6.3f}% {r["blended"]:+6.3f}% {r["mfe_pct"]:5.3f}% {r["bars"]:4d} {p:>2} {r["exit_reason"]:>8} {w}')
n=max(len(all_results),1)
print('-'*120)
print(f'TOTAL: {n} trades | W:{wins} L:{losses} | WR:{wins/n:.0%} | Blended:{total:+.3f}% | Avg:{total/n:+.3f}%')

for label,key in [("STRATEGY","strategy"),("EXIT","exit_reason"),("SECTOR","sector")]:
    print(f'\n--- {label} ---')
    by=defaultdict(lambda:{'n':0,'p':0,'w':0})
    for r in all_results:
        by[r[key]]['n']+=1; by[r[key]]['p']+=r['blended']
        if r['blended']>0: by[r[key]]['w']+=1
    for k,v in sorted(by.items(),key=lambda x:-x[1]['p']):
        print(f'  {str(k):12s}: {v["n"]:3d} trades, WR={v["w"]/v["n"]:.0%}, P&L={v["p"]:+.3f}%')

print('\n--- MARKET MEMORY (what the system learned) ---')
print(memory.get_full_memory_snapshot())

print('\n--- JUDGE JOURNAL ---')
for r in all_results:
    j=r.get('journal',{})
    e='+'if r['blended']>0 else'-'
    print(f'  {e} {r["date"]} {r["symbol"]:12s} ({r["blended"]:+.3f}%): {j.get("lesson","")[:80]}')

print(f'\n--- COMPARISON ---')
print(f'  v1 (no agents):       -0.411%')
print(f'  v3 (hardcoded):       +3.376%')
print(f'  v5 (single LLM):     -2.058%')
print(f'  v6 (multi LLM):      -1.841%')
print(f'  v7 (graph-backed):   {total:+.3f}%')
