"""
Trade execution script — called by Claude agent to place/manage orders.
Works in two modes:
  PAPER mode (default): simulates on local data
  LIVE mode: executes via Upstox API

Usage:
  python scripts/trade.py status                    — Account + positions
  python scripts/trade.py scan <date>               — Run signal scan
  python scripts/trade.py enter <symbol> <dir> <qty> <price> <stop> <target>
  python scripts/trade.py modify_stop <symbol> <new_stop>
  python scripts/trade.py modify_target <symbol> <new_target>
  python scripts/trade.py book_lot <symbol> <lot_id> — Book partial (L1-L5)
  python scripts/trade.py close <symbol>            — Close full position
  python scripts/trade.py close_all                 — Close everything (EOD)
  python scripts/trade.py journal <date>            — Write journal entry
"""
import sys, os, json, csv
from pathlib import Path
from datetime import datetime

MODE = os.environ.get('TRADE_MODE', 'PAPER')  # PAPER or LIVE
POSITIONS_FILE = Path('data/positions.json')
JOURNAL_DIR = Path('journal')
JOURNAL_DIR.mkdir(exist_ok=True)

# Upstox API (for LIVE mode)
UPSTOX_KEY = os.environ.get('UPSTOX_API_KEY', '')
UPSTOX_SECRET = os.environ.get('UPSTOX_API_SECRET', '')
UPSTOX_TOKEN = os.environ.get('UPSTOX_ACCESS_TOKEN', '')


def load_positions():
    if POSITIONS_FILE.exists():
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    return {'cash': 100000, 'positions': [], 'trades_today': [], 'total_pnl': 0}


