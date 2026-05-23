"""
Full MoE pipeline: scan + trade + monitor every 5 min.
No hardcoded rules. LLM has full autonomy.
5 lots per trade. LLM books profits at multiple levels.
Goal: capture as close to MFE as possible, never give back profits.
"""
import sys, io, os, csv, json, time
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')

from pathlib import Path
from collections import defaultdict
from app.agents.moe_fast import moe_scan_fast, moe_monitor_fast
from app.signals.proven_strategies import GapAndGoSignal, LenzSignal, AftershockSignal, GapDecaySignal
from app.core.signal_enrichment import enrich_all_signals, format_enriched_signals
from app.agents.data_providers import SectorAnalyzer
from app.core.market_memory import MarketMemory

API_KEY = os.environ.get("OPENAI_API_KEY", "REPLACE")
data_dir = Path('data/5min')
all_data = {}
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    all_data[sym] = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                       'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]

all_dates = sorted(set(r['timestamp'][:10] for rows in all_data.values() for r in rows))

import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--start', type=int, default=-3)
parser.add_argument('--days', type=int, default=3)
args, _ = parser.parse_known_args()

if args.start < 0:
    test_dates = all_dates[args.start:] if args.start + args.days >= 0 else all_dates[args.start:args.start + args.days]
else:
    test_dates = all_dates[args.start:args.start + args.days]

print(f"MoE FULL | {len(test_dates)} days | 5 lots per trade | LLM manages everything\n")

all_results = []
total_api_calls = 0

