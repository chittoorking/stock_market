"""INDmoney (INDstocks) API client — replaces Upstox."""
import requests
import logging
import time
import csv
from pathlib import Path
from . import config

log = logging.getLogger('indmoney')

BASE = 'https://api.indstocks.com'
TOKEN_FILE = Path(__file__).parent.parent / 'data' / 'indmoney_token.txt'

# Scrip code mapping: NIFTY 100 stocks (90 verified + estimated)
# Format: NSE_{security_id} — matches NSE official security IDs
# VERIFIED (50): NIFTY 50 codes confirmed via live LTP on 2026-06-16
# ESTIMATED (40): NIFTY Next 50 codes from NSE bhavcopy — run verify_scrips.py to confirm
# TRENT: not available on INDstocks (NSE_3584 returns empty)
SCRIP_CODES = {
    # --- NIFTY 50 (verified) ---
    'ADANIENT': 'NSE_25', 'ADANIPORTS': 'NSE_15083',
    'APOLLOHOSP': 'NSE_18365', 'ASIANPAINT': 'NSE_236',
    'AXISBANK': 'NSE_5900', 'BAJAJ-AUTO': 'NSE_16669',
    'BAJFINANCE': 'NSE_317', 'BAJAJFINSV': 'NSE_16675',
    'BPCL': 'NSE_526', 'BHARTIARTL': 'NSE_10604',
    'BRITANNIA': 'NSE_547', 'CIPLA': 'NSE_694',
    'COALINDIA': 'NSE_20374', 'DIVISLAB': 'NSE_10940',
    'DRREDDY': 'NSE_881', 'EICHERMOT': 'NSE_910',
    'GRASIM': 'NSE_1232', 'HCLTECH': 'NSE_7229',
    'HDFCBANK': 'NSE_1333', 'HDFCLIFE': 'NSE_467',
    'HEROMOTOCO': 'NSE_1348', 'HINDALCO': 'NSE_1363',
    'HINDUNILVR': 'NSE_1394', 'ICICIBANK': 'NSE_4963',
    'ITC': 'NSE_1660', 'INDUSINDBK': 'NSE_5258',
    'INFY': 'NSE_1594', 'JSWSTEEL': 'NSE_11723',
    'KOTAKBANK': 'NSE_1922', 'LT': 'NSE_11483',
    'M&M': 'NSE_2031', 'MARUTI': 'NSE_10999',
    'NTPC': 'NSE_11630', 'NESTLEIND': 'NSE_17963',
    'ONGC': 'NSE_2475', 'POWERGRID': 'NSE_14977',
    'RELIANCE': 'NSE_2885', 'SBILIFE': 'NSE_21808',
    'SBIN': 'NSE_3045', 'SHRIRAMFIN': 'NSE_3103',
    'SUNPHARMA': 'NSE_3351', 'TCS': 'NSE_11536',
    'TATACONSUM': 'NSE_3432', 'TATAMOTORS': 'NSE_3456',
    'TATASTEEL': 'NSE_3499', 'TECHM': 'NSE_13538',
    'TITAN': 'NSE_3506',
    'UPL': 'NSE_11287', 'ULTRACEMCO': 'NSE_11532',
    'WIPRO': 'NSE_3787',
    # --- NIFTY Next 50 (estimated — verify with verify_scrips.py) ---
    'ABB': 'NSE_20070', 'ADANIENSOL': 'NSE_25850',
    'ADANIGREEN': 'NSE_21871', 'ADANIPOWER': 'NSE_18388',
    'AMBUJACEM': 'NSE_1270', 'BAJAJHLDNG': 'NSE_1102',
    'BANKBARODA': 'NSE_1452', 'BOSCHLTD': 'NSE_1080',
    'CANBK': 'NSE_10180', 'CGPOWER': 'NSE_14875',
    'CHOLAFIN': 'NSE_2972', 'CUMMINSIND': 'NSE_1700',
    'DLF': 'NSE_14366', 'DMART': 'NSE_20640',
    'GAIL': 'NSE_4717', 'GODREJCP': 'NSE_10099',
    'HAL': 'NSE_14307', 'HDFCAMC': 'NSE_22122',
    'HINDZINC': 'NSE_1279', 'INDHOTEL': 'NSE_1550',
    'IOC': 'NSE_1624', 'IRFC': 'NSE_24143',
    'JINDALSTEL': 'NSE_11243', 'LODHA': 'NSE_24954',
    'LTM': 'NSE_17818', 'MOTHERSON': 'NSE_3405',
    'MUTHOOTFIN': 'NSE_17622', 'PFC': 'NSE_14299',
    'PIDILITIND': 'NSE_2664', 'PNB': 'NSE_2730',
    'RECLTD': 'NSE_14383', 'SHREECEM': 'NSE_3410',
    'SIEMENS': 'NSE_3150', 'SOLARINDS': 'NSE_16213',
    'TATAPOWER': 'NSE_3426', 'TORNTPHARM': 'NSE_3839',
    'TVSMOTOR': 'NSE_3937', 'UNIONBANK': 'NSE_10355',
    'UNITDSPR': 'NSE_7269', 'VBL': 'NSE_16713',
}

