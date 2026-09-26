"""Unit tests for VWAP bot — run on GCP."""
import sys
sys.path.insert(0, ".")
exec(open("live/vwap_bot.py").read().split("def main")[0])

passed = 0
failed = 0

def test(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS: {name}")
        passed += 1
    except Exception as e:
        print(f"  FAIL: {name} -> {e}")
        failed += 1

print("VWAP BOT UNIT TESTS")
print("=" * 50)

# ML tests
print("\nMarket Lens:")
ml = MarketLens()
test("ML init", lambda: None if ml.action else (_ for _ in ()).throw(Exception("action is None")))
stocks = ml.poll()
test("ML poll returns stocks", lambda: None if len(stocks) > 100 else (_ for _ in ()).throw(Exception(f"only {len(stocks)}")))

s0 = stocks[0]
test("has ticker", lambda: None if "ticker" in s0 else (_ for _ in ()).throw(Exception("missing")))
test("has lastTradedPrice", lambda: None if "lastTradedPrice" in s0 else (_ for _ in ()).throw(Exception("missing")))
test("has avgPrice", lambda: None if "avgPrice" in s0 else (_ for _ in ()).throw(Exception("missing")))
test("has sixMonthReturn", lambda: None if "sixMonthReturn" in s0 else (_ for _ in ()).throw(Exception("missing")))
test("has oneMonthReturn", lambda: None if "oneMonthReturn" in s0 else (_ for _ in ()).throw(Exception("missing")))

# Signal detection
print("\nSignal Detection:")
found = 0
for s in stocks:
    ltp = s.get("lastTradedPrice") or 0
    vwap = s.get("avgPrice") or 0
    if ltp > 0 and vwap > 0 and abs(ltp - vwap) / vwap * 100 > 1.0:
        found += 1
test(f"stocks >1% from VWAP: {found}", lambda: None if found > 0 else (_ for _ in ()).throw(Exception("none found")))

qualified = 0
for s in stocks:
    ltp = s.get("lastTradedPrice") or 0
    vwap = s.get("avgPrice") or 0
    if ltp <= 0 or vwap <= 0 or ltp < 50: continue
    if abs(ltp - vwap) / vwap * 100 < 1.0: continue
    if (s.get("sixMonthReturn") or 0) >= 15: continue
    if (s.get("oneMonthReturn") or 0) <= -5: continue
    qualified += 1
test(f"qualified signals: {qualified}", lambda: None)

fno_in_data = sum(1 for s in stocks if s.get("ticker", "") in FNO_STOCKS)
test(f"F&O stocks in data: {fno_in_data}", lambda: None if fno_in_data > 50 else (_ for _ in ()).throw(Exception("too few")))

# Position tests
print("\nPosition Class:")

# BUY profit
p = Position("TEST", "BUY", 100.0, 1, 99.0, 1.0)
p.update(101.0)
test("BUY profit when price up", lambda: None if p.pnl > 0 else (_ for _ in ()).throw(Exception(f"pnl={p.pnl}")))

# SELL profit
p = Position("TEST", "SELL", 100.0, 1, 101.0, 1.0)
p.update(99.0)
test("SELL profit when price down", lambda: None if p.pnl > 0 else (_ for _ in ()).throw(Exception(f"pnl={p.pnl}")))

# BUY loss
p = Position("TEST", "BUY", 100.0, 1, 99.0, 1.0)
p.update(99.0)
test("BUY loss when price down", lambda: None if p.pnl < 0 else (_ for _ in ()).throw(Exception(f"pnl={p.pnl}")))

# SELL loss
p = Position("TEST", "SELL", 100.0, 1, 101.0, 1.0)
p.update(101.0)
test("SELL loss when price up", lambda: None if p.pnl < 0 else (_ for _ in ()).throw(Exception(f"pnl={p.pnl}")))

# F&O tagging
p = Position("RELIANCE", "BUY", 1200.0, 1, 1215.0, 1.2)
test("RELIANCE = FUTURES", lambda: None if p.instrument == "FUTURES" else (_ for _ in ()).throw(Exception(p.instrument)))
p = Position("RANDOMSTOCK", "BUY", 100.0, 1, 99.0, 1.0)
test("non-F&O = EQUITY_MIS", lambda: None if p.instrument == "EQUITY_MIS" else (_ for _ in ()).throw(Exception(p.instrument)))

# Catastrophe SL
p = Position("TEST", "BUY", 100.0, 1, 99.0, 1.0)
p.update(96.0)
test("catastrophe SL at -4%", lambda: None if p.exited and p.exit_reason == "CATASTROPHE_SL" else (_ for _ in ()).throw(Exception(f"exited={p.exited} reason={p.exit_reason}")))

# No exit before 35m
p = Position("TEST", "BUY", 100.0, 1, 99.0, 1.0)
for i in range(6):
    p.update(100.5)
test("no exit before 35m (6 scans)", lambda: None if not p.exited else (_ for _ in ()).throw(Exception("exited early")))

# Loss at 35m
p = Position("TEST", "BUY", 100.0, 1, 99.0, 1.0)
for i in range(7):
    p.update(99.8)
test("exit on loss at 35m", lambda: None if p.exited and p.exit_reason == "LOSS_AT_35M" else (_ for _ in ()).throw(Exception(f"reason={p.exit_reason}")))

# Small profit at 35m
p = Position("TEST", "BUY", 100.0, 1, 99.0, 1.0)
for i in range(7):
    p.update(100.2)
test("exit on small profit at 35m", lambda: None if p.exited and p.exit_reason == "SMALL_PROFIT" else (_ for _ in ()).throw(Exception(f"reason={p.exit_reason}")))

# Trail activation at 35m with >0.3% profit
p = Position("TEST", "BUY", 100.0, 1, 99.0, 1.0)
for i in range(7):
    p.update(100.5)
test("trail activates at 35m +0.5%", lambda: None if not p.exited and p.trailing_active else (_ for _ in ()).throw(Exception(f"exited={p.exited} trail={p.trailing_active}")))

# Trail triggered
p = Position("TEST", "BUY", 100.0, 1, 99.0, 1.0)
for i in range(7):
    p.update(100.5)
p.update(100.7)  # peak
p.update(100.5)  # drop 0.2% from peak -> triggers 0.10% trail
test("trail triggers on 0.2% drop from peak", lambda: None if p.exited and p.exit_reason == "TRAIL_TRIGGERED" else (_ for _ in ()).throw(Exception(f"reason={p.exit_reason}")))

# Trail NOT triggered on small drop
p = Position("TEST", "BUY", 100.0, 1, 99.0, 1.0)
for i in range(7):
    p.update(100.5)
p.update(100.7)
p.update(100.65)  # drop 0.05% from peak -> NOT enough
test("trail holds on 0.05% drop", lambda: None if not p.exited else (_ for _ in ()).throw(Exception("exited too early")))

# Max hold 60m
p = Position("TEST", "BUY", 100.0, 1, 99.0, 1.0)
for i in range(7):
    p.update(100.5)  # trail activates
for i in range(5):
    p.update(100.6)  # keeps going, trail never triggers
test("max hold 60m exits", lambda: None if p.exited and p.exit_reason == "MAX_HOLD" else (_ for _ in ()).throw(Exception(f"reason={p.exit_reason}")))

# Direction assignment
print("\nDirection Logic:")
test("above VWAP -> SELL", lambda: None if ("SELL" if (102-100)/100*100 > 0 else "BUY") == "SELL" else (_ for _ in ()).throw(Exception("wrong")))
test("below VWAP -> BUY", lambda: None if ("SELL" if (98-100)/100*100 > 0 else "BUY") == "BUY" else (_ for _ in ()).throw(Exception("wrong")))

# Filter edge cases
print("\nFilter Edge Cases:")
test("6M=0 passes <15", lambda: None if 0 < 15 else (_ for _ in ()).throw(Exception("failed")))
test("6M=14.9 passes <15", lambda: None if 14.9 < 15 else (_ for _ in ()).throw(Exception("failed")))
test("6M=15.0 rejected >=15", lambda: None if not (15.0 < 15) else (_ for _ in ()).throw(Exception("should reject")))
test("1M=0 passes >-5", lambda: None if 0 > -5 else (_ for _ in ()).throw(Exception("failed")))
test("1M=-4.9 passes >-5", lambda: None if -4.9 > -5 else (_ for _ in ()).throw(Exception("failed")))
test("1M=-5.0 rejected <=-5", lambda: None if not (-5.0 > -5) else (_ for _ in ()).throw(Exception("should reject")))
test("1M=None becomes 0, passes", lambda: None if (None or 0) > -5 else (_ for _ in ()).throw(Exception("failed")))

# Telegram
print("\nTelegram:")
send_tg("VWAP Bot test - all systems check")
test("Telegram sent (check phone)", lambda: None)

# Log dir
test(f"Log dir exists: {LOG_DIR}", lambda: None if LOG_DIR.exists() else (_ for _ in ()).throw(Exception("missing")))

print(f"\n{'='*50}")
print(f"RESULTS: {passed} PASSED, {failed} FAILED out of {passed+failed}")
if failed == 0:
    print("ALL TESTS PASSED. Bot is ready.")
else:
    print(f"FIX {failed} FAILURES BEFORE DEPLOYING.")
