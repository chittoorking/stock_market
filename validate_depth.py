"""Validate order book data quality during live market."""
from live.indmoney_client import get_market_depth, get_full_quote
import json, time

stocks = ['RELIANCE','HDFCBANK','INFY','TCS','SBIN','TATAMOTORS','ICICIBANK','AXISBANK','SUNPHARMA','WIPRO']
depth = get_market_depth(stocks)
quotes = get_full_quote(stocks)

print('Sym          BuyQty      SellQty  Buy%  Sell% Spread%  Gap%   Signal')
print('=' * 80)
for sym in stocks:
    d = depth.get(sym, {})
    q = quotes.get(sym, {})
    pc = q.get('prev_close', 0)
    gap = (q.get('open', 0) - pc) / pc * 100 if pc else 0
    bp = d.get('buy_pct', 50)
    sp = d.get('sell_pct', 50)
    tb = d.get('total_buy', 0)
    ts = d.get('total_sell', 0)
    spr = d.get('spread_pct', 0)
    signal = 'SELL OK' if sp > 60 else ('BUY OK' if bp > 60 else 'NEUTRAL')
    print(f'{sym:12s} {tb:>10,} {ts:>10,} {bp:5.1f} {sp:5.1f} {spr:7.4f} {gap:+5.2f} {signal}')

print()
print('=== DEPTH STABILITY (RELIANCE, 3 samples, 5s apart) ===')
for i in range(3):
    d = get_market_depth(['RELIANCE'])
    r = d.get('RELIANCE', {})
    print(f'  t={i*5}s: buy={r.get("buy_pct",0):.1f}% sell={r.get("sell_pct",0):.1f}%')
    if i < 2: time.sleep(5)

print()
print('=== FULL DEPTH DETAIL (RELIANCE) ===')
q = quotes.get('RELIANCE', {})
md = q.get('market_depth', {})
for key, val in md.items():
    agg = val.get('aggregate', {})
    print(f'  {key}:')
    print(f'    {json.dumps(agg, default=str)[:400]}')
    # Also check individual levels
    for level_name in ['buy_depth', 'sell_depth', 'best_bid', 'best_ask']:
        lv = val.get(level_name)
        if lv:
            print(f'    {level_name}: {json.dumps(lv, default=str)[:200]}')
