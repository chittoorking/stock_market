"""Simulate all 5 sessions on today June 16 data via yfinance."""
import yfinance as yf, time, sys
import numpy as np

STOCKS = {
    'ADANIENT':'ADANIENT.NS', 'ADANIPORTS':'ADANIPORTS.NS', 'APOLLOHOSP':'APOLLOHOSP.NS',
    'ASIANPAINT':'ASIANPAINT.NS', 'AXISBANK':'AXISBANK.NS', 'BAJAJ-AUTO':'BAJAJ-AUTO.NS',
    'BAJFINANCE':'BAJFINANCE.NS', 'BAJAJFINSV':'BAJAJFINSV.NS', 'BPCL':'BPCL.NS',
    'BHARTIARTL':'BHARTIARTL.NS', 'BRITANNIA':'BRITANNIA.NS', 'CIPLA':'CIPLA.NS',
    'COALINDIA':'COALINDIA.NS', 'DIVISLAB':'DIVISLAB.NS', 'DRREDDY':'DRREDDY.NS',
    'EICHERMOT':'EICHERMOT.NS', 'GRASIM':'GRASIM.NS', 'HCLTECH':'HCLTECH.NS',
    'HDFCBANK':'HDFCBANK.NS', 'HDFCLIFE':'HDFCLIFE.NS', 'HEROMOTOCO':'HEROMOTOCO.NS',
    'HINDALCO':'HINDALCO.NS', 'HINDUNILVR':'HINDUNILVR.NS', 'ICICIBANK':'ICICIBANK.NS',
    'ITC':'ITC.NS', 'INDUSINDBK':'INDUSINDBK.NS', 'INFY':'INFY.NS', 'JSWSTEEL':'JSWSTEEL.NS',
    'KOTAKBANK':'KOTAKBANK.NS', 'LT':'LT.NS', 'M&M':'M&M.NS', 'MARUTI':'MARUTI.NS',
    'NTPC':'NTPC.NS', 'NESTLEIND':'NESTLEIND.NS', 'ONGC':'ONGC.NS', 'POWERGRID':'POWERGRID.NS',
    'RELIANCE':'RELIANCE.NS', 'SBILIFE':'SBILIFE.NS', 'SBIN':'SBIN.NS',
    'SHRIRAMFIN':'SHRIRAMFIN.NS', 'SUNPHARMA':'SUNPHARMA.NS', 'TCS':'TCS.NS',
    'TATACONSUM':'TATACONSUM.NS', 'TATAMOTORS':'TATAMOTORS.NS', 'TATASTEEL':'TATASTEEL.NS',
    'TECHM':'TECHM.NS', 'TITAN':'TITAN.NS', 'UPL':'UPL.NS', 'ULTRACEMCO':'ULTRACEMCO.NS',
    'WIPRO':'WIPRO.NS',
    # NIFTY Next 50
    'ABB':'ABB.NS', 'ADANIENSOL':'ADANIENSOL.NS', 'ADANIGREEN':'ADANIGREEN.NS',
    'ADANIPOWER':'ADANIPOWER.NS', 'AMBUJACEM':'AMBUJACEM.NS', 'BAJAJHLDNG':'BAJAJHLDNG.NS',
    'BANKBARODA':'BANKBARODA.NS', 'BOSCHLTD':'BOSCHLTD.NS', 'CANBK':'CANBK.NS',
    'CGPOWER':'CGPOWER.NS', 'CHOLAFIN':'CHOLAFIN.NS', 'CUMMINSIND':'CUMMINSIND.NS',
    'DLF':'DLF.NS', 'DMART':'DMART.NS', 'GAIL':'GAIL.NS', 'GODREJCP':'GODREJCP.NS',
    'HAL':'HAL.NS', 'HDFCAMC':'HDFCAMC.NS', 'HINDZINC':'HINDZINC.NS', 'INDHOTEL':'INDHOTEL.NS',
    'IOC':'IOC.NS', 'IRFC':'IRFC.NS', 'JINDALSTEL':'JINDALSTEL.NS', 'LODHA':'LODHA.NS',
    'LTIM':'LTIMINDTECH.NS', 'MOTHERSON':'MOTHERSON.NS', 'MUTHOOTFIN':'MUTHOOTFIN.NS',
    'PFC':'PFC.NS', 'PIDILITIND':'PIDILITIND.NS', 'PNB':'PNB.NS', 'RECLTD':'RECLTD.NS',
    'SHREECEM':'SHREECEM.NS', 'SIEMENS':'SIEMENS.NS', 'SOLARINDS':'SOLARINDS.NS',
    'TATAPOWER':'TATAPOWER.NS', 'TORNTPHARM':'TORNTPHARM.NS', 'TVSMOTOR':'TVSMOTOR.NS',
    'UNIONBANK':'UNIONBANK.NS', 'VBL':'VBL.NS',
}