# Reverse map
SYM_FROM_SCRIP = {v: k for k, v in SCRIP_CODES.items()}


def get_token():
    """Get access token from file."""
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    return None


def save_token(token):
    """Save token to file."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)


def headers():
    """Auth headers."""
    t = get_token()
    if not t:
        log.error('NO INDMONEY TOKEN — all API calls will fail!')
    return {'Authorization': t, 'Content-Type': 'application/json', 'Accept': 'application/json'}


def get_ltp(syms):
    """Get LTP for multiple stocks. Returns {sym: last_price}."""
    scrips = [SCRIP_CODES[s] for s in syms if s in SCRIP_CODES]
    if not scrips:
        return {}
    result = {}
    # INDmoney supports up to 1000 per call
    keys = ','.join(scrips)
    try:
        r = requests.get(f'{BASE}/market/quotes/ltp?scrip-codes={keys}',
                        headers=headers(), timeout=15)
        if r.status_code == 200:
            data = r.json().get('data', {})
            for scrip, val in data.items():
                sym = SYM_FROM_SCRIP.get(scrip)
                if sym:
                    result[sym] = val.get('live_price', 0)
    except Exception as e:
        log.error(f'LTP error: {e}')
    return result


def get_full_quote(syms):
    """Get full quotes including market depth. Returns {sym: {live_price, volume, prev_close, ...}}."""
    scrips = [SCRIP_CODES[s] for s in syms if s in SCRIP_CODES]
    if not scrips:
        return {}
    result = {}
    # Batch in groups of 15 to avoid URL length limits
    batch_size = 15
    for batch_start in range(0, len(scrips), batch_size):
        batch = scrips[batch_start:batch_start + batch_size]
        keys = ','.join(batch)
        try:
            r = requests.get(f'{BASE}/market/quotes/full?scrip-codes={keys}',
                            headers=headers(), timeout=15)
            if r.status_code == 200:
                data = r.json().get('data', {})
                for scrip, val in data.items():
                    sym = SYM_FROM_SCRIP.get(scrip)
                    if sym:
                        result[sym] = {
                            'last_price': val.get('live_price', 0),
                            'open': val.get('day_open', 0),
                            'high': val.get('day_high', 0),
                            'low': val.get('day_low', 0),
                            'prev_close': val.get('prev_close', 0),
                            'volume': val.get('volume', 0),
                            'upper_circuit': val.get('upper_circuit', 0),
                            'lower_circuit': val.get('lower_circuit', 0),
                            'market_depth': val.get('market_depth', {}),
                        }
        except Exception as e:
            log.error(f'Full quote batch error: {e}')
        time.sleep(0.2)  # rate limit between batches
    return result


def get_market_depth(syms):
    """Get market depth (order book). Returns {sym: {total_buy, total_sell, buy_pct, sell_pct, spread_pct}}."""
    scrips = [SCRIP_CODES[s] for s in syms if s in SCRIP_CODES]
    if not scrips:
        return {}
    result = {}
    # Batch in groups of 15
    batch_size = 15
    for batch_start in range(0, len(scrips), batch_size):
        batch = scrips[batch_start:batch_start + batch_size]
        keys = ','.join(batch)
        try:
            r = requests.get(f'{BASE}/market/quotes/mkt?scrip-codes={keys}',
                            headers=headers(), timeout=15)
            if r.status_code == 200:
                data = r.json().get('data', {})
                for scrip, val in data.items():
                    sym = SYM_FROM_SCRIP.get(scrip)
                    if not sym:
                        continue
                    # Try multiple paths to find aggregate depth
                    # API nests as: data.{scrip}.market_depth.{scrip}.aggregate
                    md = None
                    depth_root = None
                    if isinstance(val, dict):
                        mkt_depth = val.get('market_depth', {})
                        # Path 1: market_depth.{scrip}.aggregate (double-nested)
                        if isinstance(mkt_depth, dict):
                            for k, v in mkt_depth.items():
                                if isinstance(v, dict) and 'aggregate' in v:
                                    md = v.get('aggregate', {})
                                    depth_root = v
                                    break
                        # Path 2: market_depth.aggregate (single-nested)
                        if not md or md.get('buy_percentage') is None:
                            md = mkt_depth.get('aggregate', {})
                            depth_root = mkt_depth
                        # Path 3: direct aggregate in val
                        if not md or md.get('buy_percentage') is None:
                            md = val.get('aggregate', {})
                            depth_root = val

                    if not md:
                        md = {}

                    # Parse percentages (may be float or string)
                    bp = md.get('buy_percentage', 50)
                    sp = md.get('sell_percentage', 50)
                    if isinstance(bp, str):
                        try: bp = float(bp.replace(',', ''))
                        except: bp = 50
                    if isinstance(sp, str):
                        try: sp = float(sp.replace(',', ''))
                        except: sp = 50

                    # Parse totals (may have commas)
                    tb = md.get('total_buy', 0)
                    ts = md.get('total_sell', 0)
                    if isinstance(tb, str):
                        try: tb = int(tb.replace(',', ''))
                        except: tb = 0
                    if isinstance(ts, str):
                        try: ts = int(ts.replace(',', ''))
                        except: ts = 0

                    # Extract bid-ask spread from depth levels
                    spread_pct = 0
                    depth_levels = []
                    if depth_root and isinstance(depth_root, dict):
                        depth_levels = depth_root.get('depth', [])
                    if depth_levels and len(depth_levels) > 0:
                        level0 = depth_levels[0]
                        bid_p = level0.get('buy', {}).get('price', 0)
                        ask_p = level0.get('sell', {}).get('price', 0)
                        if isinstance(bid_p, str):
                            try: bid_p = float(bid_p.replace(',', ''))
                            except: bid_p = 0
                        if isinstance(ask_p, str):
                            try: ask_p = float(ask_p.replace(',', ''))
                            except: ask_p = 0
                        if bid_p > 0 and ask_p > 0:
                            spread_pct = (ask_p - bid_p) / bid_p * 100

                    result[sym] = {
                        'total_buy': tb,
                        'total_sell': ts,
                        'buy_pct': bp,
                        'sell_pct': sp,
                        'spread_pct': spread_pct,
                    }
        except Exception as e:
            log.error(f'Market depth error: {e}')
        time.sleep(0.2)
    return result


def get_historical_candles(sym, interval, start_ts, end_ts):
    """Fetch historical candle data. interval: 1minute, 5minute, 1day etc.
    start_ts, end_ts: unix epoch milliseconds."""
    scrip = SCRIP_CODES.get(sym)
    if not scrip:
        return []
    try:
        r = requests.get(
            f'{BASE}/market/historical/{interval}?scrip-codes={scrip}&start_time={start_ts}&end_time={end_ts}',
            headers=headers(), timeout=15)
        if r.status_code == 200:
            return r.json().get('data', {}).get(scrip, {}).get('candles', [])
    except Exception as e:
        log.error(f'Historical data error: {e}')
    return []


def place_order(sym, qty, side, price, order_type='MARKET', product='INTRADAY', trigger_price=0):
    """Place order via INDstocks API.
    side: 'BUY' or 'SELL'
    order_type: 'MARKET', 'LIMIT'
    product: 'INTRADAY', 'CNC', 'MARGIN'
    Note: INDstocks converts MARKET to LIMIT at live price.
    """
    scrip = SCRIP_CODES.get(sym)
    if not scrip:
        log.error(f'Unknown instrument: {sym}')
        return None

    # Extract security_id (number after NSE_)
    security_id = scrip.split('_')[1]

    order = {
        'txn_type': side,
        'exchange': 'NSE',
        'segment': 'EQUITY',
        'product': product,
        'order_type': order_type,
        'validity': 'DAY',
        'security_id': security_id,
        'qty': qty,
        'algo_id': '99999',
        'is_amo': False,
    }

    if order_type == 'LIMIT':
        order['limit_price'] = round(price, 2)

    # For stop loss: use smart order
    if trigger_price > 0:
        return place_smart_order(sym, qty, side, price, trigger_price)

    try:
        r = requests.post(f'{BASE}/order', headers=headers(), json=order, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get('status') == 'success':
                oid = data.get('data', {}).get('order_id') or data.get('data', {}).get('id')
                log.info(f'Order placed: {side} {qty} {sym} @ {price} type={order_type} -> {oid}')

                # Verify order wasn't rejected
                time.sleep(1)
                try:
                    r2 = requests.get(f'{BASE}/order-book?segment=EQUITY',
                                     headers=headers(), timeout=10)
                    if r2.status_code == 200:
                        for o in r2.json().get('data', []):
                            if str(o.get('order_id')) == str(oid) and o.get('status') == 'rejected':
                                log.error(f'ORDER REJECTED: {o.get("message", "")[:200]}')
                                return None
                except Exception:
                    pass

                return oid
        log.error(f'Order failed: {r.text[:300]}')
    except Exception as e:
        log.error(f'Order error: {e}')
    return None


def place_smart_order(sym, qty, side, limit_price, trigger_price,
                      sl_trigger=None, sl_limit=None, tgt_trigger=None, tgt_limit=None):
    """Place smart order with optional SL + target legs (OCO-like)."""
    scrip = SCRIP_CODES.get(sym)
    if not scrip:
        return None

    security_id = scrip.split('_')[1]

    order = {
        'txn_type': side,
        'exchange': 'NSE',
        'segment': 'EQUITY',
        'product': 'INTRADAY',
        'order_type': 'LIMIT',
        'security_id': security_id,
        'qty': qty,
        'limit_price': round(limit_price, 2),
        'algo_id': '99999',
    }

    # Add SL leg
    if sl_trigger and sl_limit:
        order['sl_trigger_price'] = round(sl_trigger, 2)
        order['sl_limit_price'] = round(sl_limit, 2)

    # Add target leg
    if tgt_trigger and tgt_limit:
        order['tgt_trigger_price'] = round(tgt_trigger, 2)
        order['tgt_limit_price'] = round(tgt_limit, 2)

    try:
        r = requests.post(f'{BASE}/smart/order', headers=headers(), json=order, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get('status') == 'success':
                oid = data.get('data', {}).get('order_id') or data.get('data', {}).get('id')
                log.info(f'Smart order: {side} {qty} {sym} SL={sl_trigger} TGT={tgt_trigger} -> {oid}')
                return oid
        log.error(f'Smart order failed: {r.text[:300]}')
    except Exception as e:
        log.error(f'Smart order error: {e}')
    return None


def cancel_order(order_id, segment='EQUITY'):
    """Cancel an order."""
    try:
        r = requests.post(f'{BASE}/order/cancel',
                         headers=headers(),
                         json={'order_id': str(order_id), 'segment': segment},
                         timeout=10)
        if r.status_code == 200:
            log.info(f'Order cancelled: {order_id}')
            return True
        log.error(f'Cancel failed: {r.text[:200]}')
    except Exception as e:
        log.error(f'Cancel error: {e}')
    return False


def get_positions(product='intraday'):
    """Get open positions. product: intraday, cnc."""
    try:
        r = requests.get(f'{BASE}/portfolio/positions?segment=equity&product={product}',
                        headers=headers(), timeout=10)
        if r.status_code == 200:
            data = r.json().get('data', {})
            return data.get('net_positions', [])
    except Exception as e:
        log.error(f'Positions error: {e}')
    return []


def get_order_book():
    """Get today's orders."""
    try:
        r = requests.get(f'{BASE}/order-book?segment=EQUITY',
                        headers=headers(), timeout=10)
        if r.status_code == 200:
            return r.json().get('data', [])
    except Exception as e:
        log.error(f'Order book error: {e}')
    return []


def get_funds():
    """Get available funds/margin."""
    try:
        r = requests.get(f'{BASE}/funds', headers=headers(), timeout=10)
        if r.status_code == 200:
            data = r.json().get('data', {})
            return data.get('available_balance', 0)
    except Exception as e:
        log.error(f'Funds error: {e}')
    return 0
