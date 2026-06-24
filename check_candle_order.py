"""Check: is candles[-2] really the previous closed bar?"""
from live.indmoney_client import get_historical_candles
import time
from datetime import datetime

now_ms = int(time.time() * 1000)
start = now_ms - 1800000  # last 30 min
candles = get_historical_candles("RELIANCE", "5minute", start, now_ms)
if candles:
    print(f"Total candles: {len(candles)}", flush=True)
    for i, c in enumerate(candles):
        ts = datetime.fromtimestamp(c['ts']).strftime("%H:%M:%S")
        o = c['o']; cl = c['c']
        tag = " ← CURRENT (forming)" if i == len(candles)-1 else (" ← USE THIS (just closed)" if i == len(candles)-2 else "")
        print(f"  [{i}] {ts} open={o} close={cl}{tag}", flush=True)
else:
    print("Market closed — no candles. Will verify tomorrow during market hours.", flush=True)
    print("But the logic is: candles are sorted by time, last one is still forming,", flush=True)
    print("second-to-last is the most recently COMPLETED bar.", flush=True)
