"""
Research script — called by Claude agent to get market data.
Usage: python scripts/research.py <command> [args]

Commands:
  bars <SYMBOL> [limit]     — Get recent 5-min bars
  all_bars                  — Get bar 0-10 for all 45 stocks (morning scan)
  account                   — Get account status
  positions                 — Get open positions
  levels <SYMBOL>           — Get Camarilla/pivot levels
  breadth                   — Market breadth (% up/down)
  daily <SYMBOL>            — Daily trend, RSI, 52w context
  news <SYMBOL>             — Recent news (Upstox API)
"""
import sys, os, json, csv, requests
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

# Upstox API config
UPSTOX_KEY = os.environ.get('UPSTOX_API_KEY', '')
UPSTOX_SECRET = os.environ.get('UPSTOX_API_SECRET', '')
UPSTOX_TOKEN = os.environ.get('UPSTOX_ACCESS_TOKEN', '')
BASE_URL = "https://api.upstox.com/v2"

# For backtesting, use local CSV data
DATA_DIR = Path('data/5min')


def load_local_data(symbol):
    """Load from local CSV for backtesting."""
    f = DATA_DIR / f"{symbol}_5min.csv"
    if not f.exists(): return []
    with open(f) as fh:
        return [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
                 'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))}
                for r in csv.DictReader(fh)]


def cmd_bars(symbol, limit=20):
    """Get recent bars for a symbol."""
    bars = load_local_data(symbol)
    if not bars: return json.dumps({"error": f"No data for {symbol}"})
    recent = bars[-limit:]
    lines = []
    for b in recent:
        ts = b['timestamp'].split(' ')[1][:5] if ' ' in b['timestamp'] else ''
        c = 'G' if b['close'] > b['open'] else 'R'
        body = abs(b['close']-b['open'])/(b['high']-b['low'])*100 if b['high']!=b['low'] else 0
        lines.append(f"{b['timestamp'][:10]} {ts} {c} O={b['open']:.1f} H={b['high']:.1f} L={b['low']:.1f} C={b['close']:.1f} V={b['volume']:,} body={body:.0f}%")
    return "\n".join(lines)


def cmd_levels(symbol):
    """Get Camarilla and pivot levels."""
    bars = load_local_data(symbol)
    if not bars: return json.dumps({"error": f"No data for {symbol}"})
    # Get prev day
    dates = sorted(set(b['timestamp'][:10] for b in bars))
    if len(dates) < 2: return "Not enough data"
    prev_date = dates[-2]
    prev_bars = [b for b in bars if b['timestamp'][:10] == prev_date]
    if not prev_bars: return "No prev day"
    h = max(b['high'] for b in prev_bars)
    l = min(b['low'] for b in prev_bars)
    c = prev_bars[-1]['close']
    rng = h - l
    if rng == 0: return "Zero range"
    r3 = c + rng*1.1/4; r4 = c + rng*1.1/2
    s3 = c - rng*1.1/4; s4 = c - rng*1.1/2
    pivot = (h+l+c)/3
    return json.dumps({
        "symbol": symbol, "prev_high": h, "prev_low": l, "prev_close": c,
        "pivot": round(pivot,2), "s4": round(s4,2), "s3": round(s3,2),
        "r3": round(r3,2), "r4": round(r4,2),
    }, indent=2)


def cmd_breadth(date=None):
    """Market breadth — how many stocks up/down."""
    all_stocks = {}
    for f in sorted(DATA_DIR.glob('*.csv')):
        sym = f.stem.split('_')[0].upper()
        bars = load_local_data(sym)
        if not bars: continue
        dates = sorted(set(b['timestamp'][:10] for b in bars))
        if date and date not in dates: continue
        d = date or dates[-1]
        day_bars = [b for b in bars if b['timestamp'][:10] == d]
        if len(day_bars) > 6:
            move = (day_bars[6]['close'] - day_bars[0]['open']) / day_bars[0]['open'] * 100
            all_stocks[sym] = round(move, 2)

    up = sum(1 for v in all_stocks.values() if v > 0.15)
    dn = sum(1 for v in all_stocks.values() if v < -0.15)
    tot = len(all_stocks)
    regime = 'TRENDING_UP' if up/tot>0.6 else 'TRENDING_DOWN' if dn/tot>0.6 else 'CHOPPY'
    return json.dumps({"up": up, "down": dn, "total": tot, "regime": regime,
                       "top_movers": dict(sorted(all_stocks.items(), key=lambda x: -abs(x[1]))[:10])}, indent=2)


def cmd_daily(symbol):
    """Daily trend context."""
    bars = load_local_data(symbol)
    if not bars: return json.dumps({"error": f"No data for {symbol}"})
    dates = sorted(set(b['timestamp'][:10] for b in bars))
    daily_closes = []
    for d in dates:
        db = [b for b in bars if b['timestamp'][:10] == d]
        if db: daily_closes.append(db[-1]['close'])

    if len(daily_closes) < 5: return "Not enough daily data"

    c5 = daily_closes[-5:]
    up = sum(1 for j in range(1,len(c5)) if c5[j]>c5[j-1])
    trend = 'UP' if up >= 4 else 'DOWN' if up <= 1 else 'SIDE'

    lookback = min(250, len(daily_closes))
    h52 = max(daily_closes[-lookback:]); l52 = min(daily_closes[-lookback:])
    sma20 = sum(daily_closes[-min(20,len(daily_closes)):]) / min(20,len(daily_closes))

    rsi = 50
    if len(daily_closes) >= 15:
        g = [max(0,daily_closes[j]-daily_closes[j-1]) for j in range(-14,0)]
        lo = [max(0,daily_closes[j-1]-daily_closes[j]) for j in range(-14,0)]
        ag=sum(g)/14; al=sum(lo)/14
        rsi = 100-100/(1+ag/al) if al>0 else 50

    return json.dumps({
        "symbol": symbol, "trend_5d": trend,
        "rsi": round(rsi), "above_sma20": daily_closes[-1] > sma20,
        "price": daily_closes[-1],
        "from_52w_high": round((daily_closes[-1]-h52)/h52*100, 1),
        "from_52w_low": round((daily_closes[-1]-l52)/l52*100, 1),
        "near_52w_high": (daily_closes[-1]-h52)/h52*100 > -3,
        "near_52w_low": (daily_closes[-1]-l52)/l52*100 < 3,
        "5d_change": round((daily_closes[-1]/daily_closes[-5]-1)*100, 1),
    }, indent=2)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)

    cmd = sys.argv[1]
    if cmd == 'bars':
        sym = sys.argv[2] if len(sys.argv) > 2 else 'SBIN'
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 20
        print(cmd_bars(sym, limit))
    elif cmd == 'levels':
        print(cmd_levels(sys.argv[2] if len(sys.argv) > 2 else 'SBIN'))
    elif cmd == 'breadth':
        print(cmd_breadth(sys.argv[2] if len(sys.argv) > 2 else None))
    elif cmd == 'daily':
        print(cmd_daily(sys.argv[2] if len(sys.argv) > 2 else 'SBIN'))
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
