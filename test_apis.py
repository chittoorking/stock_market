"""Test all new API features live."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)

from live.indmoney_client import get_historical_candles, get_margin_required, get_funds, headers
import time, json

# 1. Historical candles
print("=== HISTORICAL CANDLES (RELIANCE 5min, last 1 hour) ===", flush=True)
now_ms = int(time.time() * 1000)
hour_ago = now_ms - 3600000
candles = get_historical_candles("RELIANCE", "5minute", hour_ago, now_ms)
if candles:
    for c in candles[:5]:
        print(f"  {c}", flush=True)
    print(f"  ... total {len(candles)} candles", flush=True)
else:
    print("  No candles returned", flush=True)

# 2. Try daily candles for 30 days
print("\n=== DAILY CANDLES (RELIANCE last 30 days) ===", flush=True)
month_ago = now_ms - 30 * 86400000
daily = get_historical_candles("RELIANCE", "1day", month_ago, now_ms)
if daily:
    for c in daily[:5]:
        print(f"  {c}", flush=True)
    print(f"  ... total {len(daily)} candles", flush=True)
else:
    print("  No daily candles", flush=True)

# 3. Margin calculation
print("\n=== MARGIN CHECK ===", flush=True)
m = get_margin_required("RELIANCE", 10, "BUY", 1330)
print(f"  Result: {json.dumps(m, default=str)}", flush=True)

funds = get_funds()
print(f"  Available funds: Rs {funds:,.0f}", flush=True)
if m:
    tm = m.get("total_margin", 0)
    print(f"  Margin needed: Rs {tm:,.0f}", flush=True)

# 4. Test smart order format (don't actually place)
print("\n=== SMART ORDER FORMAT (dry run) ===", flush=True)
print("  Entry: BUY RELIANCE @ MARKET", flush=True)
print("  SL: trigger=1320 limit=1319", flush=True)
print("  This would place entry + SL in one API call", flush=True)
print("  Exchange handles SL even if bot crashes", flush=True)

print("\nAll API tests complete", flush=True)
