"""Live validation: BB(10,1.0) + ALMA(9) + BB width > 1.0% on 15-min bars.
Runs from now until 14:30, scans every 30 min, paper trades, tracks P&L."""
import sys, io, time, math, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from datetime import datetime
from live.indmoney_client import get_historical_candles, get_ltp, get_full_quote, SCRIP_CODES
import numpy as np

ALL_STOCKS = list(SCRIP_CODES.keys())
CAPITAL = 250_000; CHARGES = 60

def alma_calc(data, window=9, offset=0.85, sigma=6):
    m = offset*(window-1); s = window/sigma
    weights = [math.exp(-((i-m)**2)/(2*s*s)) for i in range(window)]
    ws = sum(weights); weights = [w/ws for w in weights]
    result = []
    for i in range(len(data)):
        if i < window-1: result.append(data[i])
        else: result.append(sum(data[i-window+1+j]*weights[j] for j in range(window)))
    return result

def bb_calc(closes, period=10, std_mult=1.0):
    mid=[]; upper=[]; lower=[]
    for i in range(len(closes)):
        if i < period-1: mid.append(closes[i]); upper.append(closes[i]); lower.append(closes[i])
        else:
            w = closes[i-period+1:i+1]; m=np.mean(w); s=np.std(w)
            mid.append(m); upper.append(m+std_mult*s); lower.append(m-std_mult*s)
    return mid, upper, lower

def get_15min_bars(sym):
    """Get today's 15-min bars from historical API."""
    now_ms = int(time.time() * 1000)
    open_ms = now_ms - 8*3600*1000  # 8 hours ago
    candles = get_historical_candles(sym, "5minute", open_ms, now_ms)
    if not candles:
        return []
    # Build 15-min from 5-min
    bars15 = []
    for i in range(0, len(candles) - 2, 3):
        chunk = candles[i:i+3]
        bars15.append({
            'o': chunk[0]['o'], 'h': max(c['h'] for c in chunk),
            'l': min(c['l'] for c in chunk), 'c': chunk[-1]['c'],
        })
    return bars15

# Track positions and P&L
positions = {}
trades = []
total_pnl = 0

print(f"=== BB+ALMA LIVE VALIDATION started at {datetime.now().strftime('%H:%M:%S')} ===", flush=True)
print(f"Strategy: BB(10,1.0) + ALMA(9) + BB width > 1.0%", flush=True)
print(f"Scanning every 30 min until 14:30", flush=True)
print(f"PAPER MODE — no real orders", flush=True)
print("=" * 70, flush=True)

end_time = datetime.now().replace(hour=14, minute=30, second=0)

# Calculate scan times from now
scan_times = []
t = datetime.now().replace(second=0, microsecond=0)
# Round up to next 30-min mark
if t.minute < 30:
    t = t.replace(minute=30)
else:
    t = t.replace(hour=t.hour + 1, minute=0)

while t <= end_time:
    scan_times.append(t)
    if t.minute == 0:
        t = t.replace(minute=30)
    else:
        t = t.replace(hour=t.hour + 1, minute=0)

print(f"Scan times: {[t.strftime('%H:%M') for t in scan_times]}", flush=True)

# Also do an immediate scan
scan_times.insert(0, datetime.now())

