"""Live BB+ALMA monitor — reads positions from broker, never hardcodes."""
import sys, io, time, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from live.indmoney_client import (get_ltp, place_order, get_historical_candles,
                                   get_positions, SCRIP_CODES, SYM_FROM_SEC_ID)
from datetime import datetime
import numpy as np

ALL_STOCKS = list(SCRIP_CODES.keys())
CAPITAL = 250_000
MAX_POS = 3
TRAIL_MULT = 0.25  # trail = ATR * 0.25 (~Rs 0.75 on Rs 200 stock, checked on bar close)
SL_MULT = 0.05     # SL = ATR * 0.05 (checked on 15-min bar close)
EMERGENCY_SL = 0.50 # emergency exit if price drops 0.5% mid-bar (immediate, no bar wait)

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

def load_broker_positions():
    """Read ACTUAL positions from broker — single source of truth."""
    result = {}
    try:
        broker_pos = get_positions()
        for p in broker_pos:
            sym = p.get("symbol", "")
            sec_id = str(p.get("security_id", ""))
            qty = int(p.get("net_qty", 0))
            if qty == 0: continue
            # Match by symbol first, then by security_id
            if sym not in SCRIP_CODES:
                mapped = SYM_FROM_SEC_ID.get(sec_id)
                if mapped:
                    print(f"  Mapped broker '{sym}' -> '{mapped}' via sec_id {sec_id}", flush=True)
                    sym = mapped
                else:
                    print(f"  SKIP {sym} (sec_id={sec_id}): not in scrip codes", flush=True)
                    continue
            avg = float(p.get("avg_price", 0))
            if avg <= 0: continue
            direction = "BUY" if qty > 0 else "SELL"
            result[sym] = {
                "dir": direction,
                "entry": avg,
                "qty": abs(qty),
                "mfe": 0.0,
            }
    except Exception as e:
        print(f"Error loading positions: {e}", flush=True)
    return result

# Load positions from broker on startup
positions = load_broker_positions()
exited_cooldown = {}  # sym -> exit_time, skip for 1 scan (30 min)
trades = []
last_print = 0
# If we already have positions from broker, don't scan immediately
# If empty, scan once on startup
last_scan = time.time() if positions else 0

print(f"BB+ALMA LIVE started at {datetime.now().strftime('%H:%M:%S')}", flush=True)
print(f"Broker positions: {positions}", flush=True)

last_bar_check = time.time()  # first bar check 15 min from now, not immediately

