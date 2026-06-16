#!/usr/bin/env python3
"""Simulate what would have happened if we entered at 9:15 today."""
import sys, time
sys.path.insert(0, '.')
from live import indmoney_client as api
import numpy as np

stocks = list(api.SCRIP_CODES.keys())
quotes = api.get_full_quote(stocks)

now = int(time.time() * 1000)
start = now - 86400 * 1000

CAP = 200000; LEV = 5; TOTAL = CAP * LEV; CHARGES = 47

# Get 5-min candles and prev close for all stocks
all_data = {}
for sym in stocks:
    pc = quotes.get(sym, {}).get('prev_close', 0)
    if pc <= 0: continue
    candles = api.get_historical_candles(sym, '5minute', start, now)
    if not candles or len(candles) < 5: continue
    bars = []
    for c in candles:
        if isinstance(c, dict):
            bars.append({
                'open': c.get('o', 0), 'high': c.get('h', 0),
                'low': c.get('l', 0), 'close': c.get('c', 0),
                'volume': c.get('v', 0)
            })
    if len(bars) < 5: continue
    gap = (bars[0]['open'] - pc) / pc * 100
    all_data[sym] = {'bars': bars, 'prev_close': pc, 'gap': gap}
    time.sleep(0.1)

print(f'Loaded {len(all_data)} stocks with today candles')
print()

# Compute ATR from last 14 days daily data
daily_atr = {}
for sym in all_data:
    end = now
    st = now - 30 * 86400 * 1000
    candles = api.get_historical_candles(sym, '1day', st, end)
    if candles and len(candles) >= 10:
        bars_d = []
        for c in candles:
            if isinstance(c, dict):
                bars_d.append({'high': c.get('h', 0), 'low': c.get('l', 0), 'close': c.get('c', 0)})
        if len(bars_d) >= 10:
            trs = []
            for i in range(1, len(bars_d)):
                tr = max(bars_d[i]['high'] - bars_d[i]['low'],
                         abs(bars_d[i]['high'] - bars_d[i-1]['close']),
                         abs(bars_d[i]['low'] - bars_d[i-1]['close']))
                trs.append(tr)
            price = bars_d[-1]['close']
            if price > 0:
                daily_atr[sym] = np.mean(trs[-14:]) / price * 100
    time.sleep(0.1)

# Simulate gap fill at bar 0 (9:15)
def sim(bars, direction, eb, atr):
    if eb >= len(bars): return None
    entry = bars[eb]['open']
    if entry <= 0: return None
    sl = atr * 0.05; tr = atr * 0.005; mfe = 0; bs = -sl
    for k in range(eb + 1, min(eb + 20, len(bars))):
        fav = (bars[k]['high'] - entry) / entry * 100 if direction == 'LONG' else (entry - bars[k]['low']) / entry * 100
        mfe = max(mfe, fav)
        if mfe > tr: bs = max(bs, mfe - tr)
        if direction == 'LONG':
            if bars[k]['low'] <= entry * (1 + bs / 100): return bs, k
        else:
            if bars[k]['high'] >= entry * (1 - bs / 100): return bs, k
    ek = min(eb + 19, len(bars) - 1)
    ep = bars[ek]['close']
    pnl = (ep - entry) / entry * 100 if direction == 'LONG' else (entry - ep) / entry * 100
    return pnl, ek