for scan_time in scan_times:
    now = datetime.now()
    if now < scan_time:
        wait = (scan_time - now).total_seconds()
        print(f"\nWaiting {wait:.0f}s for {scan_time.strftime('%H:%M')} scan...", flush=True)
        time.sleep(max(0, wait))

    if datetime.now() > end_time:
        break

    print(f"\n--- SCAN at {datetime.now().strftime('%H:%M:%S')} ---", flush=True)

    # Monitor existing positions first
    for sym in list(positions.keys()):
        pos = positions[sym]
        ltp_data = get_ltp([sym])
        price = ltp_data.get(sym, 0)
        if price <= 0: continue

        entry = pos['entry']; d = pos['dir']
        fav = (price-entry)/entry*100 if d=='LONG' else (entry-price)/entry*100
        pos['mfe'] = max(pos['mfe'], fav)

        exited = False; reason = ''
        # Trail
        atr_abs = pos['atr_abs']
        sl_abs = atr_abs * 0.05
        tr_abs = max(atr_abs * 0.005, entry * 0.10 / 100)

        if pos['mfe'] > pos['trail_pct']:
            nt = pos['mfe'] - pos['trail_pct']
            if d == 'LONG':
                tp = entry*(1+nt/100)
                pos['trail'] = max(pos['trail'], tp)
                if price <= pos['trail']: exited = True; reason = 'TRAIL'
            else:
                tp = entry*(1-nt/100)
                pos['trail'] = min(pos['trail'], tp)
                if price >= pos['trail']: exited = True; reason = 'TRAIL'
        elif d == 'LONG' and price <= pos['sl']:
            exited = True; reason = 'STOP'
        elif d == 'SELL' and price >= pos['sl']:
            exited = True; reason = 'STOP'

        # Target = BB middle
        if pos.get('target') and not exited:
            if d == 'LONG' and price >= pos['target']:
                exited = True; reason = 'TARGET'
            elif d == 'SHORT' and price <= pos['target']:
                exited = True; reason = 'TARGET'

        if exited:
            pnl_pct = (price-entry)/entry*100 if d=='LONG' else (entry-price)/entry*100
            pnl_rs = pnl_pct/100*entry*pos['qty']
            total_pnl += pnl_rs - CHARGES
            marker = "+" if pnl_rs > 0 else "-"
            print(f"  EXIT {marker} {d:5s} {sym:12s} {entry:.2f}->{price:.2f} {pnl_pct:+.3f}% Rs {pnl_rs:+,.0f} ({reason})", flush=True)
            trades.append({'sym':sym,'pnl_pct':pnl_pct,'pnl_rs':pnl_rs,'reason':reason})
            del positions[sym]

    # Scan for new signals
    signals = []
    # Batch fetch quotes for all stocks
    batch_size = 15
    for i in range(0, len(ALL_STOCKS), batch_size):
        batch = ALL_STOCKS[i:i+batch_size]
        for sym in batch:
            if sym in positions: continue
            bars15 = get_15min_bars(sym)
            if len(bars15) < 12: continue

            closes = [b['c'] for b in bars15]
            mid, upper, lower = bb_calc(closes, 10, 1.0)
            alm = alma_calc(closes, 9)

            b = bars15[-1]
            body_hi = max(b['o'], b['c'])
            body_lo = min(b['o'], b['c'])

            # BB width check
            bb_width = (upper[-1] - lower[-1]) / mid[-1] * 100 if mid[-1] > 0 else 0
            if bb_width < 1.0: continue

            signal = None
            if alm[-1] > mid[-1] and body_lo > upper[-1]:
                signal = 'SHORT'
            elif alm[-1] < mid[-1] and body_hi < lower[-1]:
                signal = 'LONG'

            if signal:
                score = abs(b['c'] - mid[-1]) / mid[-1] * 100 if mid[-1] > 0 else 0
                signals.append({
                    'sym': sym, 'dir': signal, 'score': score,
                    'price': b['c'], 'target': mid[-1],
                    'bb_width': bb_width, 'alma': alm[-1], 'mid': mid[-1],
                    'upper': upper[-1], 'lower': lower[-1],
                })
        time.sleep(0.3)  # rate limit

    print(f"  Signals found: {len(signals)}", flush=True)

    # Enter top 3
    if signals:
        signals.sort(key=lambda x: -x['score'])
        for sig in signals[:3]:
            if len(positions) >= 3: break
            sym = sig['sym']
            price = sig['price']
            d = sig['dir']
            qty = int(CAPITAL / 5 / price) if price > 0 else 0
            if qty <= 0: continue

            # Estimate ATR from bar ranges
            bars15 = get_15min_bars(sym)
            if len(bars15) < 5: continue
            avg_rng = np.mean([b['h']-b['l'] for b in bars15[-5:]])
            atr_abs = avg_rng

            sl_abs = atr_abs * 0.05
            trail_pct = max(atr_abs / price * 0.005 * 100, 0.10)
            sl_price = price - sl_abs if d == 'LONG' else price + sl_abs

            positions[sym] = {
                'dir': d, 'entry': price, 'qty': qty,
                'sl': sl_price, 'trail_pct': trail_pct,
                'trail': sl_price, 'mfe': 0.0,
                'target': sig['target'], 'atr_abs': atr_abs,
            }
            print(f"  ENTER {d:5s} {sym:12s} @ {price:.2f} SL={sl_price:.2f} target={sig['target']:.2f} BBw={sig['bb_width']:.2f}%", flush=True)

    # Summary
    wins = sum(1 for t in trades if t['pnl_rs'] > 0)
    total = len(trades)
    print(f"  Running: {len(positions)} open | Closed: {wins}W/{total-wins}L | P&L: Rs {total_pnl:+,.0f}", flush=True)

# EOD close remaining
print(f"\n--- EOD CLOSE at {datetime.now().strftime('%H:%M:%S')} ---", flush=True)
for sym in list(positions.keys()):
    pos = positions[sym]
    ltp_data = get_ltp([sym])
    price = ltp_data.get(sym, pos['entry'])
    d = pos['dir']; entry = pos['entry']
    pnl_pct = (price-entry)/entry*100 if d=='LONG' else (entry-price)/entry*100
    pnl_rs = pnl_pct/100*entry*pos['qty']
    total_pnl += pnl_rs - CHARGES
    marker = "+" if pnl_rs > 0 else "-"
    print(f"  EOD {marker} {d:5s} {sym:12s} {entry:.2f}->{price:.2f} {pnl_pct:+.3f}% Rs {pnl_rs:+,.0f}", flush=True)
    trades.append({'sym':sym,'pnl_pct':pnl_pct,'pnl_rs':pnl_rs,'reason':'EOD'})

wins = sum(1 for t in trades if t['pnl_rs'] > 0)
total = len(trades)
wr = wins/total*100 if total else 0
print(f"\n{'='*70}", flush=True)
print(f"BB+ALMA VALIDATION: {wins}W/{total-wins}L WR={wr:.0f}% Net=Rs {total_pnl:+,.0f}", flush=True)
print(f"{'='*70}", flush=True)
