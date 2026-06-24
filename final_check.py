"""Final system check — everything must pass."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, ".")

checks = []

# 1. Token
from live.indmoney_client import get_funds, get_full_quote, tick_round, get_positions
funds = get_funds()
ok = funds > 0
checks.append(("Token + Funds", ok, f"Rs {funds:,.0f}"))

# 2. tick_round
ok = tick_round(1277.37) == 1277.4 and tick_round(197.83) == 197.85
checks.append(("tick_round", ok, f"{tick_round(1277.37)}, {tick_round(197.83)}"))

# 3. prev_close from fresh quote
q = get_full_quote(["RELIANCE"])
r = q.get("RELIANCE", {})
pc = r.get("prev_close", 0)
ok = pc > 0
checks.append(("prev_close fresh", ok, f"RELIANCE prev_close={pc}"))

# 4. Positions clean
pos = get_positions()
opens = [p for p in pos if int(p.get("net_qty", 0)) != 0]
ok = len(opens) == 0
checks.append(("No open positions", ok, f"{len(opens)} open"))

# 5. Imports
from live.basket_trader import BasketTrader, ENABLE_GAP_FILL, ENABLE_FLIPS, ENABLE_BIGBAR, ALL_STOCKS
from live.bigbar_reversal import BigBarTrader
ok = ENABLE_GAP_FILL and ENABLE_FLIPS and ENABLE_BIGBAR
checks.append(("Strategies enabled", ok, f"GF={ENABLE_GAP_FILL} FL={ENABLE_FLIPS} BB={ENABLE_BIGBAR}"))

# 6. BasketTrader creates
bt = BasketTrader(paper_mode=True)
ok = bt is not None
checks.append(("BasketTrader creates", ok, "OK"))

# 7. Monitor has exit checks
import inspect
src = inspect.getsource(bt.monitor_ws)
has_exit = "should_exit" in src and "exit_position" in src and "TRAIL" in src and "STOP LOSS" in src
checks.append(("monitor_ws has exit logic", has_exit, "TRAIL+SL checks present"))

# 8. scan_gaps uses fresh prev_close
src2 = inspect.getsource(bt.scan_gaps)
uses_fresh = "q.get('prev_close'" in src2 or 'q.get("prev_close"' in src2
checks.append(("scan_gaps fresh prev_close", uses_fresh, "Uses quote prev_close"))

# 9. exit_position uses LIMIT first
src3 = inspect.getsource(bt.exit_position)
has_limit = "order_type='LIMIT'" in src3 or 'order_type="LIMIT"' in src3
checks.append(("exit uses LIMIT first", has_limit, "LIMIT + MARKET fallback"))

# 10. BigBarTrader creates
bbt = BigBarTrader(250000, None, None, paper_mode=True)
ok = bbt is not None
checks.append(("BigBarTrader creates", ok, "OK"))

# Print results
print("=" * 60, flush=True)
print("FINAL PRE-FLIGHT CHECK", flush=True)
print("=" * 60, flush=True)
all_pass = True
for name, passed, detail in checks:
    status = "PASS" if passed else "FAIL"
    if not passed: all_pass = False
    print(f"  {'✓' if passed else '✗'} {name:35s} {status:4s}  {detail}", flush=True)

print("=" * 60, flush=True)
if all_pass:
    print("ALL 10 CHECKS PASSED — READY FOR TOMORROW", flush=True)
else:
    failed = [n for n, p, d in checks if not p]
    print(f"FAILED: {failed}", flush=True)