# Score and select
SECTORS = {
    'ITC':'FMCG','BHARTIARTL':'Tel','TCS':'IT','CIPLA':'Pharma','NESTLEIND':'FMCG',
    'BRITANNIA':'FMCG','NTPC':'Pwr','SUNPHARMA':'Pharma','HDFCLIFE':'Ins',
    'ICICIBANK':'Bank','TATACONSUM':'FMCG','APOLLOHOSP':'Health','MARUTI':'Auto',
    'EICHERMOT':'Auto','INFY':'IT','SBIN':'Bank','WIPRO':'IT','TATASTEEL':'Metal',
    'HINDALCO':'Metal','M&M':'Auto','HDFCBANK':'Bank','AXISBANK':'Bank',
    'INDUSINDBK':'Bank','KOTAKBANK':'Bank','LT':'Infra','TATAMOTORS':'Auto',
    'JSWSTEEL':'Metal','TECHM':'IT','HCLTECH':'IT','DRREDDY':'Pharma',
    'BAJFINANCE':'Fin','ULTRACEMCO':'Cem','POWERGRID':'Pwr','ONGC':'Oil',
    'ADANIENT':'Cong','BPCL':'Oil','RELIANCE':'Cong','DIVISLAB':'Pharma',
    'HEROMOTOCO':'Auto','BAJAJ-AUTO':'Auto','TITAN':'Retail','SBILIFE':'Ins',
    'HINDUNILVR':'FMCG','BAJAJFINSV':'Fin','TATAPOWER':'Pwr',
}

candidates = []
for sym, d in all_data.items():
    if abs(d['gap']) < 0.5: continue
    atr = daily_atr.get(sym, 2.0)
    direction = 'SHORT' if d['gap'] > 0 else 'LONG'
    ev = 0.20  # default (no fill history)
    score = ev * abs(d['gap'])
    gap_vs_atr = abs(d['gap']) / atr if atr > 0 else 1
    candidates.append({
        'sym': sym, 'gap': d['gap'], 'direction': direction,
        'score': score, 'atr': atr, 'gap_vs_atr': gap_vs_atr,
        'sector': SECTORS.get(sym, 'Other'), 'bars': d['bars']
    })

candidates.sort(key=lambda x: x['score'], reverse=True)
selected = []; sec_count = {}
for c in candidates:
    sec = c['sector']
    if sec_count.get(sec, 0) >= 2: continue
    selected.append(c)
    sec_count[sec] = sec_count.get(sec, 0) + 1
    if len(selected) >= 10: break

if len(selected) < 5:
    print(f'Only {len(selected)} qualify. Not enough for basket.')
else:
    # Weighted allocation
    raw_w = [max(0.70 * c['gap_vs_atr'], 0.03) for c in selected]
    total_w = sum(raw_w)
    weights = [w / total_w for w in raw_w]

    print(f'SIMULATED 9:15 ENTRY — {len(selected)} stocks')
    print(f'{"#":<3} {"Stock":<12} {"Gap%":>6} {"Dir":<5} {"Entry":>10} {"Exit":>10} {"PnL%":>8} {"Rs":>8} {"Bar":>4} {"Reason":<10}')
    print('-' * 80)

    total_pnl = 0
    wins = 0; losses = 0
    for i, (c, w) in enumerate(zip(selected, weights)):
        atr = c['atr']
        result = sim(c['bars'], c['direction'], 0, atr)
        if result is None:
            print(f'{i+1:<3} {c["sym"]:<12} {c["gap"]:>+5.1f}% {c["direction"]:<5} NO TRADE')
            continue
        pnl, exit_bar = result
        entry = c['bars'][0]['open']
        pos_size = TOTAL * w
        pnl_rs = pnl / 100 * pos_size - CHARGES * 2
        total_pnl += pnl_rs
        marker = '+' if pnl > 0 else '-'
        if pnl > 0: wins += 1
        else: losses += 1

        if c['direction'] == 'SHORT':
            exit_price = entry * (1 - pnl / 100)
        else:
            exit_price = entry * (1 + pnl / 100)

        reason = 'TRAIL' if pnl >= 0 else 'STOP'
        print(f'{marker}{i+1:<2} {c["sym"]:<12} {c["gap"]:>+5.1f}% {c["direction"]:<5} {entry:>10.2f} {exit_price:>10.2f} {pnl:>+7.3f}% {pnl_rs:>+7,.0f} {exit_bar:>4} {reason}')

    print('-' * 80)
    print(f'TOTAL: {wins}W/{losses}L ({wins/(wins+losses)*100:.0f}% WR) | Rs {total_pnl:+,.0f}')
