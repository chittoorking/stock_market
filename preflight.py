"""Pre-flight check — validate everything before live trading."""
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)

from live.indmoney_client import get_funds, get_ltp, get_full_quote, get_market_depth, get_positions

checks = []

# 1. Token / Funds
print("=== 1. TOKEN & FUNDS ===", flush=True)
funds = get_funds()
ok = funds > 0
checks.append(ok)
print(f"  Funds: Rs {funds:,.0f} {'OK' if ok else 'FAIL'}", flush=True)

# 2. LTP
print("\n=== 2. LTP ===", flush=True)
ltp = get_ltp(["RELIANCE", "HDFCBANK", "SBIN"])
ok = len(ltp) >= 2
checks.append(ok)
print(f"  {ltp} {'OK' if ok else 'FAIL'}", flush=True)

# 3. Full quote with open price
print("\n=== 3. FULL QUOTE ===", flush=True)
q = get_full_quote(["RELIANCE"])
r = q.get("RELIANCE", {})
ok = r.get("open", 0) > 0
checks.append(ok)
print(f"  open={r.get('open')} high={r.get('high')} ltp={r.get('last_price')} pc={r.get('prev_close')} {'OK' if ok else 'FAIL'}", flush=True)

# 4. Market depth
print("\n=== 4. MARKET DEPTH ===", flush=True)
d = get_market_depth(["RELIANCE"])
rd = d.get("RELIANCE", {})
ok = rd.get("buy_pct", 0) > 0
checks.append(ok)
print(f"  buy={rd.get('buy_pct')}% sell={rd.get('sell_pct')}% spread={rd.get('spread_pct', 0):.4f}% {'OK' if ok else 'FAIL'}", flush=True)

# 5. Positions clear
print("\n=== 5. OPEN POSITIONS ===", flush=True)
pos = get_positions()
ok = len(pos) == 0
checks.append(ok)
status = "CLEAR" if ok else f"WARNING: {len(pos)} positions open!"
print(f"  {status}", flush=True)

# 6. Capital pool state
print("\n=== 6. CAPITAL POOL ===", flush=True)
pool_file = os.path.expanduser("~/trading-bot/data/capital_state.json")
if os.path.exists(pool_file):
    with open(pool_file) as f:
        pool = json.load(f)
    sessions = pool.get("sessions", {})
    if sessions:
        print(f"  WARNING: sessions allocated: {sessions}", flush=True)
        pool["sessions"] = {}
        with open(pool_file, "w") as f:
            json.dump(pool, f, indent=2)
        print("  Cleared stale sessions", flush=True)
        checks.append(True)
    else:
        print("  Clean", flush=True)
        checks.append(True)
else:
    print("  No state file (fresh start)", flush=True)
    checks.append(True)

# 7. Smart order function
print("\n=== 7. SMART ORDER FUNCTION ===", flush=True)
from live.indmoney_client import place_smart_order
ok = callable(place_smart_order)
checks.append(ok)
print(f"  place_smart_order available: {'OK' if ok else 'FAIL'}", flush=True)

# 8. Today's gaps
print("\n=== 8. TODAY'S GAPS ===", flush=True)
stocks = ["RELIANCE","HDFCBANK","INFY","TCS","SBIN","TATAMOTORS","ICICIBANK",
          "AXISBANK","SUNPHARMA","WIPRO","BPCL","ONGC","HCLTECH","TITAN"]
quotes = get_full_quote(stocks)
gap_count = 0
for sym in stocks:
    qq = quotes.get(sym, {})
    pc = qq.get("prev_close", 0)
    op = qq.get("open", 0)
    if pc > 0 and op > 0:
        gap = (op - pc) / pc * 100
        if abs(gap) >= 0.5:
            gap_count += 1
            print(f"  {sym:12s} gap={gap:+.2f}%", flush=True)
print(f"  Total gaps >= 0.5%: {gap_count}", flush=True)

# Summary
print("\n" + "=" * 50, flush=True)
passed = sum(checks)
total = len(checks)
if passed == total:
    print(f"ALL {total} CHECKS PASSED - READY FOR LIVE", flush=True)
else:
    failed = [i+1 for i, c in enumerate(checks) if not c]
    print(f"{passed}/{total} PASSED - CHECKS {failed} FAILED", flush=True)
