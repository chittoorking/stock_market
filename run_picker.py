"""
LLM as STOCK PICKER — not position manager.
Every day has a +3.9% move available. LLM's job: find WHICH stock.
System handles entry/stop/target mechanically. LLM picks the stock.
"""
import sys, io, os, csv, json, time, requests
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')

from pathlib import Path
from collections import defaultdict

API_KEY = os.environ.get("OPENAI_API_KEY", "REPLACE")
data_dir = Path('data/5min')
all_data = {}; date_bars = defaultdict(dict)
print('Loading...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    bars = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
             'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]
    all_data[sym] = bars
    by_date = defaultdict(list)
    for b in bars: by_date[b['timestamp'][:10]].append(b)
    for d, bs in by_date.items(): date_bars[d][sym] = bs

all_dates = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))

# Pre-compute prev close
prev_close = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    for i, d in enumerate(dates_for_sym):
        if i > 0:
            pdb = date_bars[dates_for_sym[i-1]].get(sym, [])
            if pdb: prev_close[(d, sym)] = pdb[-1]['close']
print('Done.\n', flush=True)

import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--start', type=int, default=-10)
parser.add_argument('--days', type=int, default=10)
args, _ = parser.parse_known_args()

if args.start < 0:
    test_dates = all_dates[args.start:] if args.start + args.days >= 0 else all_dates[args.start:args.start + args.days]
else:
    test_dates = all_dates[args.start:args.start + args.days]

print(f"STOCK PICKER MODE | {len(test_dates)} days | LLM picks 1 stock, system trades it\n")


def build_market_snapshot(date, scan_bar=6):
    """Build what a trader sees at 9:45 AM — raw state of all 45 stocks."""
    lines = []
    stocks = []

    for sym in sorted(date_bars[date].keys()):
        db = date_bars[date][sym]
        pc = prev_close.get((date, sym))
        if pc is None or len(db) <= scan_bar: continue

        bsf = db[:scan_bar+1]
        move = (db[scan_bar]['close'] - db[0]['open']) / db[0]['open'] * 100
        gap = (db[0]['open'] - pc) / pc * 100

        bar0_body = abs(db[0]['close']-db[0]['open'])
        bar0_range = db[0]['high']-db[0]['low']
        bar0_body_pct = bar0_body/bar0_range*100 if bar0_range > 0 else 0
        bar0_dir = 'G' if db[0]['close'] > db[0]['open'] else 'R'

        # VWAP
        tp_vol = sum((b['high']+b['low']+b['close'])/3*b['volume'] for b in bsf)
        cum_vol = sum(b['volume'] for b in bsf)
        vwap = tp_vol/cum_vol if cum_vol > 0 else db[scan_bar]['close']
        vwap_dist = (db[scan_bar]['close'] - vwap) / vwap * 100

        # Volume
        vol = sum(b['volume'] for b in bsf)

        # Last 3 bars direction
        last3 = sum(1 for b in bsf[-3:] if b['close'] > b['open'])

        stocks.append({
            'sym': sym, 'move': move, 'gap': gap, 'body': bar0_body_pct,
            'bar0': bar0_dir, 'vwap': vwap_dist, 'vol': vol, 'last3': last3,
            'price': db[scan_bar]['close'],
        })

    # Market breadth
    up = sum(1 for s in stocks if s['move'] > 0.15)
    dn = sum(1 for s in stocks if s['move'] < -0.15)
    tot = len(stocks)
    regime = 'TRENDING UP' if up/tot > 0.6 else 'TRENDING DOWN' if dn/tot > 0.6 else 'CHOPPY'

    header = f"DATE: {date} | Market: {up}/{tot} up, {dn}/{tot} down | {regime}\n"

    # Sort by absolute move (biggest movers first)
    stocks.sort(key=lambda x: -abs(x['move']))

    stock_lines = []
    for s in stocks[:20]:  # Top 20 movers
        stock_lines.append(
            f"  {s['sym']:>12} | move:{s['move']:+5.2f}% | gap:{s['gap']:+5.2f}% | "
            f"bar0:{s['bar0']} body={s['body']:.0f}% | vwap:{s['vwap']:+.2f}% | "
            f"last3:{s['last3']}G/{3-s['last3']}R | price:{s['price']:.1f}"
        )

    return header + "\n".join(stock_lines), stocks