print(f'Downloading 5-min data for {len(STOCKS)} stocks individually...')
raw = {}
errors = []
for i, (sym, ytk) in enumerate(STOCKS.items()):
    try:
        t = yf.Ticker(ytk)
        df = t.history(period='5d', interval='5m', auto_adjust=True)
        if not df.empty:
            raw[sym] = df
        else:
            errors.append(sym)
    except Exception:
        errors.append(sym)
    if (i+1) % 10 == 0:
        sys.stdout.write(f'  {i+1}/{len(STOCKS)} done...\n')
        sys.stdout.flush()
    time.sleep(0.05)

print(f'Got data: {len(raw)} stocks  |  Failed: {errors}')
print()


def bars_for(sym, date='2026-06-16'):
    """Return list of {t(IST), o, h, l, c, v} for a given date."""
    if sym not in raw:
        return []
    df = raw[sym]
    out = []
    for ts, row in df.iterrows():
        ts_str = str(ts)
        if date not in ts_str:
            continue
        ist = ts_str[11:16]
        out.append({'t': ist,
                    'o': float(row['Open']), 'h': float(row['High']),
                    'l': float(row['Low']),  'c': float(row['Close']),
                    'v': int(row.get('Volume', 0))})
    return sorted(out, key=lambda x: x['t'])


def hist_bars(sym, date='2026-06-16'):
    """All bars BEFORE today — for ATR calculation."""
    if sym not in raw:
        return []
    df = raw[sym]
    out = []
    for ts, row in df.iterrows():
        ts_str = str(ts)
        if date in ts_str:
            continue
        out.append({'o': float(row['Open']), 'h': float(row['High']),
                    'l': float(row['Low']),  'c': float(row['Close'])})
    return out


def prev_close(sym, date='2026-06-16'):
    if sym not in raw:
        return 0
    df = raw[sym]
    closes = [float(row['Close']) for ts, row in df.iterrows() if date not in str(ts)]
    return closes[-1] if closes else 0


def atr_pct(bars, n=14):
    if len(bars) < 2:
        return 0
    trs = [max(bars[i]['h'] - bars[i]['l'],
               abs(bars[i]['h'] - bars[i-1]['c']),
               abs(bars[i]['l'] - bars[i-1]['c'])) for i in range(1, len(bars))]
    if not trs:
        return 0
    atr = np.mean(trs[-n:])
    return atr / bars[-1]['c'] * 100 if bars[-1]['c'] > 0 else 0


def simulate(bars, entry_idx, direction, atr_p, capital=100000):
    if entry_idx >= len(bars):
        return 0
    ep = bars[entry_idx]['o']
    if ep <= 0:
        return 0
    sl = atr_p * 0.05 / 100 * ep
    trail = max(atr_p * 0.005 / 100 * ep, ep * 0.0001)
    best = ep
    stop = (ep - sl) if direction == 'LONG' else (ep + sl)

    for b in bars[entry_idx+1:]:
        if direction == 'LONG':
            if b['h'] > best:
                best = b['h']
                stop = best - trail
            if b['l'] <= stop:
                return (stop - ep) / ep * capital
        else:
            if b['l'] < best:
                best = b['l']
                stop = best + trail
            if b['h'] >= stop:
                return (ep - stop) / ep * capital

    xp = bars[-1]['c']
    return ((xp - ep) if direction == 'LONG' else (ep - xp)) / ep * capital


