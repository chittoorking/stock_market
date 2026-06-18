"""Simulate full MTF day on TODAY's live candle data."""
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from live.indmoney_client import get_historical_candles

now_ms = int(time.time() * 1000)
open_ms = now_ms - 7*3600*1000

stocks = ["RELIANCE","HDFCBANK","INFY","TCS","SBIN","TATAMOTORS","ICICIBANK",
          "AXISBANK","SUNPHARMA","WIPRO","BPCL","ONGC","HCLTECH","NTPC",
          "BAJFINANCE","KOTAKBANK","TITAN","HINDALCO","TATASTEEL","JSWSTEEL",
          "MARUTI","CIPLA","DRREDDY","BHARTIARTL","ITC","LT","ADANIENT",
          "APOLLOHOSP","HEROMOTOCO","ULTRACEMCO"]

all_bars = {}
for sym in stocks:
    candles = get_historical_candles(sym, "5minute", open_ms, now_ms)
    if candles:
        all_bars[sym] = [{'o':c['o'],'h':c['h'],'l':c['l'],'c':c['c']} for c in candles]

print(f"Got bars for {len(all_bars)} stocks", flush=True)

CAPITAL = 250000; CHARGES = 60
total_pnl = 0; total_wins = 0; total_trades = 0

# Scan every 30 min: bar 9=10:00, 15=10:30, 21=11:00 etc
scan_bars = [9,15,21,27,33,39,45,51,57]

for sb in scan_bars:
    sigs = []
    for sym, bars in all_bars.items():
        if len(bars) < sb + 8: continue
        op = bars[0]['o']
        cur = bars[sb]['c']
        hi = max(b['h'] for b in bars[:sb+1])
        lo = min(b['l'] for b in bars[:sb+1])
        if op <= 0 or cur <= 0: continue
        trend = (cur - op) / op * 100
        if abs(trend) < 0.2: continue
        if trend > 0.2:
            pb = (hi - cur) / hi * 100
            if pb > 0.1:
                sigs.append({'sym':sym,'dir':'LONG','trend':trend,'score':abs(trend),'bars':bars,'price':cur})
        elif trend < -0.2:
            bn = (cur - lo) / lo * 100
            if bn > 0.1:
                sigs.append({'sym':sym,'dir':'SHORT','trend':trend,'score':abs(trend),'bars':bars,'price':cur})

    sigs.sort(key=lambda x: -x['score'])
    picks = sigs[:3]
    hour = 10 + (sb - 9) // 6
    minute = ((sb - 9) % 6) * 5
    scan_t = f"{hour}:{minute:02d}"

    for p in picks:
        bars = p['bars']
        ep = p['price']
        d = p['dir']
        sym = p['sym']
        best = ep
        sl_abs = ep * 0.001
        tr_abs = ep * 0.001
        stop = (ep - sl_abs) if d == 'LONG' else (ep + sl_abs)
        lb = min(sb+13, len(bars))
        ex = bars[lb-1]['c']
        for b in bars[sb+1:lb]:
            if d == 'LONG':
                if b['h'] > best: best = b['h']; stop = best - tr_abs
                if b['l'] <= stop: ex = stop; break
            else:
                if b['l'] < best: best = b['l']; stop = best + tr_abs
                if b['h'] >= stop: ex = stop; break
        pp = (ex-ep)/ep*100 if d=='LONG' else (ep-ex)/ep*100
        qty = int(CAPITAL/5/ep)
        pnl_rs = pp/100*qty*ep
        total_pnl += pnl_rs - CHARGES
        total_trades += 1
        if pp > 0: total_wins += 1
        marker = "+" if pp > 0 else "-"
        print(f"{scan_t:>5s} {marker} {d:5s} {sym:12s} {ep:>8.2f}->{ex:>8.2f} {pp:+.3f}% Rs {pnl_rs:+,.0f}", flush=True)

wr = total_wins/total_trades*100 if total_trades else 0
print(f"\nTODAY MTF: {total_wins}W/{total_trades-total_wins}L WR={wr:.0f}% Net=Rs {total_pnl:+,.0f}", flush=True)
