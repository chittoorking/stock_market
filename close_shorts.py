"""Emergency: close naked shorts on INFY and TCS."""
from live.indmoney_client import place_order, get_ltp
ltp = get_ltp(["INFY","TCS"])
print("Closing naked shorts:")
p = ltp.get("INFY", 0)
oid = place_order("INFY", 40, "BUY", p, order_type="MARKET")
print(f"BUY 40 INFY @ {p} -> {oid}")
p = ltp.get("TCS", 0)
oid = place_order("TCS", 20, "BUY", p, order_type="MARKET")
print(f"BUY 20 TCS @ {p} -> {oid}")
