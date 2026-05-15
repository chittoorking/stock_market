"""Run full agent pipeline on 6-month data — last 10 trading days."""
import sys; sys.path.insert(0, '.')
import csv, json, time
from pathlib import Path
from collections import defaultdict

API_KEY = "REPLACE_WITH_YOUR_OPENAI_KEY"

data_dir = Path('data/5min')
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
from app.core.signal_enrichment import enrich_all_signals, format_enriched_signals
from app.signals.base import get_sector, SECTOR_MAP

vol_agent = VolatilityAgent()
trade_graph = TradeDevGraph()
memory = MarketMemory()

# Window selection: use --start and --days args, default to last 10
import argparse
_parser = argparse.ArgumentParser()
_parser.add_argument('--start', type=int, default=-10, help='Start day index (negative = from end)')
_parser.add_argument('--days', type=int, default=10)
_args, _ = _parser.parse_known_args()

all_dates = sorted(set(r['timestamp'][:10] for rows in all_data.values() for r in rows))
if _args.start < 0:
    test_dates = all_dates[_args.start:_args.start + _args.days] if _args.start + _args.days < 0 else all_dates[_args.start:]
else:
    test_dates = all_dates[_args.start:_args.start + _args.days]
print(f"Running on {len(test_dates)} days: {test_dates[0]} to {test_dates[-1]}")
print(f"Stocks: {len(all_data)}")

all_results = []
llm_calls = 0

