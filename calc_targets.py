"""Calculate exit targets for net profit."""
from live.indmoney_client import get_ltp, headers
import requests

ltp = get_ltp(["INDUSINDBK","JSWSTEEL","HDFCLIFE"])
r = requests.get("https://api.indstocks.com/funds", headers=headers(), timeout=10)
data = r.json().get("data", {})
rpnl = data.get("realized_pnl", 0)
charges = data.get("eq_charges", 0)
net_so_far = rpnl - charges

print(f"Realized so far: Rs {rpnl:+,.0f}")
print(f"Charges so far: Rs {charges:,.0f}")
print(f"Net so far: Rs {net_so_far:+,.0f}")
print()

trades = [
    ("INDUSINDBK", "BUY", 60, 922.45),
    ("JSWSTEEL", "BUY", 14, 1277.00),
    ("HDFCLIFE", "SELL", 69, 597.60),
]

exit_charges = 180  # 3 exits x Rs 60 each
need = -net_so_far + exit_charges + 50  # cover losses + charges + Rs 50 profit
print(f"Need Rs {need:,.0f} total from 3 trades to end at +Rs 50 net")
per_trade = need / 3
print(f"= Rs {per_trade:,.0f} per trade")
print()

for sym, side, qty, entry in trades:
    cur = ltp.get(sym, 0)
    if side == "BUY":
        cur_pnl = (cur - entry) * qty
        target = entry + per_trade / qty
        sl = entry - 100 / qty
        target_move = target - entry
        sl_move = entry - sl
    else:
        cur_pnl = (entry - cur) * qty
        target = entry - per_trade / qty
        sl = entry + 100 / qty
        target_move = entry - target
        sl_move = sl - entry
    print(f"{sym}: {side} {qty}@{entry:.2f} now={cur:.2f} pnl=Rs {cur_pnl:+,.0f}")
    print(f"  TARGET: {target:.2f} (need Rs {target_move:.2f}/share)")
    print(f"  SL:     {sl:.2f} (max Rs 100 loss)")
    print()
