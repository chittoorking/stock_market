"""Flatten all positions to zero."""
from live.indmoney_client import place_order, get_positions
pos = get_positions()
for p in pos:
    sym = p.get("symbol","")
    qty = p.get("net_qty", 0)
    if qty > 0:
        oid = place_order(sym, qty, "SELL", 0, order_type="MARKET")
        print(f"SELL {qty} {sym} -> {oid}")
    elif qty < 0:
        oid = place_order(sym, abs(qty), "BUY", 0, order_type="MARKET")
        print(f"BUY {abs(qty)} {sym} -> {oid}")
    else:
        print(f"{sym}: flat (realized Rs {p.get('realized_profit',0)})")
