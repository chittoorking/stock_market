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

# Pre-compute prev close + daily trend data
prev_close = {}
daily_trend = {}  # (date, sym) -> {trend, streak, daily_rsi, ema_position, higher_lows}

for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    daily_closes = []
    daily_highs = []
    daily_lows = []

    for i, d in enumerate(dates_for_sym):
        db = date_bars[d].get(sym, [])
        if not db: continue

        if i > 0:
            pdb = date_bars[dates_for_sym[i-1]].get(sym, [])
            if pdb: prev_close[(d, sym)] = pdb[-1]['close']

        day_close = db[-1]['close']
        day_high = max(b['high'] for b in db)
        day_low = min(b['low'] for b in db)
        daily_closes.append(day_close)
        daily_highs.append(day_high)
        daily_lows.append(day_low)

        if len(daily_closes) >= 5:
            # 5-day trend: are closes rising or falling?
            c5 = daily_closes[-5:]
            up_days = sum(1 for j in range(1, len(c5)) if c5[j] > c5[j-1])
            trend_5d = 'UP' if up_days >= 4 else 'DOWN' if up_days <= 1 else 'SIDEWAYS'

            # 10-day trend
            if len(daily_closes) >= 10:
                c10 = daily_closes[-10:]
                trend_10d = 'UP' if c10[-1] > c10[0] * 1.02 else 'DOWN' if c10[-1] < c10[0] * 0.98 else 'SIDEWAYS'
            else:
                trend_10d = trend_5d

            # Higher lows (bullish) or lower highs (bearish) last 5 days
            h5 = daily_highs[-5:]
            l5 = daily_lows[-5:]
            higher_lows = sum(1 for j in range(1, len(l5)) if l5[j] > l5[j-1])
            lower_highs = sum(1 for j in range(1, len(h5)) if h5[j] < h5[j-1])

            # Daily RSI (14-period)
            if len(daily_closes) >= 15:
                gains = [max(0, daily_closes[j]-daily_closes[j-1]) for j in range(-14, 0)]
                loss_l = [max(0, daily_closes[j-1]-daily_closes[j]) for j in range(-14, 0)]
                ag = sum(gains)/14; al = sum(loss_l)/14
                rsi = 100 - 100/(1+ag/al) if al > 0 else 50
            else:
                rsi = 50

            # Price vs 20-day SMA
            sma20 = sum(daily_closes[-min(20,len(daily_closes)):]) / min(20, len(daily_closes))
            above_sma = day_close > sma20

            # Streak: consecutive up/down days
            streak = 0
            for j in range(len(daily_closes)-1, 0, -1):
                if daily_closes[j] > daily_closes[j-1]: streak += 1
                else: break
            down_streak = 0
            for j in range(len(daily_closes)-1, 0, -1):
                if daily_closes[j] < daily_closes[j-1]: down_streak += 1
                else: break

            # 52-week (250 days) high/low context
            lookback = min(250, len(daily_closes))
            high_52w = max(daily_closes[-lookback:])
            low_52w = min(daily_closes[-lookback:])
            pct_from_high = (day_close - high_52w) / high_52w * 100
            pct_from_low = (day_close - low_52w) / low_52w * 100
            near_52w_high = pct_from_high > -3  # Within 3% of 52w high
            near_52w_low = pct_from_low < 3     # Within 3% of 52w low

            # Volume context: today vs 20-day avg daily volume
            daily_vols = []
            for j2 in range(max(0, len(daily_closes)-21), len(daily_closes)-1):
                d2 = dates_for_sym[j2]
                db2 = date_bars[d2].get(sym, [])
                if db2: daily_vols.append(sum(b2['volume'] for b2 in db2))
            avg_daily_vol = sum(daily_vols)/len(daily_vols) if daily_vols else 0
            today_vol = sum(b3['volume'] for b3 in db)
            vol_vs_avg = today_vol / avg_daily_vol if avg_daily_vol > 0 else 1

            # Volatility: 5-day avg daily range as % of price
            ranges_5d = [(daily_highs[j3]-daily_lows[j3])/daily_closes[j3]*100 for j3 in range(-min(5,len(daily_closes)),0)]
            avg_range_5d = sum(ranges_5d)/len(ranges_5d) if ranges_5d else 0

            daily_trend[(d, sym)] = {
                'trend_5d': trend_5d,
                'trend_10d': trend_10d,
                'higher_lows': higher_lows,
                'lower_highs': lower_highs,
                'rsi': round(rsi, 0),
                'above_sma': above_sma,
                'up_streak': streak,
                'dn_streak': down_streak,
                'daily_change_5d': round((daily_closes[-1]/daily_closes[-5]-1)*100, 2) if len(daily_closes)>=5 else 0,
                'pct_from_52w_high': round(pct_from_high, 1),
                'pct_from_52w_low': round(pct_from_low, 1),
                'near_52w_high': near_52w_high,
                'near_52w_low': near_52w_low,
                'vol_vs_20d_avg': round(vol_vs_avg, 1),
                'avg_daily_range': round(avg_range_5d, 2),
            }

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
    """Full market state — ALL stocks, bar-by-bar momentum, not just snapshot."""
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

        vol = sum(b['volume'] for b in bsf)
        last3 = sum(1 for b in bsf[-3:] if b['close'] > b['open'])

        # Bar-by-bar momentum: is each bar accelerating or decelerating?
        bar_moves = []
        for i in range(1, len(bsf)):
            bm = (bsf[i]['close'] - bsf[i-1]['close']) / bsf[i-1]['close'] * 100
            bar_moves.append(round(bm, 3))

        # Momentum acceleration: are recent bars getting bigger?
        if len(bar_moves) >= 4:
            first_half = sum(abs(m) for m in bar_moves[:3])
            second_half = sum(abs(m) for m in bar_moves[3:])
            accel = 'ACCEL' if second_half > first_half * 1.2 else 'DECEL' if second_half < first_half * 0.8 else 'STEADY'
        else:
            accel = 'N/A'

        # Consecutive direction (are bars all same color?)
        consec_green = 0; consec_red = 0
        for b in reversed(bsf[1:]):
            if b['close'] > b['open']: consec_green += 1
            else: break
        for b in reversed(bsf[1:]):
            if b['close'] < b['open']: consec_red += 1
            else: break
        streak = f'{consec_green}G' if consec_green > consec_red else f'{consec_red}R'

        stocks.append({
            'sym': sym, 'move': move, 'gap': gap, 'body': bar0_body_pct,
            'bar0': bar0_dir, 'vwap': vwap_dist, 'vol': vol, 'last3': last3,
            'price': db[scan_bar]['close'], 'accel': accel, 'streak': streak,
            'bar_moves': bar_moves,
        })

    # Market breadth
    up = sum(1 for s in stocks if s['move'] > 0.15)
    dn = sum(1 for s in stocks if s['move'] < -0.15)
    tot = len(stocks)
    regime = 'TRENDING UP' if up/tot > 0.6 else 'TRENDING DOWN' if dn/tot > 0.6 else 'CHOPPY'

    header = f"DATE: {date} | Market: {up}/{tot} up, {dn}/{tot} down | {regime}\n\n"

    # Show ALL stocks grouped by category
    # 1. Big movers (>1%) — these are the obvious picks
    big = [s for s in stocks if abs(s['move']) > 1.0]
    big.sort(key=lambda x: -abs(x['move']))

    # 2. Building momentum quietly (0.3-1%, accelerating)
    building = [s for s in stocks if 0.3 < abs(s['move']) <= 1.0 and s['accel'] == 'ACCEL']
    building.sort(key=lambda x: -abs(x['move']))

    # 3. Quiet with volume (small move but high volume = coiling)
    quiet_vol = [s for s in stocks if abs(s['move']) <= 0.3 and s['vol'] > sum(ss['vol'] for ss in stocks)/len(stocks)*1.5]

    lines = [header]
    def fmt_stock(s):
        dt = daily_trend.get((date, s['sym']), {})
        trend = dt.get('trend_5d', '?')
        t10 = dt.get('trend_10d', '?')
        rsi_d = dt.get('rsi', 50)
        chg5 = dt.get('daily_change_5d', 0)
        hl = dt.get('higher_lows', 0)
        lh = dt.get('lower_highs', 0)
        sma = 'abvSMA' if dt.get('above_sma') else 'blwSMA'
        h52 = dt.get('pct_from_52w_high', 0)
        l52 = dt.get('pct_from_52w_low', 0)
        vol_d = dt.get('vol_vs_20d_avg', 1)
        adr = dt.get('avg_daily_range', 0)
        bm_str = ' '.join(f'{m:+.2f}' for m in s['bar_moves'][-4:])
        w52 = 'nr52H' if dt.get('near_52w_high') else ('nr52L' if dt.get('near_52w_low') else '')
        return (
            f"  {s['sym']:>12} | today:{s['move']:+5.2f}% gap:{s['gap']:+5.2f}% {s['accel']} {s['streak']} | "
            f"DAILY:{trend}/{t10} RSI={rsi_d:.0f} {sma} 5d={chg5:+.1f}% HL={hl} LH={lh} | "
            f"52w: {h52:+.0f}%fromH {l52:+.0f}%fromL {w52} | vol:{vol_d:.1f}x ADR:{adr:.1f}% | [{bm_str}]"
        )

    lines.append("BIG MOVERS (>1%):")
    for s in big[:15]:
        lines.append(fmt_stock(s))

    lines.append("\nBUILDING MOMENTUM (0.3-1%, accelerating):")
    for s in building[:10]:
        lines.append(fmt_stock(s))

    lines.append("\nQUIET + HIGH VOLUME (coiling?):")
    for s in quiet_vol[:5]:
        dt = daily_trend.get((date, s['sym']), {})
        lines.append(f"  {s['sym']:>12} | {s['move']:+5.2f}% | vol:{s['vol']:,} | DAILY: {dt.get('trend_5d','?')} RSI={dt.get('rsi',50):.0f}")

    return "\n".join(lines), stocks


