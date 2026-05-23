"""
Run MoE (15 expert agents) on 1 day. Full lifecycle analysis.
"""
import sys, io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')

import os, csv, json, time
from pathlib import Path
from collections import defaultdict

API_KEY = os.environ.get("OPENAI_API_KEY", "REPLACE")

data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.agents.data_providers import SectorAnalyzer
from app.core.signal_enrichment import enrich_all_signals, format_enriched_signals
from app.signals.base import get_sector

all_dates = sorted(set(r['timestamp'][:10] for rows in all_data.values() for r in rows))

import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--start', type=int, default=-1)
parser.add_argument('--days', type=int, default=1)
args, _ = parser.parse_known_args()

if args.start < 0:
    test_dates = all_dates[args.start:] if args.start + args.days >= 0 else all_dates[args.start:args.start + args.days]
else:
    test_dates = all_dates[args.start:args.start + args.days]

print(f"MoE MODE (15 experts) | {len(test_dates)} days: {test_dates[0]} to {test_dates[-1]}")
print(f"Stocks: {len(all_data)}\n")

from app.agents.moe_fast import moe_scan_fast

all_results = []

for date in test_dates:
    print(f"{'='*80}")
    print(f"DATE: {date}")
    print(f"{'='*80}")

    # Scan signals
    crowd = 0; total_syms = 0
    for sym, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or not db: continue
        total_syms += 1
        gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
        if abs(gap) > 0.3: crowd += 1

    scan_bar = 6
    signals = []
    for sym, bars in all_data.items():
        db = [b for b in bars if b['timestamp'][:10] == date]
        pb = [b for b in bars if b['timestamp'][:10] < date]
        if not pb or len(db) <= scan_bar: continue
        pc = pb[-1]['close']; bsf = db[:scan_bar+1]
        for scanner in [
            lambda: GapAndGoSignal.scan(sym, bsf[:3], pc),
            lambda: LenzSignal.scan(sym, bsf) if len(bsf)>=2 else None,
            lambda: AftershockSignal.scan(sym, bsf) if len(bsf)>=2 else None,
            lambda: GapDecaySignal.scan(sym, bsf, pc, crowd, total_syms) if len(bsf)>=7 else None,
        ]:
            try:
                sig = scanner()
                if sig: signals.append(sig)
            except: pass

    print(f"Signals found: {len(signals)}")
    if not signals:
        print("No signals. SKIP day.")
        continue

    # Build enrichment
    from app.core.market_memory import MarketMemory
    memory = MarketMemory()
    enrichments = enrich_all_signals(signals, all_data, date, scan_bar, memory)
    signals_text = format_enriched_signals(signals, enrichments)

    # Sector context
    sector_ctx = SectorAnalyzer.compute(all_data, date, scan_bar)
    sector_text = "\n".join(
        f"{sn}: leader {sd.get('leader','')} {sd.get('leader_change_pct',0):+.2f}%, {sd.get('laggards_following',0)}/{sd.get('laggards_total',0)} following"
        for sn, sd in sorted(sector_ctx.items(), key=lambda x: -abs(x[1].get('leader_change_pct',0)))
    )

    # Market breadth
    up=0; dn=0; tot=0
    for sym2 in all_data:
        db2 = [b for b in all_data[sym2] if b['timestamp'][:10] == date]
        if len(db2) <= scan_bar: continue
        tot += 1
        mv = (db2[scan_bar]['close'] - db2[0]['open']) / db2[0]['open'] * 100
        if mv > 0.15: up += 1
        elif mv < -0.15: dn += 1
    breadth_text = f"Market breadth: {up}/{tot} up ({up/tot*100:.0f}%), {dn}/{tot} down ({dn/tot*100:.0f}%)"

    # Macro
    macro_data = {}
    mf = Path('data/macro/daily_macro.json')
    if mf.exists():
        with open(mf) as f:
            for e in json.load(f): macro_data[e['date']] = e
    mc = macro_data.get(date, {})
    macro_text = f"VIX={mc.get('india_vix',0):.1f} | US overnight: S&P={mc.get('sp500_overnight',0):+.2f}% Nasdaq={mc.get('nasdaq_overnight',0):+.2f}% | Nifty prev={mc.get('nifty_prev_return',0):+.2f}%"

    # Full context for experts
    full_context = (
        f"DATE: {date} | SCAN BAR: {scan_bar}\n\n"
        f"SIGNALS:\n{signals_text}\n\n"
        f"SECTOR FLOWS:\n{sector_text}\n\n"
        f"{breadth_text}\n\n"
        f"MACRO: {macro_text}\n\n"
        f"MEMORY:\n{memory.get_full_memory_snapshot()}"
    )

    print(f"\nContext sent to 15 experts:")
    print(f"  Signals: {len(signals)}")
    print(f"  {breadth_text}")
    print(f"  Macro: {macro_text}")
    print(f"\nRunning 15-lens MoE (single API call)...")

    t0 = time.time()
    trades, opinions = moe_scan_fast(full_context, API_KEY)
    elapsed = time.time() - t0

    print(f"  Done in {elapsed:.1f}s")

    if trades:
        for tr in trades:
            sym_name = tr.get('symbol','?')
            direction = tr.get('direction','?')
            confidence = tr.get('confidence',0)
            capital_pct = tr.get('capital_pct',0)
            reasoning = tr.get('reasoning','')[:150]
            print(f"  TRADE: {direction} {sym_name} [{tr.get('strategy','')}] conf={confidence}% capital={capital_pct}%")
            print(f"    {reasoning}")

            # Simulate the trade
            strat = tr.get('strategy','')
            sym_bars = [b for b in all_data.get(sym_name, []) if b['timestamp'][:10] == date]
            prev_bars = [b for b in all_data.get(sym_name, []) if b['timestamp'][:10] < date]
            if not sym_bars or len(sym_bars) <= scan_bar or not prev_bars:
                print(f"    No data for {sym_name}")
                continue

            pc_val = prev_bars[-1]['close']
            entry = sym_bars[scan_bar]['close']
            atr = sum(sym_bars[k]['high']-sym_bars[k]['low'] for k in range(max(0,scan_bar-5),scan_bar+1))/min(6,scan_bar+1)
            if direction == 'LONG':
                stop = entry - atr * 1.5
                target = entry + atr * 1.5 * 2.5  # R:R 2.5
            else:
                stop = entry + atr * 1.5
                target = entry - atr * 1.5 * 2.5

            # Simulate with break-even stop
            exit_price = sym_bars[-1]['close']
            exit_reason = 'eod'
            mfe = 0
            be_activated = False
            cur_stop = stop

            for j in range(scan_bar+1, len(sym_bars)):
                if direction == 'LONG':
                    fav = (sym_bars[j]['high'] - entry) / entry * 100
                    mfe = max(mfe, fav)
                    if mfe >= 0.2 and not be_activated:
                        cur_stop = max(cur_stop, entry)
                        be_activated = True
                    if sym_bars[j]['low'] <= cur_stop:
                        exit_price = cur_stop; exit_reason = 'stop'; break
                    if sym_bars[j]['high'] >= target:
                        exit_price = target; exit_reason = 'target'; break
                else:
                    fav = (entry - sym_bars[j]['low']) / entry * 100
                    mfe = max(mfe, fav)
                    if mfe >= 0.2 and not be_activated:
                        cur_stop = min(cur_stop, entry)
                        be_activated = True
                    if sym_bars[j]['high'] >= cur_stop:
                        exit_price = cur_stop; exit_reason = 'stop'; break
                    if sym_bars[j]['low'] <= target:
                        exit_price = target; exit_reason = 'target'; break

            pnl = (exit_price - entry)/entry*100 if direction=='LONG' else (entry - exit_price)/entry*100
            win = pnl > 0
            w_str = 'W' if win else 'L'
            print(f"    Result: {w_str} PnL={pnl:+.3f}% exit={exit_reason} MFE={mfe:.3f}% BE={'Y' if be_activated else 'N'}")

            all_results.append({
                'date': date, 'sym': sym_name, 'dir': direction, 'confidence': confidence,
                'capital_pct': capital_pct, 'pnl': round(pnl, 4), 'win': win,
                'exit_reason': exit_reason, 'mfe': round(mfe, 3),
            })
    else:
        skip_reason = opinions.get('skipped_reason', opinions.get('reasoning', 'No trades'))
        print(f"  SKIP: {skip_reason[:150]}")

    print()

