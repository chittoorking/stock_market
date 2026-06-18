"""Verify actual charges from INDmoney API."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from live.indmoney_client import get_margin_required

tests = [
    ("RELIANCE", 10, 1330, "BUY"),
    ("RELIANCE", 10, 1330, "SELL"),
    ("RELIANCE", 50, 1330, "BUY"),
    ("RELIANCE", 50, 1330, "SELL"),
    ("HDFCBANK", 50, 790, "BUY"),
    ("HDFCBANK", 50, 790, "SELL"),
    ("SBIN", 100, 1030, "BUY"),
    ("SBIN", 100, 1030, "SELL"),
    ("TATAMOTORS", 200, 395, "BUY"),
    ("TATAMOTORS", 200, 395, "SELL"),
]

print("Sym          Qty   Price  Side  Margin     Brok    STT   Total", flush=True)
print("=" * 75, flush=True)

buy_charges = {}
sell_charges = {}

for sym, qty, price, side in tests:
    m = get_margin_required(sym, qty, side, price)
    if m:
        total = m['charges']
        brok = m['brokerage']
        stt = m['stt']
        margin = m['total_margin']
        key = (sym, qty, price)
        if side == 'BUY':
            buy_charges[key] = total
        else:
            sell_charges[key] = total
        print(f"{sym:12s} {qty:>4} {price:>6} {side:4s}  Rs {margin:>8,.0f}  {brok:>6.1f}  {stt:>5.1f}  {total:>6.1f}", flush=True)
    else:
        print(f"{sym:12s} {qty:>4} {price:>6} {side:4s}  FAILED", flush=True)

# Calculate round trip costs
print("\n=== ROUND TRIP (BUY + SELL) ===", flush=True)
for key in buy_charges:
    if key in sell_charges:
        sym, qty, price = key
        rt = buy_charges[key] + sell_charges[key]
        notional = qty * price
        pct = rt / notional * 100
        print(f"{sym:12s} {qty:>4} x Rs {price} = Rs {notional:>8,} | Charges: Rs {rt:>6.1f} ({pct:.3f}%)", flush=True)

print("\n=== COMPARISON WITH OLD Rs 84 ASSUMPTION ===", flush=True)
print("Old assumption: Rs 84 per round trip (Zerodha STT-heavy)", flush=True)
print("Reality: see above", flush=True)
