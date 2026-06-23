"""Check ALL stocks: real gaps today and did they fill?"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from live.indmoney_client import get_full_quote, SCRIP_CODES

ALL = list(SCRIP_CODES.keys())
q = get_full_quote(ALL)

gaps = []
for sym in ALL:
    d = q.get(sym, {})
    pc = d.get("prev_close", 0)
    op = d.get("open", 0)
    cur = d.get("last_price", 0)
    hi = d.get("high", 0)
    lo = d.get("low", 0)
    if pc <= 0 or op <= 0: continue
    gap = (op - pc) / pc * 100
    if abs(gap) < 0.5: continue
    # Did it fill? For gap up: did price come back to prev_close?
    if gap > 0:
        filled = "YES" if lo <= pc else "NO"
        direction = "SHORT(fade)"
        move = (cur - op) / op * 100
    else:
        filled = "YES" if hi >= pc else "NO"
        direction = "LONG(fade)"
        move = (cur - op) / op * 100
    gaps.append((sym, gap, direction, op, pc, cur, move, filled))

gaps.sort(key=lambda x: -abs(x[1]))
print(f"Stocks with gap >= 0.5% today: {len(gaps)}", flush=True)
print(f"", flush=True)
print(f"{'Sym':12s} {'Gap%':>7s} {'Dir':15s} {'Open':>8s} {'PrevCl':>8s} {'Now':>8s} {'Move%':>7s} {'Filled?'}", flush=True)
print("=" * 80, flush=True)
filled_count = 0
for sym, gap, d, op, pc, cur, move, filled in gaps:
    m = ">>>" if filled == "YES" else "   "
    print(f"{m} {sym:12s} {gap:>+6.2f}% {d:15s} {op:>8.2f} {pc:>8.2f} {cur:>8.2f} {move:>+6.2f}% {filled}", flush=True)
    if filled == "YES": filled_count += 1

print(f"", flush=True)
print(f"Total gaps: {len(gaps)}", flush=True)
print(f"Filled: {filled_count}", flush=True)
print(f"Fill rate: {filled_count/len(gaps)*100:.0f}%" if gaps else "N/A", flush=True)