def run_session(label, gap_time, min_gap=0.5, top_n=10):
    is_s1 = gap_time == '09:15'
    cands = []

    for sym in raw:
        bday = bars_for(sym)
        if not bday:
            continue
        idx = next((i for i, b in enumerate(bday) if b['t'] == gap_time), None)
        if idx is None:
            continue

        if is_s1:
            pc = prev_close(sym)
            if pc <= 0:
                continue
            gap = (bday[idx]['o'] - pc) / pc * 100
        else:
            if idx == 0:
                continue
            pc = bday[idx-1]['c']
            if pc <= 0:
                continue
            gap = (bday[idx]['o'] - pc) / pc * 100

        if abs(gap) < min_gap:
            continue
        # Use prior-day bars for ATR (today's bars before entry often empty at 9:15)
        prior = hist_bars(sym) if idx < 14 else bday[:idx]
        atr = atr_pct(prior if prior else bday[:idx])
        if atr < 0.2:
            continue

        direction = 'LONG' if gap < 0 else 'SHORT'
        cands.append({'sym': sym, 'gap': gap, 'dir': direction,
                      'score': abs(gap) * atr, 'atr': atr,
                      'idx': idx, 'bars': bday})

    if not cands:
        return 0, 0, 0, []

    cands.sort(key=lambda x: -x['score'])
    sel = cands[:top_n]
    total = 0
    wins = 0
    trades = []
    for c in sel:
        pnl = simulate(c['bars'], c['idx'], c['dir'], c['atr'])
        total += pnl
        if pnl > 0:
            wins += 1
        trades.append((c['sym'], c['gap'], c['dir'], pnl))
    return total, wins, len(sel), trades


# Quick sanity check
sample_sym = list(raw.keys())[0]
sample_bars = bars_for(sample_sym)
print(f'Sanity check ({sample_sym}): {len(sample_bars)} bars today')
if sample_bars:
    print(f'  First: {sample_bars[0]["t"]}  Last: {sample_bars[-1]["t"]}  '
          f'  Open: {sample_bars[0]["o"]:.1f}')
print()

SESSIONS = [
    ('S1 Gap Fill',  '09:15'),
    ('S4 MG-10:15', '10:15'),
    ('S5 MG-11:15', '11:15'),
    ('S6 MG-12:15', '12:15'),
    ('S7 MG-13:15', '13:15'),
]

grand = 0
print(f'{"Session":<16} {"N":>4} {"W/L":>5} {"WR%":>5} {"P&L Rs":>10}  Details')
print('-'*90)
for label, t in SESSIONS:
    pnl, wins, n, trades = run_session(label, t)
    wr = wins/n*100 if n else 0
    grand += pnl
    if trades:
        details = '  '.join(f'{s}({d[0]}){p:+.0f}' for s,g,d,p in sorted(trades, key=lambda x:-x[3])[:4])
    else:
        details = 'no signals'
    tag = ' <-- actual Rs +276' if label == 'S1 Gap Fill' else ''
    print(f'{label:<16} {n:>4} {wins:>3}/{max(n-wins,0):<2} {wr:>4.0f}% {pnl:>+10,.0f}  {details}{tag}')

print('-'*90)
print(f'{"ALL SESSIONS":<16} {"":<10} {grand:>+10,.0f}  (simulated from 9:15 entry)')
print()
print(f'Actual S1 ran late (9:20): Rs +276')
gap = grand - pnl  # grand minus S1 sim = microgap contribution
_, _, _, trades_s1 = run_session('S1 Gap Fill', '09:15')
s1_pnl = sum(p for _,_,_,p in trades_s1) if trades_s1 else 0
print(f'S1 sim (9:15 sharp):       Rs {s1_pnl:+,.0f}')
print(f'S4-S7 microgaps:           Rs {grand-s1_pnl:+,.0f}')
print(f'Grand total (sim):         Rs {grand:+,.0f}')