def llm_pick(snapshot, api_key):
    """LLM picks exactly 1 stock and direction."""
    system = """You are an expert NSE intraday trader. You see all 45 stocks with DAILY trend data + today's opening action.

STEP 1 — READ THE DAILY TREND FIRST:
Each stock shows its 5-day and 10-day trend (UP/DOWN/SIDEWAYS), daily RSI,
position vs 20-day SMA, and higher-lows/lower-highs count.
A stock trending UP on daily with higher lows = strong. Trade WITH that trend intraday.
A stock SIDEWAYS on daily = range-bound = DON'T TRADE IT (this is what kills trades).

STEP 2 — CHECK TODAY'S INTRADAY ACTION:
Is today's move WITH or AGAINST the daily trend?
- Stock trending UP daily + opening green today = HIGH PROBABILITY LONG
- Stock trending DOWN daily + opening red today = HIGH PROBABILITY SHORT
- Stock trending UP daily but opening red today = WAIT or SKIP (counter-trend)
- Stock SIDEWAYS daily + any move = SKIP (dead pick, stock won't follow through)

STEP 3 — CONFIRM WITH BAR MOMENTUM:
Look at bar-by-bar data. Is momentum ACCELERATING or DECELERATING?
ACCEL = bars getting bigger = conviction building = ENTER
DECEL = bars getting smaller = fading = SKIP

WHAT MAKES DEAD PICKS (avoid these):
- Daily trend = SIDEWAYS AND today's move < 0.5% = truly dead stock
- Stock near 52-week middle with no momentum = range-bound
- Today's move AGAINST strong daily trend with strong body = dangerous fade

WHAT MAKES WINNERS:
- Daily trend = UP/DOWN (clear direction) + intraday move WITH it
- Stock near 52-week high/low = trending strongly
- Momentum ACCELERATING on today's bars
- High volume vs 20-day avg = institutional interest

IMPORTANT: CHOPPY market days CAN still have winners.
On choppy days, look for the ONE stock that has clear daily trend + intraday confirmation.
DO NOT skip just because the market is choppy — the best stock still moves +2-3%.
Only SKIP if truly NO stock has daily trend + intraday confirmation together.

Pick exactly 1 stock. Only SKIP if nothing has both daily trend AND intraday confirmation.

Output JSON only:
{"symbol":"STOCKNAME","direction":"LONG or SHORT","reasoning":"2 sentences citing daily trend + intraday confirmation"}
Or: {"symbol":"SKIP","direction":"SKIP","reasoning":"why no clear setup"}"""

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

    if sym == 'SKIP' or direction == 'SKIP':
        print(f"  LLM says SKIP today.\n"); continue

    # Trade it mechanically
    db = date_bars[date].get(sym, [])
    if not db or len(db) <= 40:
        print(f"  No data for {sym}. SKIP.\n"); continue

    scan_bar = 10  # 10:15 AM — wait for first 50min of noise to settle
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
