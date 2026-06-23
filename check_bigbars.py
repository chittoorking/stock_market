"""Check if big bars exist in the last hour."""
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from live.indmoney_client import get_historical_candles, SCRIP_CODES

now_ms = int(time.time() * 1000)
start_ms = now_ms - 7200000  # last 2 hours

count = 0
signals = 0
ALL = list(SCRIP_CODES.keys())

for sym in ALL:
    candles = get_historical_candles(sym, "5minute", start_ms, now_ms)
    if not candles or len(candles) < 3:
        continue
    for i in range(1, len(candles)):
        c = candles[i]
        body = c['c'] - c['o']
        bp = abs(body) / c['c'] * 100 if c['c'] > 0 else 0
        if bp > 0.7:
            count += 1
            prev = candles[i-1]
            prev_body = prev['c'] - prev['o']
            isolated = prev_body * body < 0
            d = "BUY" if body < 0 else "SELL"
            if isolated:
                signals += 1
                print(f"  SIGNAL: {sym:12s} body={bp:.2f}% {d} isolated=True", flush=True)
            else:
                print(f"  skip:   {sym:12s} body={bp:.2f}% {d} isolated=False (trend)", flush=True)
    time.sleep(0.1)

print(f"", flush=True)
print(f"Big bars (>0.7%) in last 2 hours: {count}", flush=True)
print(f"Isolated (tradeable): {signals}", flush=True)
print(f"Filtered out (trend): {count - signals}", flush=True)