while datetime.now().hour < 15:
    # Get LTP for open positions
    if positions:
        ltp = get_ltp(list(positions.keys()))
    else:
        ltp = {}

    now_t = time.time()
    # Only check trail on 15-min bar boundaries (match backtest resolution)
    is_bar_close = (now_t - last_bar_check) >= 900  # 900s = 15 min

    # Monitor positions
    for sym in list(positions.keys()):
        pos = positions[sym]
        price = ltp.get(sym, 0)
        if price <= 0: continue
        ep = pos["entry"]; d = pos["dir"]
        atr_pct = pos.get("atr", 1.0)
        atr_abs = atr_pct / 100 * ep
        sl_abs = atr_abs * SL_MULT
        tr_abs = atr_abs * TRAIL_MULT

        if d == "BUY":
            fav = (price - ep) / ep * 100
        else:
            fav = (ep - price) / ep * 100
        pos["mfe"] = max(pos["mfe"], fav)

        # Update best price continuously (track high/low)
        best = pos.get("best", ep)
        if d == "BUY" and price > best: best = price
        elif d == "SELL" and price < best: best = price
        pos["best"] = best

        should_exit = False; reason = ""

        # Emergency SL: if price drops significantly mid-bar, exit immediately
        if d == "BUY" and price <= ep * (1 - EMERGENCY_SL/100): should_exit = True; reason = "EMERGENCY SL"
        elif d == "SELL" and price >= ep * (1 + EMERGENCY_SL/100): should_exit = True; reason = "EMERGENCY SL"

        # Regular SL and trail check on 15-min bar close (match backtest)
        if not should_exit and is_bar_close:
            # Fetch actual 15-min bar close from API
            bars15 = get_15min_bars(sym)
            if bars15:
                bar_close = bars15[-1]['c']
                bar_high = bars15[-1]['h']
                bar_low = bars15[-1]['l']
                # Update best from bar high/low
                if d == "BUY" and bar_high > best: best = bar_high; pos["best"] = best
                elif d == "SELL" and bar_low < best: best = bar_low; pos["best"] = best
                # SL check on bar close (not bar low — too tight for intraday noise)
                if d == "BUY" and bar_close <= ep - sl_abs: should_exit = True; reason = "STOP"
                elif d == "SELL" and bar_close >= ep + sl_abs: should_exit = True; reason = "STOP"
                # Trail check on bar close
                if not should_exit:
                    if d == "BUY":
                        trail_stop = best - tr_abs
                        if bar_close <= trail_stop and pos["mfe"] > 0: should_exit = True; reason = "TRAIL"
                    else:
                        trail_stop = best + tr_abs
                        if bar_close >= trail_stop and pos["mfe"] > 0: should_exit = True; reason = "TRAIL"
                # Use bar close as exit price
                if should_exit:
                    price = bar_close

        if should_exit:
            # Verify position still exists on broker before exiting
            broker_pos = load_broker_positions()
            if sym not in broker_pos:
                print(f"  {sym} not on broker — removing from tracking", flush=True)
                del positions[sym]
                continue

            exit_side = "SELL" if d == "BUY" else "BUY"
            actual_qty = broker_pos[sym]["qty"]
            oid = place_order(sym, actual_qty, exit_side, price, order_type="MARKET")
            pnl_pct = (price-ep)/ep*100 if d=="BUY" else (ep-price)/ep*100
            pnl_rs = pnl_pct / 100 * ep * actual_qty
            t = datetime.now().strftime("%H:%M:%S")
            m = "+" if pnl_rs > 0 else "-"
            print(f"{t} EXIT {m} {d} {sym} {ep:.2f}->{price:.2f} {pnl_pct:+.3f}% Rs {pnl_rs:+,.0f} ({reason}) oid={oid}", flush=True)
            trades.append({"sym":sym,"pnl_rs":pnl_rs})
            exited_cooldown[sym] = time.time()
            del positions[sym]

    if is_bar_close:
        last_bar_check = now_t

    # Scan for new entries every 30 min
    now_t = time.time()
    if now_t - last_scan > 1800 and len(positions) < MAX_POS:
        last_scan = now_t
        t = datetime.now().strftime("%H:%M")
        print(f"\n{t} SCANNING...", flush=True)
        signals = []
        for sym in ALL_STOCKS:
            if sym in positions: continue
            # 30-min cooldown after exit
            if sym in exited_cooldown and (time.time() - exited_cooldown[sym]) < 1800: continue
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
        cooled = sum(1 for s in exited_cooldown if (time.time()-exited_cooldown[s]) < 1800)
        print(f"  {len(signals)} signals ({cooled} in cooldown)", flush=True)
        if signals:
            signals.sort(key=lambda x: -x['score'])
            slots = MAX_POS - len(positions)
            for sig in signals[:slots]:
                sym = sig['sym']; price = sig['price']
                side = 'BUY' if sig['dir'] == 'LONG' else 'SELL'
                qty = int(CAPITAL/5/price) if price > 0 else 0
                if qty <= 0: continue
                oid = place_order(sym, qty, side, price, order_type='MARKET')
                if oid:
                    # Get ATR for trail/SL
                    bars15 = get_15min_bars(sym)
                    atr_p = 1.0
                    if len(bars15) >= 5:
                        ranges = [b['h']-b['l'] for b in bars15[-5:]]
                        atr_p = np.mean(ranges) / price * 100 if price > 0 else 1.0
                    positions[sym] = {"dir":side,"entry":price,"qty":qty,"mfe":0.0,"atr":atr_p,"best":price}
                    print(f"  ENTER {side} {qty} {sym} @ {price:.2f} -> {oid}", flush=True)

    # Print P&L every 30s
    now = time.time()
    if now - last_print > 30 and positions:
        total = 0; parts = []
        for sym, pos in positions.items():
            price = ltp.get(sym, 0)
            if price <= 0: continue
            d = pos["dir"]; ep = pos["entry"]
            pnl = (price-ep)/ep*100 if d=="BUY" else (ep-price)/ep*100
            rs = pnl/100*ep*pos["qty"]
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
    # Verify against broker
    broker_pos = load_broker_positions()
    for sym in list(positions.keys()):
        if sym not in broker_pos:
            print(f"  {sym} not on broker — skip", flush=True)
            continue
        pos = positions[sym]
        price_data = get_ltp([sym])
        price = price_data.get(sym, pos["entry"])
        exit_side = "SELL" if pos["dir"] == "BUY" else "BUY"
        actual_qty = broker_pos[sym]["qty"]
        oid = place_order(sym, actual_qty, exit_side, price, order_type="MARKET")
        d = pos["dir"]; ep = pos["entry"]
        pnl = (price-ep)/ep*100 if d=="BUY" else (ep-price)/ep*100
        pnl_rs = pnl/100*ep*actual_qty
        print(f"  EOD {sym} {pnl:+.3f}% Rs {pnl_rs:+,.0f} oid={oid}", flush=True)
        trades.append({"sym":sym,"pnl_rs":pnl_rs})

wins = sum(1 for t in trades if t["pnl_rs"] > 0)
total_pnl = sum(t["pnl_rs"] for t in trades)
print(f"\nBB+ALMA: {wins}W/{len(trades)-wins}L Rs {total_pnl:+,.0f}", flush=True)
