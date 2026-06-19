"""BB+ALMA v3 — uses EXACT gap fill trail logic, polls every 15 min.
No new inventions. Same exit that's proven in gap fill, just applied to BB signals."""
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
EMERGENCY_SL_PCT = 0.50  # only mid-bar protection

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
    result = {}
    try:
        for p in get_positions():
            sym = p.get("symbol", "")
            sec_id = str(p.get("security_id", ""))
            qty = int(p.get("net_qty", 0))
            if qty == 0: continue
            if sym not in SCRIP_CODES:
                mapped = SYM_FROM_SEC_ID.get(sec_id)
                if mapped: sym = mapped
                else: continue
            avg = float(p.get("avg_price", 0))
            if avg <= 0: continue
            result[sym] = {"dir": "BUY" if qty > 0 else "SELL",
                           "entry": avg, "qty": abs(qty), "mfe": 0.0, "best": avg}
    except Exception as e:
        print(f"Error loading positions: {e}", flush=True)
    return result

# State
positions = load_broker_positions()
exited_cooldown = {}
trades = []

print(f"BB+ALMA v3 started at {datetime.now().strftime('%H:%M:%S')}", flush=True)
print(f"Broker positions: {list(positions.keys()) if positions else 'empty'}", flush=True)

# Timing
last_scan = time.time() if positions else 0
SCAN_INTERVAL = 900   # scan every 15 min (same as bar check)
BAR_INTERVAL = 900    # 15 min
last_bar_check = time.time()

