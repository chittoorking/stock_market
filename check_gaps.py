"""Check today's gap fill status."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from live.indmoney_client import get_full_quote, headers
import requests

stocks = ["JSWSTEEL","HDFCLIFE","HINDUNILVR","INDUSINDBK","GRASIM"]
q = get_full_quote(stocks)

print("=== GAP FILL STATUS ===", flush=True)
for sym in stocks:
    d = q.get(sym, {})
    pc = d.get("prev_close", 0)
    op = d.get("open", 0)
    cur = d.get("last_price", 0)
    if pc > 0 and op > 0:
        gap = (op - pc) / pc * 100
        direction = "LONG(buy)" if gap < 0 else "SHORT(sell)"
        if gap < 0:
            filled = "YES" if cur > pc else "NO"
        else:
            filled = "YES" if cur < pc else "NO"
        move_from_open = (cur - op) / op * 100
        print(f"  {sym:12s} gap={gap:+.2f}% dir={direction} open={op} now={cur} move={move_from_open:+.2f}% filled={filled}", flush=True)

print("", flush=True)
r = requests.get("https://api.indstocks.com/funds", headers=headers(), timeout=10)
data = r.json().get("data", {})
rpnl = data.get("realized_pnl", 0)
upnl = data.get("unrealized_pnl", 0)
charges = data.get("eq_charges", 0)
print(f"Realized: Rs {rpnl:+,.0f}", flush=True)
print(f"Unrealized: Rs {upnl:+,.0f}", flush=True)
print(f"Charges: Rs {charges:,.0f}", flush=True)
print(f"Net: Rs {rpnl+upnl-charges:+,.0f}", flush=True)
