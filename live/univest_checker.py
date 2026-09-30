"""
Univest Signal Checker — LOCKED STRATEGY
Score>=4 + RSI 40-75 + Skip ADX 35-50 + Stock CE only + 20% SL cap

Usage: python univest_checker.py SBIN
       python univest_checker.py RELIANCE HDFCBANK TATASTEEL
"""
import sys
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance")
    sys.exit(1)


def compute_ema(s, p):
    return s.ewm(span=p, adjust=False).mean()


def compute_rsi(s, p=14):
    d = s.diff()
    g = d.where(d > 0, 0).rolling(p).mean()
    l = (-d.where(d < 0, 0)).rolling(p).mean()
    return 100 - 100 / (1 + g / l)


def compute_atr(h, l, c, p=14):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()


def check_signal(sym):
    """Check V3 consensus for a stock. Returns dict with decision."""
    ticker = sym.upper() + '.NS'
    try:
        data = yf.download(ticker, period='1y', progress=False, auto_adjust=True)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        if len(data) < 60:
            return {'sym': sym, 'error': 'insufficient data'}

        c = data['Close']
        h = data['High']
        l = data['Low']
        o = data['Open']
        price = c.iloc[-1]

        # V3 Score: 5 indicators
        ema9 = compute_ema(c, 9).iloc[-1]
        ema21 = compute_ema(c, 21).iloc[-1]
        vwap = c.rolling(20).mean().iloc[-1]

        # SuperTrend
        hl2 = (h + l) / 2
        st_atr = (h - l).rolling(10).mean()
        st_upper = hl2 + 2 * st_atr
        st_lower = hl2 - 2 * st_atr
        st_dir = 0
        for i in range(1, len(c)):
            if c.iloc[i] > st_upper.iloc[i - 1]:
                st_dir = -1
            elif c.iloc[i] < st_lower.iloc[i - 1]:
                st_dir = 1

        # Ichimoku Cloud
        tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2
        kijun = (h.rolling(26).max() + l.rolling(26).min()) / 2
        senkou_a = (tenkan + kijun) / 2
        senkou_b = (h.rolling(52).max() + l.rolling(52).min()) / 2
        cloud_top = max(senkou_a.iloc[-1], senkou_b.iloc[-1])

        # Score
        checks = {
            'EMA9>EMA21': ema9 > ema21,
            'Close>VWAP': price > vwap,
            'SuperTrend': st_dir == -1,
            'Close>DailyEMA': price > ema9,
            'AboveCloud': price > cloud_top,
        }
        score = sum(1 for v in checks.values() if v)

        # RSI
        rsi = compute_rsi(c).iloc[-1]

        # ADX
        plus_dm = h.diff().where((h.diff() > -l.diff()) & (h.diff() > 0), 0)
        minus_dm = (-l.diff()).where((-l.diff() > h.diff()) & (-l.diff() > 0), 0)
        atr_s = compute_atr(h, l, c, 14)
        plus_di = 100 * plus_dm.rolling(14).mean() / atr_s
        minus_di = 100 * minus_dm.rolling(14).mean() / atr_s
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.rolling(14).mean().iloc[-1]

        # Decision
        reasons = []
        take = True

        if score < 4:
            take = False
            reasons.append(f'Score {score}/5 < 4')
        if rsi < 40 or rsi > 75:
            take = False
            reasons.append(f'RSI {rsi:.0f} outside 40-75')
        if 35 <= adx <= 50:
            take = False
            reasons.append(f'ADX {adx:.0f} in danger zone 35-50')

        return {
            'sym': sym,
            'price': round(price, 2),
            'score': score,
            'checks': checks,
            'rsi': round(rsi, 1),
            'adx': round(adx, 1),
            'decision': 'TAKE' if take else 'SKIP',
            'reasons': reasons,
        }
    except Exception as e:
        return {'sym': sym, 'error': str(e)}


def print_result(r):
    if 'error' in r:
        print(f"\n  {r['sym']}: ERROR - {r['error']}")
        return

    decision = r['decision']
    color_start = '\033[92m' if decision == 'TAKE' else '\033[91m'
    color_end = '\033[0m'

    print(f"\n{'='*50}")
    print(f"  {r['sym']} @ Rs {r['price']}")
    print(f"{'='*50}")
    print(f"  Score: {r['score']}/5")
    for name, val in r['checks'].items():
        mark = '+' if val else '-'
        print(f"    [{mark}] {name}")
    print(f"  RSI: {r['rsi']}")
    print(f"  ADX: {r['adx']}")
    print(f"  {'='*46}")
    print(f"  {color_start}>>> {decision} <<<{color_end}")
    if r['reasons']:
        for reason in r['reasons']:
            print(f"  Skip: {reason}")
    else:
        print(f"  All checks passed! Enter with 20% SL on premium.")
    print()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python univest_checker.py SBIN RELIANCE HDFCBANK")
        print("\nChecks V3 consensus (Score>=4, RSI 40-75, ADX not 35-50)")
        print("LOCKED STRATEGY: 85% WR, Rs 1,616/trade on 255 trades")
        sys.exit(0)

    stocks = [s.upper() for s in sys.argv[1:]]

    for sym in stocks:
        result = check_signal(sym)
        print_result(result)


def check_multyfi(sym, entry_price, sl_price):
    """Multyfi filter: SL<3% + Close>VWAP = 100% WR on 14 trades."""
    # Check SL distance
    sl_dist = abs(entry_price - sl_price) / entry_price * 100
    if sl_dist >= 3:
        return {'sym': sym, 'decision': 'SKIP', 'reason': f'SL {sl_dist:.1f}% >= 3% (wide SL = 21% WR)'}

    # Check VWAP
    result = check_signal(sym)
    if 'error' in result:
        return result

    # VWAP proxy = close > 20-day SMA (same as our V3 VWAP check)
    above_vwap = result['checks'].get('Close>VWAP', False)
    rsi = result.get('rsi', 50)

    if not above_vwap:
        return {'sym': sym, 'decision': 'SKIP', 'reason': f'Below VWAP (all losses were below VWAP)',
                'sl_dist': round(sl_dist, 1), 'rsi': rsi}

    return {'sym': sym, 'decision': 'TAKE', 'price': result.get('price', 0),
            'sl_dist': round(sl_dist, 1), 'rsi': rsi, 'above_vwap': True,
            'reason': 'SL<3% + Above VWAP = 100% WR filter passed'}
