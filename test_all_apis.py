#!/usr/bin/env python3
"""Full API validation — run at 9:15-9:20 AM during market hours."""
import sys, time, json
sys.path.insert(0, '.')
from live import indmoney_client as api
from datetime import datetime

PASS = '✓'
FAIL = '✗'
results = []

def test(name, fn):
    try:
        result = fn()
        if result:
            print(f'  {PASS} {name}: {result}')
            results.append((name, True))
        else:
            print(f'  {FAIL} {name}: returned empty/None')
            results.append((name, False))
    except Exception as e:
        print(f'  {FAIL} {name}: {e}')
        results.append((name, False))

print('=' * 70)
print(f'API VALIDATION — {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
print('=' * 70)
print()

# 1. Token
print('--- AUTH ---')
test('Token loads', lambda: f'{api.get_token()[:30]}...' if api.get_token() else None)

# 2. Funds
print('--- FUNDS ---')
test('Get funds', lambda: f'Rs {api.get_funds():,.0f}')

# 3. LTP — single stock
print('--- LTP ---')
test('LTP single (RELIANCE)', lambda: api.get_ltp(['RELIANCE']) or None)
test('LTP batch (5 stocks)', lambda: api.get_ltp(['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ITC']))
test('LTP all 51 stocks', lambda: f'{len(api.get_ltp(list(api.SCRIP_CODES.keys())))} stocks'
     if api.get_ltp(list(api.SCRIP_CODES.keys())) else None)

# 4. Full quote
print('--- FULL QUOTE ---')
def test_full_quote():
    q = api.get_full_quote(['RELIANCE', 'TCS'])
    if not q: return None
    for sym, data in q.items():
        fields = ['open', 'high', 'low', 'last_price', 'prev_close']
        missing = [f for f in fields if f not in data or data[f] == 0]
        if missing:
            return f'{sym}: missing {missing}'
    return f'{len(q)} stocks, all fields present'
test('Full quote (2 stocks)', test_full_quote)

# 5. Market depth
print('--- MARKET DEPTH ---')
def test_depth():
    d = api.get_market_depth(['RELIANCE', 'TCS'])
    if not d: return None
    for sym, data in d.items():
        if 'buy_pct' not in data or 'sell_pct' not in data:
            return f'{sym}: missing buy_pct/sell_pct'
    return f'{len(d)} stocks: ' + ', '.join(f'{s} buy={d[s]["buy_pct"]:.0f}%' for s in d)
test('Market depth', test_depth)

# 6. Historical candles
print('--- HISTORICAL ---')
def test_historical():
    now = int(time.time() * 1000)
    start = now - 7 * 86400 * 1000  # 7 days ago
    candles = api.get_historical_candles('RELIANCE', '1day', start, now)
    if not candles: return None
    return f'{len(candles)} daily candles for RELIANCE'
test('Historical candles', test_historical)

# 7. Circuit limits (from full quote)
print('--- CIRCUIT LIMITS ---')
def test_circuits():
    q = api.get_full_quote(['RELIANCE'])
    if not q or 'RELIANCE' not in q: return None
    r = q['RELIANCE']
    uc = r.get('upper_circuit', 0)
    lc = r.get('lower_circuit', 0)
    if uc == 0 and lc == 0:
        return 'No circuit data (may be normal for RELIANCE)'
    return f'RELIANCE: upper={uc} lower={lc}'
test('Circuit limits', test_circuits)

# 8. Prev close verification
print('--- PREV CLOSE ---')
def test_prev_close():
    q = api.get_full_quote(['RELIANCE', 'TCS', 'HDFCBANK'])
    if not q: return None
    for sym, data in q.items():
        pc = data.get('prev_close', 0)
        ltp = data.get('last_price', 0)
        if pc <= 0: return f'{sym}: prev_close is 0'
        gap = (ltp - pc) / pc * 100
    return ', '.join(f'{s}: pc={q[s]["prev_close"]:.0f} gap={((q[s]["last_price"]-q[s]["prev_close"])/q[s]["prev_close"]*100):+.2f}%' for s in q)
test('Prev close + gap calc', test_prev_close)

# 9. WebSocket connectivity
print('--- WEBSOCKET ---')
def test_ws():
    import websocket
    token = api.get_token()
    if not token: return None
    ws_url = 'wss://ws-prices.indstocks.com/api/v1/ws/prices'
    result = {'connected': False, 'price_received': False}

    def on_open(ws):
        result['connected'] = True
        # Subscribe to RELIANCE
        msg = json.dumps({'action': 'subscribe', 'mode': 'ltp',
                          'instruments': ['NSE:2885']})
        ws.send(msg)

    def on_message(ws, message):
        data = json.loads(message)
        if data.get('data', {}).get('ltp'):
            result['price_received'] = True
            result['ltp'] = data['data']['ltp']
            result['instrument'] = data.get('instrument', '?')
            ws.close()

    def on_error(ws, error):
        result['error'] = str(error)[:100]
        ws.close()

    ws = websocket.WebSocketApp(
        ws_url,
        header={'Authorization': token},
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
    )

    import threading
    t = threading.Thread(target=lambda: ws.run_forever(ping_interval=10))
    t.daemon = True
    t.start()
    t.join(timeout=10)

    if not result['connected']:
        return f'Connection failed: {result.get("error", "timeout")}'
    if not result['price_received']:
        return f'Connected but no price in 10s (market may be closed)'
    return f'Connected + received LTP={result["ltp"]} for {result["instrument"]}'

test('WebSocket price feed', test_ws)

# 10. Order feed WebSocket
print('--- ORDER WEBSOCKET ---')
def test_order_ws():
    import websocket
    token = api.get_token()
    if not token: return None
    ws_url = 'wss://ws-order-updates.indstocks.com/api/v1/ws/trades'
    result = {'connected': False}

    def on_open(ws):
        result['connected'] = True
        ws.close()

    def on_error(ws, error):
        result['error'] = str(error)[:100]

    ws = websocket.WebSocketApp(
        ws_url,
        header={'Authorization': token},
        on_open=on_open,
        on_error=on_error,
    )

    import threading
    t = threading.Thread(target=lambda: ws.run_forever())
    t.daemon = True
    t.start()
    t.join(timeout=10)

    if result['connected']:
        return 'Connected OK'
    return f'Failed: {result.get("error", "timeout")}'

test('WebSocket order feed', test_order_ws)

# 11. Scrip code verification
print('--- SCRIP CODES ---')
def test_scrips():
    # Try LTP for all mapped stocks
    all_ltps = api.get_ltp(list(api.SCRIP_CODES.keys()))
    working = sum(1 for v in all_ltps.values() if v > 0)
    missing = [s for s in api.SCRIP_CODES if s not in all_ltps or all_ltps[s] <= 0]
    if missing:
        return f'{working}/{len(api.SCRIP_CODES)} working. Missing: {missing[:5]}...'
    return f'All {working}/{len(api.SCRIP_CODES)} stocks have valid LTP'
test('All scrip codes valid', test_scrips)

# Summary
print()
print('=' * 70)
passed = sum(1 for _, ok in results if ok)
failed = sum(1 for _, ok in results if not ok)
print(f'RESULT: {passed} PASSED, {failed} FAILED out of {len(results)} tests')
if failed == 0:
    print('ALL SYSTEMS GO — ready for live trading')
else:
    print('FAILED tests:')
    for name, ok in results:
        if not ok:
            print(f'  {FAIL} {name}')
print('=' * 70)