for date in test_dates:
    print(f"{'='*80}\nDATE: {date}")

    scan_bar = 6
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
        if not pb or len(db) <= scan_bar: continue
        pc = pb[-1]['close']; bsf = db[:scan_bar+1]
        for sc in [lambda: GapAndGoSignal.scan(sym, bsf[:3], pc),
                   lambda: LenzSignal.scan(sym, bsf) if len(bsf) >= 2 else None,
                   lambda: AftershockSignal.scan(sym, bsf) if len(bsf) >= 2 else None,
                   lambda: GapDecaySignal.scan(sym, bsf, pc, crowd, total_syms) if len(bsf) >= 7 else None]:
            try:
                sig = sc()
                if sig: signals.append(sig)
            except: pass

    if not signals:
        print(f"  No signals. SKIP.\n"); continue

    memory = MarketMemory()
    enrichments = enrich_all_signals(signals, all_data, date, scan_bar, memory)
    signals_text = format_enriched_signals(signals, enrichments)
    sector_ctx = SectorAnalyzer.compute(all_data, date, scan_bar)
    sector_text = "\n".join(f"{sn}: {sd.get('leader','')} {sd.get('leader_change_pct',0):+.2f}%" for sn, sd in sector_ctx.items())
    up = dn = tot = 0
    for s2 in all_data:
        db2 = [b for b in all_data[s2] if b['timestamp'][:10] == date]
        if len(db2) <= scan_bar: continue
        tot += 1; mv = (db2[scan_bar]['close'] - db2[0]['open']) / db2[0]['open'] * 100
        if mv > 0.15: up += 1
        elif mv < -0.15: dn += 1

    ctx = f"DATE: {date}\nSIGNALS:\n{signals_text}\nSECTOR:\n{sector_text}\nBreadth: {up}/{tot} up, {dn}/{tot} down"
    trades, _ = moe_scan_fast(ctx, API_KEY)
    total_api_calls += 1
    print(f"  {len(signals)} signals -> {len(trades)} trades")

    for tr in trades[:3]:
        sym_name = tr.get('symbol', ''); direction = tr.get('direction', '')
        confidence = tr.get('confidence', 0)
        if not sym_name or not direction: continue

        sym_bars = [b for b in all_data.get(sym_name, []) if b['timestamp'][:10] == date]
        prev_bars = [b for b in all_data.get(sym_name, []) if b['timestamp'][:10] < date]
        if not sym_bars or len(sym_bars) <= scan_bar or not prev_bars: continue

        entry = sym_bars[scan_bar]['close']
        atr = sum(sym_bars[k]['high']-sym_bars[k]['low'] for k in range(max(0,scan_bar-5),scan_bar+1))/min(6,scan_bar+1)
        if direction == 'LONG':
            stop = entry - atr * 3; target = entry + atr * 3 * 2.5
        else:
            stop = entry + atr * 3; target = entry - atr * 3 * 2.5

        # 5 lots: 30%, 25%, 20%, 15%, 10%
        lots = {'L1': 0.30, 'L2': 0.25, 'L3': 0.20, 'L4': 0.15, 'L5': 0.10}
        booked_pnl = 0; cur_stop = stop; max_fav = 0
        exit_reason = 'eod'

        print(f"\n  {direction} {sym_name} @ {entry:.2f} | stop={stop:.2f} tgt={target:.2f} | 5 lots")

        for j in range(scan_bar + 1, len(sym_bars)):
            b = sym_bars[j]
            if direction == 'LONG':
                pnl_now = (b['close'] - entry) / entry * 100
                fav = (b['high'] - entry) / entry * 100
            else:
                pnl_now = (entry - b['close']) / entry * 100
                fav = (entry - b['low']) / entry * 100
            max_fav = max(max_fav, fav)
            fade = max(0, (max_fav - max(0, fav)) / max_fav * 100) if max_fav > 0 else 0
            bars_held = j - scan_bar
            ts = b.get('timestamp', '').split(' ')[1][:5] if ' ' in b.get('timestamp', '') else ''
            remaining = sum(lots.values())

            if remaining <= 0: break

            # Hard stop/target on remaining
            if direction == 'LONG' and b['low'] <= cur_stop:
                booked_pnl += ((cur_stop - entry) / entry * 100) * remaining
                lots = {}; exit_reason = 'stop'; break
            if direction == 'SHORT' and b['high'] >= cur_stop:
                booked_pnl += ((entry - cur_stop) / entry * 100) * remaining
                lots = {}; exit_reason = 'stop'; break
            if direction == 'LONG' and b['high'] >= target:
                booked_pnl += ((target - entry) / entry * 100) * remaining
                lots = {}; exit_reason = 'target'; break
            if direction == 'SHORT' and b['low'] <= target:
                booked_pnl += ((entry - target) / entry * 100) * remaining
                lots = {}; exit_reason = 'target'; break

            # LLM monitor every 3 bars
            if bars_held % 3 != 0 or bars_held == 0: continue

            vol_trend = 'up' if b['volume'] > sym_bars[max(0,j-1)]['volume'] else 'down'
            candle = 'green' if b['close'] > b['open'] else 'red'
            body = abs(b['close']-b['open'])/(b['high']-b['low'])*100 if b['high']!=b['low'] else 0
            lots_str = ' '.join(f'{k}={v*100:.0f}%' for k,v in lots.items())

            ctx = (
                f"{direction} {sym_name} | Entry:{entry:.2f} Now:{b['close']:.2f}\n"
                f"P&L:{pnl_now:+.3f}% | Peak:{max_fav:.3f}% | Fade:{fade:.0f}%\n"
                f"Booked so far:{booked_pnl:+.3f}% | Open lots: {lots_str}\n"
                f"Stop:{cur_stop:.2f} Target:{target:.2f}\n"
                f"Bar {bars_held} {ts} | Vol:{vol_trend} | {candle} body {body:.0f}%"
            )

            mon = moe_monitor_fast(ctx, API_KEY)
            total_api_calls += 1
            action = mon.get('action', 'hold')

            if action == 'close_all':
                booked_pnl += pnl_now * remaining
                lots = {}; exit_reason = 'llm_close'
                print(f"    {ts}: CLOSE ALL PnL={pnl_now:+.3f}% | {mon.get('reason','')[:50]}")
                break

            # Book individual lots
            if action.startswith('book_'):
                lot_key = action.split('_')[1].upper()
                if lot_key in lots:
                    booked_pnl += pnl_now * lots[lot_key]
                    print(f"    {ts}: BOOK {lot_key} ({lots[lot_key]*100:.0f}%) @ PnL={pnl_now:+.3f}%")
                    del lots[lot_key]
                # Also try generic book (book first available)
                elif lots:
                    first = list(lots.keys())[0]
                    booked_pnl += pnl_now * lots[first]
                    print(f"    {ts}: BOOK {first} ({lots[first]*100:.0f}%) @ PnL={pnl_now:+.3f}%")
                    del lots[first]

            # Tighten stop
            if action == 'tighten' or mon.get('new_stop', 'unchanged') != 'unchanged':
                ns_raw = mon.get('new_stop', 'unchanged')
                if ns_raw not in ('unchanged', None, ''):
                    try:
                        ns = entry if str(ns_raw).lower() == 'breakeven' else float(ns_raw)
                        if direction == 'LONG' and ns > cur_stop:
                            cur_stop = ns
                            print(f"    {ts}: TRAIL stop->{cur_stop:.2f} PnL={pnl_now:+.3f}%")
                        elif direction == 'SHORT' and ns < cur_stop:
                            cur_stop = ns
                            print(f"    {ts}: TRAIL stop->{cur_stop:.2f} PnL={pnl_now:+.3f}%")
                    except: pass

            # Extend target
            nt_raw = mon.get('new_target', 'unchanged')
            if nt_raw not in ('unchanged', None, ''):
                try:
                    nt = float(nt_raw)
                    target = nt
                    print(f"    {ts}: TARGET->{target:.2f}")
                except: pass

        # EOD close remaining
        if lots:
            eod_p = sym_bars[-1]['close']
            eod_pnl = (eod_p-entry)/entry*100 if direction=='LONG' else (entry-eod_p)/entry*100
            booked_pnl += eod_pnl * sum(lots.values())

        win = booked_pnl > 0
        w = 'W' if win else 'L'
        capture = booked_pnl / max_fav * 100 if max_fav > 0 else 0
        print(f"  {w} PnL={booked_pnl:+.3f}% | MFE={max_fav:.3f}% | Captured={capture:.0f}% of MFE | {exit_reason}")

        all_results.append({
            'date': date, 'sym': sym_name, 'dir': direction,
            'confidence': confidence, 'pnl': round(booked_pnl, 4),
            'win': win, 'exit_reason': exit_reason, 'mfe': round(max_fav, 3),
            'capture': round(capture, 1),
        })

    print()

# Summary
print(f"\n{'='*80}\nSUMMARY | {total_api_calls} API calls\n{'='*80}")
if all_results:
    wins = sum(1 for r in all_results if r['win'])
    n = len(all_results)
    total_pnl = sum(r['pnl'] for r in all_results)
    avg_capture = sum(r['capture'] for r in all_results) / n
    print(f"Trades: {n} | W:{wins} L:{n-wins} | WR:{wins/n*100:.0f}%")
    print(f"Total PnL: {total_pnl:+.3f}% | Avg: {total_pnl/n:+.3f}%")
    print(f"Avg MFE captured: {avg_capture:.0f}%")

    for r in sorted(all_results, key=lambda x: x['date']):
        w = 'W' if r['win'] else 'L'
        print(f"  {r['date']} {r['dir']:>5} {r['sym']:>12} PnL={r['pnl']:+.3f}% MFE={r['mfe']:.3f}% cap={r['capture']:.0f}% {r['exit_reason']:>10} {w}")
