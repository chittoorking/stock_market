"""Live BB+ALMA: monitor + scan for new entries all day."""
import sys, io, time, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from live.indmoney_client import get_ltp, place_order, get_historical_candles, SCRIP_CODES
from datetime import datetime
import numpy as np

ALL_STOCKS = list(SCRIP_CODES.keys())
CAPITAL = 250_000
MAX_POS = 3
TRAIL_PCT = 0.10

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

positions = {
    "INFY": {"dir":"BUY","entry":1036.20,"qty":40,"mfe":0.0},
    "TCS": {"dir":"BUY","entry":2071.40,"qty":20,"mfe":0.0},
    "APOLLOHOSP": {"dir":"BUY","entry":4715.00,"qty":9,"mfe":0.0},
}
trades = []
last_print = 0
last_scan = 0

print(f"BB+ALMA LIVE started at {datetime.now().strftime('%H:%M:%S')}", flush=True)
print(f"Positions: {list(positions.keys())}", flush=True)

while positions and datetime.now().hour < 15:
    ltp = get_ltp(list(positions.keys()))
    for sym in list(positions.keys()):
        pos = positions[sym]
        price = ltp.get(sym, 0)
        if price <= 0: continue
        ep = pos["entry"]
        fav = (price - ep) / ep * 100
        pos["mfe"] = max(pos["mfe"], fav)

        sl_price = ep * 0.999
        should_exit = False; reason = ""

        if pos["mfe"] > TRAIL_PCT:
            trail_level = ep * (1 + (pos["mfe"] - TRAIL_PCT) / 100)
            if price <= trail_level:
                should_exit = True; reason = "TRAIL"
        elif price <= sl_price:
            should_exit = True; reason = "STOP"

        if should_exit:
            oid = place_order(sym, pos["qty"], "SELL", price, order_type="MARKET")
            pnl = (price - ep) / ep * 100
            pnl_rs = pnl / 100 * ep * pos["qty"]
            t = datetime.now().strftime("%H:%M:%S")
            print(f"{t} EXIT {sym} {ep:.2f}->{price:.2f} {pnl:+.3f}% Rs {pnl_rs:+,.0f} ({reason}) oid={oid}", flush=True)
            del positions[sym]

    # Scan for new entries every 30 min
    now_t = time.time()
    if now_t - last_scan > 1800 and len(positions) < MAX_POS:
        last_scan = now_t
        t = datetime.now().strftime("%H:%M")
        print(f"\n{t} SCANNING for new BB signals...", flush=True)
        signals = []
        for sym in ALL_STOCKS:
            if sym in positions: continue
            bars15 = get_15min_bars(sym)
            if len(bars15) < 12: continue
            closes = [b['c'] for b in bars15]
            mid, upper, lower = bb_calc(closes)
            alm = alma_calc(closes)
            b = bars15[-1]
            body_hi = max(b['o'], b['c']); body_lo = min(b['o'], b['c'])
            bb_width = (upper[-1]-lower[-1])/mid[-1]*100 if mid[-1]>0 else 0
            if bb_width < 1.0: continue
            signal = None
            if alm[-1] > mid[-1] and body_lo > upper[-1]: signal = 'SHORT'
            elif alm[-1] < mid[-1] and body_hi < lower[-1]: signal = 'LONG'
            if signal:
                score = abs(b['c']-mid[-1])/mid[-1]*100 if mid[-1]>0 else 0
                signals.append({'sym':sym,'dir':signal,'score':score,'price':b['c']})
            time.sleep(0.1)
        print(f"  Found {len(signals)} signals", flush=True)
        if signals:
            signals.sort(key=lambda x: -x['score'])
            slots = MAX_POS - len(positions)
            for sig in signals[:slots]:
                sym = sig['sym']; price = sig['price']; side = sig['dir']
                exit_side = 'SELL' if side == 'BUY' else 'BUY'
                qty = int(CAPITAL/5/price) if price > 0 else 0
                if qty <= 0: continue
                oid = place_order(sym, qty, side, price, order_type='MARKET')
                if oid:
                    sl_price = price*0.999 if side=='BUY' else price*1.001
                    positions[sym] = {"dir":side,"entry":price,"qty":qty,"mfe":0.0}
                    print(f"  ENTER {side} {qty} {sym} @ {price:.2f} -> {oid}", flush=True)
                    trades.append({'sym':sym,'action':'ENTER','price':price})

    # Print P&L every 30s
    now = time.time()
    if now - last_print > 30:
        total = 0
        parts = []
        for sym, pos in positions.items():
            price = ltp.get(sym, 0)
            if price <= 0: continue
            pnl = (price - pos["entry"]) / pos["entry"] * 100
            rs = pnl / 100 * pos["entry"] * pos["qty"]
            total += rs
            parts.append(f"{sym}={pnl:+.2f}%")
        t = datetime.now().strftime("%H:%M")
        mfe_str = ' '.join(f'{s}={positions[s]["mfe"]:.2f}%' for s in positions)
        print(f"{t} | {', '.join(parts)} | Rs {total:+,.0f} | mfe: {mfe_str}", flush=True)
        last_print = now

    time.sleep(2)

# 3:15 PM force close
if positions:
    print("EOD FORCE CLOSE", flush=True)
    ltp = get_ltp(list(positions.keys()))
    for sym in list(positions.keys()):
        pos = positions[sym]
        price = ltp.get(sym, pos["entry"])
        oid = place_order(sym, pos["qty"], "SELL", price, order_type="MARKET")
        pnl = (price - pos["entry"]) / pos["entry"] * 100
        pnl_rs = pnl / 100 * pos["entry"] * pos["qty"]
        print(f"EOD {sym} {pnl:+.3f}% Rs {pnl_rs:+,.0f} oid={oid}", flush=True)

print("Monitor done", flush=True)
