"""Upstox API client — data feed, orders, token management."""
import requests
import logging
import time
import csv
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from . import config

log = logging.getLogger('upstox')


def get_token():
    """Get access token from env or file."""
    token = config.UPSTOX_ACCESS_TOKEN
    if not token and config.TOKEN_FILE.exists():
        token = config.TOKEN_FILE.read_text().strip()
    return token


def save_token(token):
    """Save token to file."""
    config.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.TOKEN_FILE.write_text(token)


def headers(token=None):
    """Auth headers."""
    t = token or get_token()
    if not t:
        log.error('NO TOKEN — all API calls will fail. Refresh token first!')
    return {'Authorization': f'Bearer {t}', 'Content-Type': 'application/json', 'Accept': 'application/json'}


def get_historical_candles(instrument_key, interval, from_date, to_date):
    """Fetch historical candle data (no auth needed for this endpoint)."""
    url = f'{config.UPSTOX_BASE}/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}'
    try:
        r = requests.get(url, headers={'Accept': 'application/json'}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            return data.get('data', {}).get('candles', [])
        log.error(f'Historical data error {r.status_code}: {r.text[:200]}')
    except Exception as e:
        log.error(f'Historical data error: {e}')
    return []


def get_intraday_candles(instrument_key, interval='1minute'):
    """Fetch today's intraday candle data (needs auth). Returns 5-min aggregated bars."""
    url = f'{config.UPSTOX_BASE}/historical-candle/intraday/{instrument_key}/{interval}'
    try:
        r = requests.get(url, headers=headers(), timeout=15)
        if r.status_code == 200:
            candles = r.json().get('data', {}).get('candles', [])
            if interval == '1minute' and candles:
                # Return raw 1-min candles — caller will aggregate if needed
                return candles
            return candles
        log.error(f'Intraday data error {r.status_code}: {r.text[:200]}')
    except Exception as e:
        log.error(f'Intraday data error: {e}')
    return []


def get_ltp(instrument_keys):
    """Get last traded price for multiple instruments."""
    keys = ','.join(instrument_keys)
    url = f'{config.UPSTOX_BASE}/market-quote/ltp?instrument_key={keys}'
    try:
        r = requests.get(url, headers=headers(), timeout=10)
        if r.status_code == 200:
            return r.json().get('data', {})
    except Exception as e:
        log.error(f'LTP error: {e}')
    return {}


def place_order(sym, qty, side, price, order_type='MARKET', product='I', trigger_price=0):
    """Place order via Upstox API.
    side: 'BUY' or 'SELL'
    order_type: 'MARKET' (default), 'LIMIT', 'SL' (stop loss)
    product: 'I' (intraday)
    trigger_price: required for SL orders
    """
    inst = config.INSTRUMENTS.get(sym)
    if not inst:
        log.error(f'Unknown instrument: {sym}')
        return None

    order = {
        'quantity': qty,
        'product': product,
        'validity': 'DAY',
        'instrument_token': inst,
        'order_type': order_type,
        'transaction_type': side,
        'disclosed_quantity': 0,
        'is_amo': False,
    }

    def tick_round(val, tick=0.05):
        """Round price to nearest tick size."""
        return round(round(val / tick) * tick, 2)

    def sl_round(val):
        """Round to 0.50 for SL orders — Upstox rejects 0.05 ticks on SL."""
        return round(round(val / 0.50) * 0.50, 2)

    if order_type == 'MARKET':
        order['price'] = 0
        order['trigger_price'] = 0
    elif order_type == 'LIMIT':
        order['price'] = tick_round(price)
        order['trigger_price'] = 0
    elif order_type == 'SL':
        tp = sl_round(trigger_price)
        if side == 'SELL':
            order['price'] = sl_round(tp * 0.995)
        else:
            order['price'] = sl_round(tp * 1.005)
        order['trigger_price'] = tp
    elif order_type == 'SL-M':
        order['price'] = 0
        order['trigger_price'] = sl_round(trigger_price)

    try:
        r = requests.post(f'{config.UPSTOX_BASE}/order/place',
                          headers=headers(), json=order, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get('status') == 'success':
                oid = data.get('data', {}).get('order_id')
                log.info(f'Order placed: {side} {qty} {sym} @ {price} type={order_type} -> {oid}')

                # For SL orders, verify it wasn't rejected by exchange
                if order_type in ('SL', 'SL-M') and oid:
                    import time as _t
                    _t.sleep(1)
                    try:
                        r2 = requests.get(f'{config.UPSTOX_BASE}/order/retrieve-all',
                                         headers=headers(), timeout=10)
                        if r2.status_code == 200:
                            for o in r2.json().get('data', []):
                                if o.get('order_id') == oid and o.get('status') == 'rejected':
                                    log.error(f'SL REJECTED by exchange: {o.get("status_message", "")[:200]}')
                                    return None
                    except Exception:
                        pass  # Verification failed but order may still be ok

                return oid
        log.error(f'Order failed: {r.text[:300]}')
    except Exception as e:
        log.error(f'Order error: {e}')
    return None


def modify_order(order_id, new_price=None, new_qty=None):
    """Modify existing order (used to update stop loss)."""
    body = {'order_id': order_id, 'validity': 'DAY'}
    if new_price: body['price'] = round(new_price, 2)
    if new_qty: body['quantity'] = new_qty
    try:
        r = requests.put(f'{config.UPSTOX_BASE}/order/modify',
                         headers=headers(), json=body, timeout=10)
        if r.status_code == 200:
            log.info(f'Order modified: {order_id} -> price={new_price}')
            return True
        log.error(f'Modify failed: {r.text[:200]}')
    except Exception as e:
        log.error(f'Modify error: {e}')
    return False


def cancel_order(order_id):
    """Cancel an order."""
    try:
        r = requests.delete(f'{config.UPSTOX_BASE}/order/cancel?order_id={order_id}',
                            headers=headers(), timeout=10)
        if r.status_code == 200:
            log.info(f'Order cancelled: {order_id}')
            return True
    except Exception as e:
        log.error(f'Cancel error: {e}')
    return False


def get_positions():
    """Get current positions."""
    try:
        r = requests.get(f'{config.UPSTOX_BASE}/portfolio/short-term-positions',
                         headers=headers(), timeout=10)
        if r.status_code == 200:
            return r.json().get('data', [])
    except Exception as e:
        log.error(f'Positions error: {e}')
    return []


def get_orders():
    """Get today's orders."""
    try:
        r = requests.get(f'{config.UPSTOX_BASE}/order/retrieve-all',
                         headers=headers(), timeout=10)
        if r.status_code == 200:
            return r.json().get('data', [])
    except Exception as e:
        log.error(f'Orders error: {e}')
    return []


def aggregate_1min_to_5min(candles_1min):
    """Aggregate 1-minute candles into 5-minute candles."""
    bars_5min = []
    # Sort chronologically
    sorted_candles = sorted(candles_1min, key=lambda x: x[0])

    for i in range(0, len(sorted_candles), 5):
        chunk = sorted_candles[i:i+5]
        if not chunk: break
        bars_5min.append({
            'timestamp': chunk[0][0][:19],
            'open': float(chunk[0][1]),
            'high': max(float(c[2]) for c in chunk),
            'low': min(float(c[3]) for c in chunk),
            'close': float(chunk[-1][4]),
            'volume': sum(int(c[5]) for c in chunk),
        })
    return bars_5min


def load_previous_days(sym, num_days=10):
    """Load last N days of 5-min candles for a stock from Upstox API."""
    inst = config.INSTRUMENTS.get(sym)
    if not inst: return []

    to_date = datetime.now().strftime('%Y-%m-%d')
    from_date = (datetime.now() - timedelta(days=num_days + 5)).strftime('%Y-%m-%d')

    # Use 30-minute bars for historical (no date range limit, no errors)
    # Daily OHLCV is computed from these — sufficient for trends, MAs, levels
    candles = get_historical_candles(inst, '30minute', from_date, to_date)
    if not candles: return []
    bars = []
    for c in sorted(candles, key=lambda x: x[0]):
        bars.append({
            'timestamp': c[0][:19],
            'open': float(c[1]),
            'high': float(c[2]),
            'low': float(c[3]),
            'close': float(c[4]),
            'volume': int(c[5]),
        })
    return bars
