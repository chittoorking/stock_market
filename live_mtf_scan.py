"""Live MTF scan — validate on real market right now."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from live.indmoney_client import get_full_quote
from datetime import datetime

stocks = ["RELIANCE","HDFCBANK","INFY","TCS","SBIN","TATAMOTORS","ICICIBANK",
          "AXISBANK","SUNPHARMA","WIPRO","BPCL","ONGC","HCLTECH","TITAN",
          "NTPC","BAJFINANCE","KOTAKBANK","HINDALCO","TATASTEEL","JSWSTEEL",
          "MARUTI","CIPLA","DRREDDY","BHARTIARTL","ITC","LT","ADANIENT",
          "ULTRACEMCO","HEROMOTOCO","APOLLOHOSP"]

print(f"=== LIVE MTF SCAN at {datetime.now().strftime('%H:%M:%S')} ===", flush=True)

quotes = get_full_quote(stocks)
signals = []

print(f"{'Sym':12s} {'Open':>8s} {'Cur':>8s} {'Trend':>7s} {'Hi':>8s} {'Lo':>8s} {'PBfromHi':>8s} {'Signal'}", flush=True)
print("=" * 90, flush=True)

for sym in stocks:
    q = quotes.get(sym, {})
    op = q.get('open', 0)
    hi = q.get('high', 0)
    lo = q.get('low', 0)
    cur = q.get('last_price', 0)
    if op <= 0 or cur <= 0 or hi <= 0 or lo <= 0:
        continue

    trend = (cur - op) / op * 100
    pb_hi = (hi - cur) / hi * 100
    bn_lo = (cur - lo) / lo * 100

    signal = "-"
    if trend > 0.2 and pb_hi > 0.1:
        signal = "BUY dip"
        signals.append({'sym': sym, 'dir': 'LONG', 'trend': trend, 'score': abs(trend)})
    elif trend < -0.2 and bn_lo > 0.1:
        signal = "SHORT bounce"
        signals.append({'sym': sym, 'dir': 'SHORT', 'trend': trend, 'score': abs(trend)})

    marker = ">>>" if signal != "-" else "   "
    print(f"{marker} {sym:12s} {op:>8.2f} {cur:>8.2f} {trend:>+6.2f}% {hi:>8.2f} {lo:>8.2f} {pb_hi:>7.2f}% {signal}", flush=True)

print(f"\nTotal signals: {len(signals)}", flush=True)
if signals:
    signals.sort(key=lambda x: -x['score'])
    print("\nTop 3 picks:", flush=True)
    for s in signals[:3]:
        print(f"  {s['dir']:5s} {s['sym']:12s} trend={s['trend']:+.2f}%", flush=True)

    # Check: would we have made money if we entered 30 min ago?
    print("\n=== WOULD-HAVE-BEEN P&L (if entered 30 min ago) ===", flush=True)
    print("(Can't verify without historical intraday bars from today)", flush=True)
    print("But trend direction + pullback signals are live and real", flush=True)