def save_positions(state):
    with open(POSITIONS_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def cmd_status():
    state = load_positions()
    total_value = state['cash']
    print(f"Mode: {MODE}")
    print(f"Cash: Rs {state['cash']:,.0f}")
    print(f"Positions: {len(state['positions'])}")
    for p in state['positions']:
        print(f"  {p['direction']} {p['symbol']} | qty={p['qty']} entry={p['entry']:.2f} stop={p['stop']:.2f} target={p['target']:.2f}")
        print(f"    Lots: {json.dumps(p.get('lots', {}))}")
        total_value += p['qty'] * p['entry']  # Approximate
    print(f"Total value: Rs {total_value:,.0f}")
    print(f"Today's trades: {len(state['trades_today'])}")
    print(f"Total P&L: {state['total_pnl']:+.3f}%")


def cmd_scan(date):
    """Run proven signal scan for a date."""
    sys.path.insert(0, '.')
    from scripts.research import load_local_data, cmd_levels, cmd_breadth, cmd_daily

    DATA_DIR = Path('data/5min')
    all_syms = [f.stem.split('_')[0].upper() for f in sorted(DATA_DIR.glob('*.csv'))]
    scan_bar = 10

    signals = []
    for sym in all_syms:
        bars = load_local_data(sym)
        if not bars: continue
        day_bars = [b for b in bars if b['timestamp'][:10] == date]
        if len(day_bars) <= scan_bar: continue

        # ORB
        rh = day_bars[0]['high']; rl = day_bars[0]['low']; rs = rh - rl
        if rs > 0:
            for j in range(1, scan_bar+1):
                if day_bars[j]['close'] > rh and day_bars[j]['volume'] > day_bars[0]['volume']:
                    signals.append({'sym': sym, 'type': 'ORB', 'dir': 'LONG', 'entry': day_bars[j]['close'],
                                   'stop': rl, 'target': day_bars[j]['close'] + (day_bars[j]['close']-rl)*2.5})
                    break
                if day_bars[j]['close'] < rl and day_bars[j]['volume'] > day_bars[0]['volume']:
                    signals.append({'sym': sym, 'type': 'ORB', 'dir': 'SHORT', 'entry': day_bars[j]['close'],
                                   'stop': rh, 'target': day_bars[j]['close'] - (rh-day_bars[j]['close'])*2.5})
                    break

        # Camarilla R3
        levels = json.loads(cmd_levels(sym))
        if 'r3' in levels and 'r4' in levels:
            r3 = levels['r3']; r4 = levels['r4']; pivot = levels['pivot']
            for j in range(1, scan_bar+1):
                atr = sum(day_bars[k]['high']-day_bars[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                tol = atr * 0.3
                if abs(day_bars[j]['high']-r3) < tol and day_bars[j]['close'] < r3:
                    signals.append({'sym': sym, 'type': 'CAM_R3', 'dir': 'SHORT',
                                   'entry': day_bars[j]['close'], 'stop': r4, 'target': pivot})
                    break

    # Add daily context
    for s in signals:
        daily = json.loads(cmd_daily(s['sym']))
        s['daily_trend'] = daily.get('trend_5d', '?')
        s['rsi'] = daily.get('rsi', 50)
        s['near_52w_high'] = daily.get('near_52w_high', False)
        s['near_52w_low'] = daily.get('near_52w_low', False)
        s['risk_pct'] = round(abs(s['entry']-s['stop'])/s['entry']*100, 2)

    # Dedup by sym
    seen = set(); unique = []
    for s in signals:
        if s['sym'] not in seen: seen.add(s['sym']); unique.append(s)

    breadth = json.loads(cmd_breadth(date))

    print(json.dumps({
        'date': date,
        'breadth': breadth,
        'signals': unique,
        'count': len(unique),
    }, indent=2))


def cmd_enter(symbol, direction, qty, price, stop, target):
    state = load_positions()
    # Validate
    cost = qty * price
    if cost > state['cash'] * 0.8:
        print(json.dumps({"error": "Exceeds 80% cash limit", "cash": state['cash'], "cost": cost}))
        return
    if len(state['positions']) >= 3:
        print(json.dumps({"error": "Max 3 positions already open"}))
        return

    position = {
        'symbol': symbol, 'direction': direction, 'qty': int(qty),
        'entry': float(price), 'stop': float(stop), 'target': float(target),
        'lots': {'L1': 0.30, 'L2': 0.25, 'L3': 0.20, 'L4': 0.15, 'L5': 0.10},
        'booked_pnl': 0, 'entered_at': datetime.now().isoformat(),
    }
    state['positions'].append(position)
    state['cash'] -= cost
    save_positions(state)
    print(json.dumps({"status": "ENTERED", "position": position}))


def cmd_book_lot(symbol, lot_id, current_price=0):
    state = load_positions()
    pos = next((p for p in state['positions'] if p['symbol'] == symbol), None)
    if not pos:
        print(json.dumps({"error": f"No position for {symbol}"})); return

    lot_key = lot_id.upper()
    if lot_key not in pos['lots']:
        print(json.dumps({"error": f"Lot {lot_key} already booked or invalid"})); return

    pct = pos['lots'][lot_key]
    if current_price:
        if pos['direction'] == 'LONG':
            lot_pnl = (float(current_price) - pos['entry']) / pos['entry'] * 100
        else:
            lot_pnl = (pos['entry'] - float(current_price)) / pos['entry'] * 100
        pos['booked_pnl'] += lot_pnl * pct

    del pos['lots'][lot_key]
    state['cash'] += pos['qty'] * pct * (float(current_price) if current_price else pos['entry'])
    save_positions(state)
    print(json.dumps({"status": "LOT_BOOKED", "lot": lot_key, "remaining_lots": list(pos['lots'].keys())}))


def cmd_modify_stop(symbol, new_stop):
    state = load_positions()
    pos = next((p for p in state['positions'] if p['symbol'] == symbol), None)
    if not pos:
        print(json.dumps({"error": f"No position for {symbol}"})); return
    old = pos['stop']
    pos['stop'] = float(new_stop)
    save_positions(state)
    print(json.dumps({"status": "STOP_MODIFIED", "old": old, "new": pos['stop']}))


def cmd_modify_target(symbol, new_target):
    state = load_positions()
    pos = next((p for p in state['positions'] if p['symbol'] == symbol), None)
    if not pos:
        print(json.dumps({"error": f"No position for {symbol}"})); return
    old = pos['target']
    pos['target'] = float(new_target)
    save_positions(state)
    print(json.dumps({"status": "TARGET_MODIFIED", "old": old, "new": pos['target']}))


def cmd_close(symbol, exit_price=0):
    state = load_positions()
    pos = next((p for p in state['positions'] if p['symbol'] == symbol), None)
    if not pos:
        print(json.dumps({"error": f"No position for {symbol}"})); return

    remaining = sum(pos['lots'].values())
    if exit_price and remaining > 0:
        if pos['direction'] == 'LONG':
            final_pnl = (float(exit_price) - pos['entry']) / pos['entry'] * 100
        else:
            final_pnl = (pos['entry'] - float(exit_price)) / pos['entry'] * 100
        pos['booked_pnl'] += final_pnl * remaining

    total_pnl = pos['booked_pnl']
    state['cash'] += pos['qty'] * (float(exit_price) if exit_price else pos['entry'])
    state['positions'] = [p for p in state['positions'] if p['symbol'] != symbol]
    state['trades_today'].append({
        'symbol': symbol, 'direction': pos['direction'],
        'entry': pos['entry'], 'exit': float(exit_price) if exit_price else 0,
        'pnl': round(total_pnl, 3), 'closed_at': datetime.now().isoformat(),
    })
    state['total_pnl'] += total_pnl
    save_positions(state)
    print(json.dumps({"status": "CLOSED", "symbol": symbol, "pnl": round(total_pnl, 3)}))


def cmd_close_all():
    state = load_positions()
    for pos in list(state['positions']):
        cmd_close(pos['symbol'])


def cmd_journal(date):
    state = load_positions()
    trades = state.get('trades_today', [])
    journal_file = JOURNAL_DIR / f"{date}.md"

    content = f"""# Trade Journal - {date}

## Account
- Cash: Rs {state['cash']:,.0f}
- Open positions: {len(state['positions'])}
- Total P&L: {state['total_pnl']:+.3f}%

## Trades Today
"""
    if trades:
        content += "| Symbol | Direction | Entry | Exit | P&L |\n|--------|-----------|-------|------|-----|\n"
        for t in trades:
            content += f"| {t['symbol']} | {t['direction']} | {t['entry']:.2f} | {t.get('exit',0):.2f} | {t['pnl']:+.3f}% |\n"
    else:
        content += "No trades taken.\n"

    content += "\n## Lessons\n(To be filled by agent)\n"

    with open(journal_file, 'w') as f:
        f.write(content)
    print(f"Journal written to {journal_file}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)

    cmd = sys.argv[1]
    if cmd == 'status': cmd_status()
    elif cmd == 'scan': cmd_scan(sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime('%Y-%m-%d'))
    elif cmd == 'enter': cmd_enter(sys.argv[2], sys.argv[3], int(sys.argv[4]), float(sys.argv[5]), float(sys.argv[6]), float(sys.argv[7]))
    elif cmd == 'book_lot': cmd_book_lot(sys.argv[2], sys.argv[3], float(sys.argv[4]) if len(sys.argv)>4 else 0)
    elif cmd == 'modify_stop': cmd_modify_stop(sys.argv[2], float(sys.argv[3]))
    elif cmd == 'modify_target': cmd_modify_target(sys.argv[2], float(sys.argv[3]))
    elif cmd == 'close': cmd_close(sys.argv[2], float(sys.argv[3]) if len(sys.argv)>3 else 0)
    elif cmd == 'close_all': cmd_close_all()
    elif cmd == 'journal': cmd_journal(sys.argv[2] if len(sys.argv)>2 else datetime.now().strftime('%Y-%m-%d'))
    else: print(f"Unknown: {cmd}"); print(__doc__)
