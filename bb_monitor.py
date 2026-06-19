"""Trail monitor for live BB+ALMA positions."""
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from live.indmoney_client import get_ltp, place_order
from datetime import datetime

positions = {
    "INFY": {"dir":"BUY","entry":1036.20,"qty":40,"mfe":0.0},
    "TCS": {"dir":"BUY","entry":2071.40,"qty":20,"mfe":0.0},
    "APOLLOHOSP": {"dir":"BUY","entry":4715.00,"qty":9,"mfe":0.0},
}

TRAIL_PCT = 0.10

print(f"Monitor started at {datetime.now().strftime('%H:%M:%S')}", flush=True)
print(f"Positions: {list(positions.keys())}", flush=True)

last_print = 0

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
        print(f"{t} | {', '.join(parts)} | Rs {total:+,.0f} | mfe={[f'{s}={p[\"mfe\"]:.2f}%' for s,p in positions.items()]}", flush=True)
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