# ═══ SUMMARY ═══
print(f"\n{'='*80}")
print(f"MoE 10-DAY SUMMARY")
print(f"{'='*80}")
if all_results:
    wins = sum(1 for r in all_results if r['win'])
    total = len(all_results)
    total_pnl = sum(r['pnl'] for r in all_results)
    print(f"Trades: {total} | W:{wins} L:{total-wins} | WR:{wins/total*100:.0f}%")
    print(f"Total PnL: {total_pnl:+.3f}%")
    print(f"Avg PnL/trade: {total_pnl/total:+.3f}%")

    # Compound on 1 lakh
    bal = 100000
    for r in sorted(all_results, key=lambda x: x['date']):
        sized_pnl = r['pnl'] * r['capital_pct'] / 100  # Size by confidence
        bal *= (1 + sized_pnl / 100)
    print(f"Rs 1L compound (confidence-sized): Rs {bal:,.0f}")

    # Per day
    print(f"\nDaily log:")
    for r in sorted(all_results, key=lambda x: x['date']):
        w = 'W' if r['win'] else 'L'
        print(f"  {r['date']} {r['dir']:>5} {r['sym']:>12} conf={r['confidence']}% cap={r['capital_pct']}% PnL={r['pnl']:+.3f}% {r['exit_reason']:>6} {w}")
else:
    print("No trades taken.")
