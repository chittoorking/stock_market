"""Quick scan: what's tradeable right now?"""
import sys, io, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from live.indmoney_client import get_full_quote, get_historical_candles, SCRIP_CODES
import numpy as np, time

ALL = list(SCRIP_CODES.keys())

def alma_calc(data, window=9, offset=0.85, sigma=6):
    m=offset*(window-1); s=window/sigma
    weights=[math.exp(-((i-m)**2)/(2*s*s)) for i in range(window)]
    ws=sum(weights); weights=[w/ws for w in weights]
    r=[]
    for i in range(len(data)):
        if i<window-1: r.append(data[i])
        else: r.append(sum(data[i-window+1+j]*weights[j] for j in range(window)))
    return r

def bb_calc(closes, period=10, std_mult=1.0):
    mid=[]; upper=[]; lower=[]
    for i in range(len(closes)):
        if i<period-1: mid.append(closes[i]); upper.append(closes[i]); lower.append(closes[i])
        else:
            w=closes[i-period+1:i+1]; m=np.mean(w); s=np.std(w)
            mid.append(m); upper.append(m+std_mult*s); lower.append(m-std_mult*s)
    return mid, upper, lower

def get_15min_bars(sym):
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 30*3600*1000
    candles = get_historical_candles(sym, "5minute", start_ms, now_ms)
    if not candles: return []
    bars15 = []
    for i in range(0, len(candles)-2, 3):
        chunk = candles[i:i+3]
        bars15.append({'o':chunk[0]['o'],'h':max(c['h'] for c in chunk),
                       'l':min(c['l'] for c in chunk),'c':chunk[-1]['c']})
    return bars15

print("Scanning all 90 stocks for BB+ALMA signals...", flush=True)
signals = []
near_signals = []

for sym in ALL:
    bars15 = get_15min_bars(sym)
    if len(bars15) < 12: continue
    closes = [b['c'] for b in bars15]
    mid, upper, lower = bb_calc(closes)
    alm = alma_calc(closes)
    b = bars15[-1]
    body_hi = max(b['o'], b['c']); body_lo = min(b['o'], b['c'])
    bb_width = (upper[-1]-lower[-1])/mid[-1]*100 if mid[-1]>0 else 0

    # Distance from BB bands
    dist_upper = (body_lo - upper[-1]) / upper[-1] * 100 if upper[-1] > 0 else 0
    dist_lower = (lower[-1] - body_hi) / lower[-1] * 100 if lower[-1] > 0 else 0
    alma_vs_mid = "ABOVE" if alm[-1] > mid[-1] else "BELOW"

    # Full signal
    if bb_width >= 1.0:
        if alm[-1] > mid[-1] and body_lo > upper[-1]:
            signals.append(("SHORT", sym, b['c'], dist_upper, bb_width, alma_vs_mid))
        elif alm[-1] < mid[-1] and body_hi < lower[-1]:
            signals.append(("LONG", sym, b['c'], dist_lower, bb_width, alma_vs_mid))

    # Near signals (close to BB but not fully outside yet)
    if bb_width >= 0.5:
        if dist_upper > -0.3 and alm[-1] > mid[-1]:
            near_signals.append(("near SHORT", sym, b['c'], dist_upper, bb_width))
        elif dist_lower > -0.3 and alm[-1] < mid[-1]:
            near_signals.append(("near LONG", sym, b['c'], dist_lower, bb_width))
    time.sleep(0.1)

print(f"\n=== BB+ALMA SIGNALS (body fully outside) ===", flush=True)
if signals:
    for sig, sym, price, dist, bbw, alma in signals:
        print(f"  {sig:6s} {sym:12s} @ {price:.2f} dist={dist:+.2f}% BBw={bbw:.1f}% ALMA={alma}", flush=True)
else:
    print("  None — no stocks fully outside BB right now", flush=True)

print(f"\n=== NEAR SIGNALS (close to BB edge) ===", flush=True)
if near_signals:
    for sig, sym, price, dist, bbw in near_signals[:10]:
        print(f"  {sig:12s} {sym:12s} @ {price:.2f} dist={dist:+.2f}% BBw={bbw:.1f}%", flush=True)
else:
    print("  None", flush=True)