while datetime.now().hour < 15:
    now_t = time.time()

    # === EMERGENCY SL CHECK (every 2s, tick level) ===
    if positions:
        ltp = get_ltp(list(positions.keys()))
        for sym in list(positions.keys()):
            pos = positions[sym]
            price = ltp.get(sym, 0)
            if price <= 0: continue
            ep = pos["entry"]; d = pos["dir"]
            loss_pct = (ep - price)/ep*100 if d == "BUY" else (price - ep)/ep*100
            if loss_pct > EMERGENCY_SL_PCT:
                # Verify on broker
                bp = load_broker_positions()
                if sym not in bp: del positions[sym]; continue
                exit_side = "SELL" if d == "BUY" else "BUY"
                oid = place_order(sym, bp[sym]["qty"], exit_side, price, order_type="MARKET")
                pnl_pct = -loss_pct
                pnl_rs = pnl_pct/100*ep*pos["qty"]
                print(f"{datetime.now().strftime('%H:%M:%S')} EMERGENCY {sym} {ep:.2f}->{price:.2f} {pnl_pct:+.2f}% Rs {pnl_rs:+,.0f} oid={oid}", flush=True)
                trades.append({"sym":sym,"pnl_rs":pnl_rs})
                exited_cooldown[sym] = now_t
                del positions[sym]

    # === 15-MIN BAR CHECK (gap fill trail logic on candle) ===
    if now_t - last_bar_check >= BAR_INTERVAL and positions:
        last_bar_check = now_t
        for sym in list(positions.keys()):
            pos = positions[sym]
            bars15 = get_15min_bars(sym)
            if not bars15: continue
            b = bars15[-1]  # latest 15-min bar
            ep = pos["entry"]; d = pos["dir"]
            best = pos["best"]

            # ATR from recent bars
            if len(bars15) >= 5:
                atr_abs = np.mean([bar['h']-bar['l'] for bar in bars15[-5:]])
            else:
                atr_abs = b['h'] - b['l']
            sl_abs = atr_abs * 0.05
            tr_abs = max(atr_abs * 0.005, ep * 0.10 / 100)

            # EXACT gap fill logic: update best from bar high/low, then check SL
            should_exit = False; reason = ""
            if d == "BUY":
                if b['h'] > best: best = b['h']; pos["best"] = best
                stop = best - tr_abs
                if b['l'] <= max(stop, ep - sl_abs): should_exit = True; reason = "TRAIL" if b['l'] <= stop else "STOP"
            else:
                if b['l'] < best: best = b['l']; pos["best"] = best
                stop = best + tr_abs
                if b['h'] >= min(stop, ep + sl_abs): should_exit = True; reason = "TRAIL" if b['h'] >= stop else "STOP"

            # Update MFE
            fav = (b['h']-ep)/ep*100 if d=="BUY" else (ep-b['l'])/ep*100
            pos["mfe"] = max(pos["mfe"], fav)

            if should_exit:
                bp = load_broker_positions()
                if sym not in bp:
                    print(f"  {sym} not on broker — removing", flush=True)
                    del positions[sym]; continue
                exit_side = "SELL" if d == "BUY" else "BUY"
                exit_price = b['c']  # exit at bar close price
                oid = place_order(sym, bp[sym]["qty"], exit_side, exit_price, order_type="MARKET")
                pnl_pct = (exit_price-ep)/ep*100 if d=="BUY" else (ep-exit_price)/ep*100
                pnl_rs = pnl_pct/100*ep*pos["qty"]
                m = "+" if pnl_rs > 0 else "-"
                print(f"{datetime.now().strftime('%H:%M:%S')} EXIT {m} {d} {sym} {ep:.2f}->{exit_price:.2f} {pnl_pct:+.3f}% Rs {pnl_rs:+,.0f} ({reason}) mfe={pos['mfe']:.2f}% oid={oid}", flush=True)
                trades.append({"sym":sym,"pnl_rs":pnl_rs})
                exited_cooldown[sym] = now_t
                del positions[sym]

        # Print status
        if positions:
            ltp = get_ltp(list(positions.keys()))
            parts = []
            total = 0
            for sym, pos in positions.items():
                price = ltp.get(sym, 0)
                if price <= 0: continue
                d = pos["dir"]; ep = pos["entry"]
                pnl = (price-ep)/ep*100 if d=="BUY" else (ep-price)/ep*100
                rs = pnl/100*ep*pos["qty"]
                total += rs
                parts.append(f"{sym}={pnl:+.2f}%")
            t = datetime.now().strftime("%H:%M")
            print(f"{t} BAR CHECK | {', '.join(parts)} | Rs {total:+,.0f}", flush=True)

    # === SCAN FOR NEW ENTRIES (every 30 min) ===
    if now_t - last_scan >= SCAN_INTERVAL and len(positions) < MAX_POS:
        last_scan = now_t
        t = datetime.now().strftime("%H:%M")
        signals = []
        for sym in ALL_STOCKS:
            if sym in positions: continue
            if sym in exited_cooldown and (now_t - exited_cooldown[sym]) < 900: continue
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

        cooled = sum(1 for s in exited_cooldown if (now_t-exited_cooldown[s]) < 1800)
        print(f"\n{t} SCAN: {len(signals)} signals ({cooled} cooldown)", flush=True)

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
                    bars15 = get_15min_bars(sym)
                    positions[sym] = {"dir":side,"entry":price,"qty":qty,"mfe":0.0,"best":price}
                    print(f"  ENTER {side} {qty} {sym} @ {price:.2f} -> {oid}", flush=True)

    time.sleep(2)

# EOD close
if positions:
    print("\nEOD FORCE CLOSE", flush=True)
    bp = load_broker_positions()
    for sym in list(positions.keys()):
        if sym not in bp: continue
        pos = positions[sym]
        ltp_data = get_ltp([sym])
        price = ltp_data.get(sym, pos["entry"])
        exit_side = "SELL" if pos["dir"] == "BUY" else "BUY"
        oid = place_order(sym, bp[sym]["qty"], exit_side, price, order_type="MARKET")
        pnl = (price-pos["entry"])/pos["entry"]*100 if pos["dir"]=="BUY" else (pos["entry"]-price)/pos["entry"]*100
        pnl_rs = pnl/100*pos["entry"]*pos["qty"]
        print(f"  EOD {sym} {pnl:+.3f}% Rs {pnl_rs:+,.0f} oid={oid}", flush=True)
        trades.append({"sym":sym,"pnl_rs":pnl_rs})

wins = sum(1 for t in trades if t["pnl_rs"] > 0)
total_pnl = sum(t["pnl_rs"] for t in trades)
print(f"\nBB+ALMA v3: {wins}W/{len(trades)-wins}L Rs {total_pnl:+,.0f}", flush=True)
