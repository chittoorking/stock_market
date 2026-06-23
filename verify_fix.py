"""Verify the fixed scan_gaps would pick correct stocks."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from live.indmoney_client import get_full_quote, get_ltp, SCRIP_CODES

ALL = list(SCRIP_CODES.keys())
quotes = get_full_quote(ALL)

gaps = []
for sym in ALL:
    q = quotes.get(sym, {})
    prev = q.get('prev_close', 0)
    op = q.get('open', 0)
    cur = q.get('last_price', 0)
    if prev <= 0 or op <= 0: continue
    gap = (op - prev) / prev * 100
    if abs(gap) < 0.5: continue
    d = 'LONG' if gap < 0 else 'SHORT'
    move = (cur - op) / op * 100
    gaps.append((sym, gap, d, prev, op, cur, move))

gaps.sort(key=lambda x: -abs(x[1]))
print("FIXED scan would pick top 5:", flush=True)
for i, (sym, gap, d, prev, op, cur, move) in enumerate(gaps[:5]):
    print(f"  #{i+1}: {sym:12s} gap={gap:+.2f}% {d} prev={prev:.2f} open={op:.2f} now={cur:.2f} move={move:+.2f}%", flush=True)

print("", flush=True)
print("Total stocks with gap >= 0.5%: " + str(len(gaps)), flush=True)
print("", flush=True)

# Verify prev_close values are correct
print("Spot check prev_close:", flush=True)
for sym in ["INDUSINDBK", "HDFCLIFE", "GRASIM", "APOLLOHOSP", "KOTAKBANK"]:
    q = quotes.get(sym, {})
    print(f"  {sym:12s} prev_close={q.get('prev_close',0)} open={q.get('open',0)}", flush=True)