for date in test_dates:
    # Crowd count
    crowd = 0; total_syms = 0
    for sym, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or not db: continue
        total_syms += 1
        gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
        if abs(gap) > 0.3: crowd += 1

    day_pnl = 0; day_trades = 0

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

        # Scanner
        sector_ctx = SectorAnalyzer.compute(all_data, date, scan_bar)
        signals_text = format_enriched_signals(signals, enrichments)
        sector_text = "\n".join(
            f"{sn}: {sd['leader']} {sd['leader_change_pct']:+.2f}%, {sd['laggards_following']}/{sd['laggards_total']} following"
            for sn, sd in sorted(sector_ctx.items(), key=lambda x: -abs(x[1]['leader_change_pct']))
        )

        print(f"  [{date} bar {scan_bar}] {len(signals)} signals ({red_count} red-flagged)...", end=" ")
        ranked = scanner_with_graph(signals_text, sector_text, memory, API_KEY)
        llm_calls += 1; time.sleep(0.3)
        if not ranked:
            print("no ranked signals")
            continue

        for r in ranked: r["sector"] = get_sector(r.get("symbol", ""))

        # Risk
        risk_result = risk_with_graph(ranked, trade_graph, memory, API_KEY)
        llm_calls += 1; time.sleep(0.3)
        approved = risk_result.get("approved", [])
        if not approved:
            print(f"ranked {len(ranked)}, all vetoed")
            continue

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
        llm_calls += 1; time.sleep(0.3)

        opened = 0
        for t in trades:
            sym = t.get("symbol","")
            if trade_graph.get_active(sym) or len(trade_graph.get_all_active())>=3: continue
            sig = next((s for s in signals if s.symbol==sym), None)
            if not sig: continue

            entry_price = sig.suggested_entry
            direction = t.get("direction", sig.direction)

            # Smart stops: chart-structure-based, not ATR
            from app.agents.smart_stops import SmartStopCalculator
            sb_for_stop = [b for b in all_data.get(sym,[]) if b['timestamp'][:10]==date]
            pa = [b for b in all_data.get(sym,[]) if b['timestamp'][:10]<=date]
            atr = vol_agent.calculate_atr(pa[-30:]) if len(pa) >= 5 else 0
            stop, stop_reason = SmartStopCalculator.calculate(sb_for_stop, scan_bar, entry_price, direction, atr)
            target, _ = SmartStopCalculator.calculate_target(entry_price, stop, direction, 2.5)

            # Override with LLM's stop/target if they're reasonable
            llm_stop = t.get("stop", 0); llm_target = t.get("target", 0)
            if llm_stop and abs(llm_stop - entry_price) / entry_price * 100 > 0.20:
                # LLM set a wider stop — use the WIDER of chart vs LLM
                if direction == "LONG":
                    stop = min(stop, llm_stop)  # Lower = wider for longs
                else:
                    stop = max(stop, llm_stop)  # Higher = wider for shorts
            if llm_target:
                target = llm_target

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
            opened += 1

        print(f"ranked {len(ranked)}, approved {len(approved)}, opened {opened}")

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

        prev_events = []  # Track what changed bar-to-bar

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

            # ─── EVERY BAR: write to trade dev graph ───
            sec_data = SectorAnalyzer.compute(all_data, date, j)
            sec_info = sec_data.get(node.sector, {})
            vol_data = VolumeProfiler.compute(sb, j)

            snap = BarSnapshot(bar_index=j, timestamp=b.get('timestamp','').split(' ')[1][:5],
                price=b['close'], high=b['high'], low=b['low'], volume=b['volume'],
                pnl_pct=round(pnl,3), mfe_pct=round(mfe,3), mae_pct=round(mae,3),
                fade_pct=round(fade,1), zone=zone,
                sector_leader_change=sec_info.get('leader_change_pct',0),
                sector_strength=sec_info.get('strength',0),
                volume_divergence=vol_data.get('divergence','none'))
            node.add_snapshot(snap)

            # ─── EVERY BAR: detect events (candle patterns, volume shifts, etc.) ───
            events = []

            # Candle patterns
            from app.agents.candle_patterns import detect_patterns, format_patterns_for_graph
            candle_pats = detect_patterns(sb, j)
            for cp in candle_pats:
                node.add_observation(j, "candle", f"{cp.name}({cp.direction}): {cp.meaning[:50]}")
                if cp.reliability == "high":
                    events.append(f"candle:{cp.name}({cp.direction})")

            # Volume divergence
            if "BEARISH" in vol_data.get('divergence',''):
                node.add_observation(j, "volume", f"Bearish divergence")
                events.append("volume:bearish_divergence")

            # Fade started
            if fade > 40 and mfe > 0.15 and "fade_started" not in str(prev_events):
                node.add_observation(j, "fade", f"Gave back {fade:.0f}% of {mfe:.2f}% peak")
                events.append("fade:started")

            # Sector shift
            if sec_info.get('leader_change_pct', 0) != 0:
                leader_against = (direction == 'LONG' and sec_info['leader_change_pct'] < -0.3) or \
                                 (direction == 'SHORT' and sec_info['leader_change_pct'] > 0.3)
                if leader_against and "sector_against" not in str(prev_events):
                    node.add_observation(j, "sector", f"Sector leader now against: {sec_info['leader_change_pct']:+.2f}%")
                    events.append("sector:turned_against")

            # Zone change
            if node.snapshots and len(node.snapshots) >= 2:
                prev_zone = node.snapshots[-2].zone if len(node.snapshots) >= 2 else ""
                if zone != prev_zone and (zone == "loss" or prev_zone == "decent_win"):
                    events.append(f"zone:{prev_zone}->{zone}")

            prev_events = events

            # ─── Hard stops/targets (system, not LLM) ───
            if direction=='LONG':
                if b['low']<=cur_stop: exit_price=cur_stop; exit_reason='stop'; exit_bar=j; break
                if b['high']>=target: exit_price=target; exit_reason='target'; exit_bar=j; break
            else:
                if b['high']>=cur_stop: exit_price=cur_stop; exit_reason='stop'; exit_bar=j; break
                if b['low']<=target: exit_price=target; exit_reason='target'; exit_bar=j; break

            # ─── LLM CALL every 6 bars ───
            # Data is written to graph EVERY bar (above). LLM checks periodically.
            # The LLM sees accumulated events/patterns at each check.
            if not (bh % 6 == 0 and bh > 0):
                continue

            # Build technical context for the LLM
            from app.agents.chart_narratives import NarrativeBuilder
            candle_text = format_patterns_for_graph(candle_pats)
            narratives = NarrativeBuilder.build(sb, j, direction, entry_price)
            narrative_text = NarrativeBuilder.format_for_llm(narratives, direction)

            # Add event trigger info so LLM knows WHY it's being called
            event_trigger = f"TRIGGERED BY: {', '.join(events)}" if events else "Periodic check"
            full_tech = f"{event_trigger}\n\n{candle_text}\n\n{narrative_text}"

            mon = monitor_with_graph(node, memory, API_KEY, technical_data=full_tech)
            llm_calls += 1; time.sleep(0.2)
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
            memory.record_trade({"symbol": sym, "strategy": node.strategy, "direction": direction,
                "sector": node.sector, "pnl": closed.final_pnl_pct, "mfe": closed.max_mfe_pct,
                "exit_reason": exit_reason, "bars_held": closed.bars_held, "date": date, "scan_bar": entry_bar})

            journal = judge_with_graph(closed, API_KEY)
            llm_calls += 1; time.sleep(0.2)

            day_pnl += blended; day_trades += 1
            all_results.append({
                'date': date, 'symbol': sym, 'direction': direction,
                'strategy': node.strategy, 'sector': node.sector,
                'entry': entry_price, 'exit': exit_price, 'exit_reason': exit_reason,
                'pnl': round(closed.final_pnl_pct,3), 'blended': round(blended,3),
                'mfe': round(closed.max_mfe_pct,3), 'bars': closed.bars_held,
                'partial': partial_taken,
            })

    memory.record_day(DayMemory(date=date, trades_taken=day_trades, total_pnl=round(day_pnl,3)))
    print(f"  Day {date}: {day_trades} trades, P&L={day_pnl:+.3f}%")

# Report
print()
print('='*100)
print(f'6-MONTH DATA, LAST 10 DAYS | {llm_calls} LLM calls')
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

print(f'\n--- MEMORY LEARNED ---')
print(memory.get_full_memory_snapshot())