def llm_pick(snapshot, api_key):
    """LLM picks exactly 1 stock and direction."""
    system = """You are an expert NSE intraday trader. You see all 45 stocks at 9:45 AM.

Your job: pick THE ONE stock that will move the most in a predictable direction today.

What to look for:
- Big morning move with WEAK opening bar (low body %) = likely to reverse
- Big morning move with STRONG body (>80%) + sector confirming = likely to continue
- Stock that gapped against its sector = will snap back
- Stock with unusual volume = institutional interest
- CHOPPY market = fade the biggest movers. TRENDING market = ride momentum.

Pick exactly 1 stock. State direction (LONG or SHORT) and WHY.

Output JSON only:
{"symbol":"STOCKNAME","direction":"LONG or SHORT","reasoning":"2 sentences why this specific stock"}"""

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": snapshot},
        ],
        "temperature": 0,
        "max_tokens": 200,
    }

    try:
        resp = requests.post("https://api.openai.com/v1/chat/completions",
                           headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            raw = resp.json()["choices"][0]["message"]["content"]
            start = raw.find('{'); end = raw.rfind('}') + 1
            if start >= 0:
                return json.loads(raw[start:end])
    except Exception as e:
        print(f"  API error: {e}")
    return None


all_results = []

for date in test_dates:
    print(f"{'='*80}")
    print(f"DATE: {date}")

    snapshot, stocks = build_market_snapshot(date)
    print(snapshot[:500])

    # LLM picks
    t0 = time.time()
    pick = llm_pick(snapshot, API_KEY)
    elapsed = time.time() - t0

    if not pick:
        print(f"  LLM failed. SKIP.\n"); continue

    sym = pick.get('symbol', '')
    direction = pick.get('direction', '')
    reasoning = pick.get('reasoning', '')
    print(f"\n  LLM PICK ({elapsed:.1f}s): {direction} {sym}")
    print(f"  Reasoning: {reasoning}")

    # Trade it mechanically
    db = date_bars[date].get(sym, [])
    if not db or len(db) <= 40:
        print(f"  No data for {sym}. SKIP.\n"); continue

    scan_bar = 6
    entry = db[scan_bar]['close']
    atr = sum(db[k]['high']-db[k]['low'] for k in range(max(0,scan_bar-5),scan_bar+1))/min(6,scan_bar+1)

    # Wide stop (3x ATR), R:R 2.5, exit at 3PM
    if direction == 'LONG':
        stop = entry - atr * 3
        target = entry + atr * 3 * 2.5
    else:
        stop = entry + atr * 3
        target = entry - atr * 3 * 2.5

    # ═══ LLM MANAGES THE POSITION — trail, book, protect ═══
    from app.agents.moe_fast import moe_monitor_fast

    lots = {'L1': 0.30, 'L2': 0.25, 'L3': 0.20, 'L4': 0.15, 'L5': 0.10}
    booked_pnl = 0; cur_stop = stop; mfe = 0; exit_reason = 'eod'

    for j in range(scan_bar+1, min(len(db), 70)):
        b = db[j]
        if direction == 'LONG':
            pnl_now = (b['close']-entry)/entry*100
            fav = (b['high']-entry)/entry*100
        else:
            pnl_now = (entry-b['close'])/entry*100
            fav = (entry-b['low'])/entry*100
        mfe = max(mfe, fav)
        fade = max(0, (mfe-max(0,fav))/mfe*100) if mfe > 0 else 0
        bars_held = j - scan_bar
        ts = b.get('timestamp','').split(' ')[1][:5] if ' ' in b.get('timestamp','') else ''
        remaining = sum(lots.values())
        if remaining <= 0: break

        # Hard stop/target
        if direction=='LONG' and b['low']<=cur_stop:
            booked_pnl += ((cur_stop-entry)/entry*100)*remaining
            lots={}; exit_reason='stop'; break
        if direction=='SHORT' and b['high']>=cur_stop:
            booked_pnl += ((entry-cur_stop)/entry*100)*remaining
            lots={}; exit_reason='stop'; break
        if direction=='LONG' and b['high']>=target:
            booked_pnl += ((target-entry)/entry*100)*remaining
            lots={}; exit_reason='target'; break
        if direction=='SHORT' and b['low']<=target:
            booked_pnl += ((entry-target)/entry*100)*remaining
            lots={}; exit_reason='target'; break

        # LLM monitor every 3 bars
        if bars_held % 3 != 0 or bars_held == 0: continue

        vol_trend = 'up' if b['volume']>db[max(0,j-1)]['volume'] else 'down'
        candle = 'green' if b['close']>b['open'] else 'red'
        body = abs(b['close']-b['open'])/(b['high']-b['low'])*100 if b['high']!=b['low'] else 0
        lots_str = ' '.join(f'{k}={v*100:.0f}%' for k,v in lots.items())

        ctx = (
            f"{direction} {sym} | Entry:{entry:.2f} Now:{b['close']:.2f}\n"
            f"P&L:{pnl_now:+.3f}% | Peak:{mfe:.3f}% | Fade:{fade:.0f}%\n"
            f"Booked:{booked_pnl:+.3f}% | Lots: {lots_str}\n"
            f"Stop:{cur_stop:.2f} Target:{target:.2f}\n"
            f"Bar {bars_held} {ts} | Vol:{vol_trend} | {candle} body {body:.0f}%"
        )

        mon = moe_monitor_fast(ctx, API_KEY)

        action = mon.get('action', 'hold')
        if action == 'close_all':
            booked_pnl += pnl_now * remaining; lots={}
            exit_reason='llm_close'
            print(f"    {ts}: CLOSE ALL PnL={pnl_now:+.2f}% | {mon.get('reason','')[:50]}")
            break
        if action.startswith('book_') and lots:
            lot_key = action.split('_')[1].upper()
            if lot_key in lots:
                booked_pnl += pnl_now * lots[lot_key]
                print(f"    {ts}: BOOK {lot_key} ({lots[lot_key]*100:.0f}%) @ PnL={pnl_now:+.2f}%")
                del lots[lot_key]
            else:
                first = list(lots.keys())[0]
                booked_pnl += pnl_now * lots[first]
                print(f"    {ts}: BOOK {first} ({lots[first]*100:.0f}%) @ PnL={pnl_now:+.2f}%")
                del lots[first]
        ns = mon.get('new_stop', 'unchanged')
        if ns not in ('unchanged', None, ''):
            try:
                nsp = entry if str(ns).lower()=='breakeven' else float(ns)
                if direction=='LONG' and nsp > cur_stop:
                    cur_stop = nsp; print(f"    {ts}: TRAIL stop->{cur_stop:.2f}")
                elif direction=='SHORT' and nsp < cur_stop:
                    cur_stop = nsp; print(f"    {ts}: TRAIL stop->{cur_stop:.2f}")
            except: pass
        nt = mon.get('new_target', 'unchanged')
        if nt not in ('unchanged', None, ''):
            try: target = float(nt); print(f"    {ts}: TARGET->{target:.2f}")
            except: pass

    # EOD close remaining
    if lots:
        eod_p = db[min(69,len(db)-1)]['close']
        eod_pnl = (eod_p-entry)/entry*100 if direction=='LONG' else (entry-eod_p)/entry*100
        booked_pnl += eod_pnl * sum(lots.values())

    pnl = booked_pnl
    win = pnl > 0
    w = 'W' if win else 'L'
    capture = pnl/mfe*100 if mfe > 0 else 0

    # What was the BEST possible trade today?
    best_peak = 0; best_sym = ''; best_dir = ''
    for s in stocks:
        sdb = date_bars[date].get(s['sym'], [])
        if len(sdb) <= scan_bar: continue
        for d in ['LONG','SHORT']:
            for j in range(scan_bar+1, min(len(sdb), 70)):
                if d=='LONG': f = (sdb[j]['high']-sdb[scan_bar]['close'])/sdb[scan_bar]['close']*100
                else: f = (sdb[scan_bar]['close']-sdb[j]['low'])/sdb[scan_bar]['close']*100
                if f > best_peak: best_peak=f; best_sym=s['sym']; best_dir=d

    print(f"\n  RESULT: {w} PnL={pnl:+.3f}% | MFE={mfe:.3f}% | Captured={capture:.0f}% of MFE | exit={exit_reason}")
    print(f"  Best possible today: {best_dir} {best_sym} +{best_peak:.2f}%")

    all_results.append({
        'date': date, 'sym': sym, 'dir': direction, 'pnl': round(pnl,4),
        'win': win, 'mfe': round(mfe,3), 'exit': exit_reason,
        'best_peak': round(best_peak,2), 'best_sym': best_sym,
        'reasoning': reasoning[:100],
    })
    print()

# Summary
print(f"\n{'='*80}")
print(f"STOCK PICKER SUMMARY")
print(f"{'='*80}")
if all_results:
    wins = sum(1 for r in all_results if r['win'])
    n = len(all_results)
    total_pnl = sum(r['pnl'] for r in all_results)
    avg_mfe = sum(r['mfe'] for r in all_results)/n
    avg_best = sum(r['best_peak'] for r in all_results)/n
    charges = 0.0835 * n
    net = total_pnl - charges

    print(f"Trades: {n} | W:{wins} L:{n-wins} | WR:{wins/n*100:.0f}%")
    print(f"Total PnL: {total_pnl:+.3f}% | After charges: {net:+.3f}%")
    print(f"Avg PnL/trade: {total_pnl/n:+.3f}% | Avg MFE: {avg_mfe:.3f}%")
    print(f"Avg best possible: {avg_best:.2f}% | We captured: {total_pnl/sum(r['best_peak'] for r in all_results)*100:.0f}% of available")

    # Did LLM pick the right stock?
    right_stock = sum(1 for r in all_results if r['sym'] == r['best_sym'])
    print(f"\nLLM picked THE best stock: {right_stock}/{n} days ({right_stock/n*100:.0f}%)")

    print(f"\nDaily log:")
    for r in sorted(all_results, key=lambda x: x['date']):
        w = 'W' if r['win'] else 'L'
        match = 'BEST!' if r['sym']==r['best_sym'] else f"best was {r['best_sym']}"
        print(f"  {r['date']} {r['dir']:>5} {r['sym']:>12} PnL={r['pnl']:+.3f}% MFE={r['mfe']:.2f}% {r['exit']:>6} {w} | {match}")

    # Compound
    bal = 100000
    for r in sorted(all_results, key=lambda x: x['date']):
        bal *= (1 + (r['pnl']-0.0835)/100)
    print(f"\nRs 1L compound (after charges): Rs {bal:,.0f}")
