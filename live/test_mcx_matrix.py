"""Monday MCX Order Matrix Test — find the right exchange/segment/product combo.

Run at 9:05 AM IST (MCX opens 9:00, before NSE 9:15).
Uses 1-lot SILVERM with LIMIT order safely below market to avoid fills.

Steps:
1. Refresh INDmoney token
2. Fetch live SILVERM sec_id from source=mcx
3. Get LTP for the instrument
4. Try every exchange × segment × product × order_type × algo_id combo
5. Log which ones get accepted vs rejected (and exact error messages)
"""
import sys
import os
import json
import time
import csv
import io
from datetime import datetime

sys.path.insert(0, os.path.expanduser('~/news-trading'))
from live.indmoney_client import get_token, headers, BASE
import requests

LOG_FILE = os.path.expanduser('~/news-trading/live/logs/mcx_matrix_test.log')


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f'{ts} | {msg}'
    print(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


def fetch_silverm_sec_id():
    """Fetch SILVERM nearest-expiry sec_id from source=mcx instrument master."""
    log('Fetching MCX instrument master...')
    r = requests.get(f'{BASE}/market/instruments',
                     params={'source': 'mcx'},
                     headers=headers(), timeout=30)
    if r.status_code != 200:
        log(f'FAIL: instruments source=mcx returned {r.status_code}: {r.text[:200]}')
        return None, None

    lines = r.text.strip().split('\n')
    log(f'MCX master: {len(lines)} lines')
    log(f'Header: {lines[0]}')

    # Parse CSV — find SILVERM futures, nearest expiry
    today = datetime.now()
    best = None
    best_expiry = None
    all_silver = []

    for line in lines[1:]:
        parts = line.split(',')
        if len(parts) < 7:
            continue
        # CSV columns: EXCH, SEGMENT, SECURITY_ID, INSTRUMENT_NAME, EXPIRY, TRADING_SYMBOL, ...
        exch = parts[0].strip()
        segment = parts[1].strip()
        sec_id = parts[2].strip()
        inst_name = parts[3].strip()
        expiry_str = parts[4].strip()
        trading_sym = parts[5].strip()

        # Look for SILVERM futures
        if 'SILVERM' in trading_sym.upper() and inst_name == 'FUTCOM':
            all_silver.append({
                'exch': exch, 'segment': segment, 'sec_id': sec_id,
                'inst_name': inst_name, 'expiry': expiry_str,
                'trading_symbol': trading_sym,
            })
            try:
                exp = datetime.strptime(expiry_str, '%d %b %Y')
                if exp >= today and (best_expiry is None or exp < best_expiry):
                    best_expiry = exp
                    best = {
                        'exch': exch, 'segment': segment, 'sec_id': sec_id,
                        'inst_name': inst_name, 'expiry': expiry_str,
                        'trading_symbol': trading_sym,
                    }
            except Exception:
                pass

    log(f'SILVERM futures found: {len(all_silver)}')
    for s in all_silver:
        log(f'  {s["trading_symbol"]} sec_id={s["sec_id"]} exp={s["expiry"]} exch={s["exch"]} seg={s["segment"]}')

    if best:
        log(f'SELECTED: {best["trading_symbol"]} sec_id={best["sec_id"]} exp={best["expiry"]}')
    else:
        log('No SILVERM futures found!')
    return best, all_silver


def get_ltp_for_sec_id(sec_id, segment_prefix='MCX'):
    """Try to get LTP using various segment prefixes."""
    for prefix in [segment_prefix, 'MCX', 'NFO', 'NSE', 'MCO']:
        scrip_code = f'{prefix}_{sec_id}'
        try:
            r = requests.get(f'{BASE}/market/quotes/ltp',
                             params={'scrip-codes': scrip_code},
                             headers=headers(), timeout=10)
            if r.status_code == 200:
                data = r.json().get('data', {})
                if data:
                    for k, v in data.items():
                        ltp = v.get('ltp', 0)
                        if ltp > 0:
                            log(f'LTP {scrip_code}: {ltp}')
                            return ltp, prefix
        except Exception as e:
            pass
    log(f'Could not get LTP for sec_id={sec_id}')
    return 0, segment_prefix


def test_order(sec_id, exchange, segment, product, order_type, algo_id, limit_price=0, qty=1):
    """Place a test order and return the result."""
    order = {
        'txn_type': 'BUY',
        'exchange': exchange,
        'segment': segment,
        'product': product,
        'order_type': order_type,
        'validity': 'DAY',
        'security_id': str(sec_id),
        'qty': qty,
        'algo_id': algo_id,
        'is_amo': False,
    }
    if order_type == 'LIMIT' and limit_price > 0:
        order['limit_price'] = limit_price

    try:
        r = requests.post(f'{BASE}/order', headers=headers(), json=order, timeout=10)
        resp = r.json() if r.status_code != 500 else {'raw': r.text[:300]}
        status = resp.get('status', resp.get('success', 'unknown'))
        order_id = resp.get('data', {}).get('order_id', '')
        error = resp.get('message', resp.get('data', {}).get('message', ''))

        # If order was actually placed, cancel it immediately!
        if status == 'success' and order_id:
            log(f'  >>> ORDER PLACED! Cancelling {order_id}...')
            cancel_r = requests.delete(f'{BASE}/order/{order_id}',
                                       headers=headers(), timeout=10)
            log(f'  >>> Cancel: {cancel_r.status_code} {cancel_r.text[:200]}')

        return {
            'http_status': r.status_code,
            'status': status,
            'order_id': order_id,
            'error': str(error)[:200],
            'exchange': exchange,
            'segment': segment,
            'product': product,
            'order_type': order_type,
            'algo_id': algo_id,
        }
    except Exception as e:
        return {
            'http_status': 0,
            'status': 'exception',
            'error': str(e)[:200],
            'exchange': exchange,
            'segment': segment,
            'product': product,
            'order_type': order_type,
            'algo_id': algo_id,
        }


def run_matrix():
    """Run the full matrix test."""
    log('=' * 70)
    log('MCX ORDER MATRIX TEST')
    log('=' * 70)

    # Step 1: Get SILVERM sec_id
    best, all_silver = fetch_silverm_sec_id()
    if not best:
        log('ABORT: No SILVERM instrument found')
        return

    sec_id = best['sec_id']
    csv_exch = best['exch']      # exchange value from the CSV
    csv_segment = best['segment']  # segment value from the CSV

    log(f'\nCSV says: exch={csv_exch} segment={csv_segment}')

    # Step 2: Get LTP
    ltp, working_prefix = get_ltp_for_sec_id(sec_id, csv_segment)
    if ltp > 0:
        # Set limit price 2% below LTP (won't fill)
        limit_price = round(ltp * 0.98)
    else:
        limit_price = 200000  # fallback for silver mini
        log(f'Using fallback limit_price={limit_price}')

    log(f'\nTest params: sec_id={sec_id} limit_price={limit_price} qty=1')

    # Step 3: Matrix test
    exchanges = ['NSE', 'BSE', 'MCX']
    segments = ['DERIVATIVE', 'EQUITY', 'COMMODITY', 'FNO']
    products = ['MARGIN', 'INTRADAY', 'CNC', 'NRML', 'MIS']
    order_types = ['LIMIT']  # LIMIT only — safer, won't fill at bad price
    algo_ids = ['99999', '9999999999999999']

    # Also add the CSV values if they're different
    if csv_exch not in exchanges:
        exchanges.append(csv_exch)
    if csv_segment not in segments:
        segments.append(csv_segment)

    total = len(exchanges) * len(segments) * len(products) * len(order_types) * len(algo_ids)
    log(f'\nTotal combinations: {total}')
    log(f'Exchanges: {exchanges}')
    log(f'Segments: {segments}')
    log(f'Products: {products}')
    log(f'Order types: {order_types}')
    log(f'Algo IDs: {algo_ids}')
    log('')

    results = []
    successes = []
    i = 0

    for exchange in exchanges:
        for segment in segments:
            for product in products:
                for order_type in order_types:
                    for algo_id in algo_ids:
                        i += 1
                        combo = f'exch={exchange} seg={segment} prod={product} ot={order_type} algo={algo_id}'
                        result = test_order(sec_id, exchange, segment, product,
                                           order_type, algo_id, limit_price, qty=1)
                        status_icon = 'OK' if result['status'] == 'success' else 'FAIL'
                        log(f'[{i}/{total}] {status_icon} | {combo} | {result["error"][:80]}')

                        results.append(result)
                        if result['status'] == 'success':
                            successes.append(result)

                        # Rate limit: don't hammer the API
                        time.sleep(0.3)

    # Summary
    log('\n' + '=' * 70)
    log('RESULTS SUMMARY')
    log('=' * 70)
    log(f'Total tested: {len(results)}')
    log(f'Successes: {len(successes)}')
    log(f'Failures: {len(results) - len(successes)}')

    if successes:
        log('\n>>> WORKING COMBINATIONS:')
        for s in successes:
            log(f'  exchange={s["exchange"]} segment={s["segment"]} product={s["product"]} '
                f'order_type={s["order_type"]} algo_id={s["algo_id"]}')
    else:
        log('\n>>> NO WORKING COMBINATION FOUND')
        # Show unique error messages
        errors = {}
        for r in results:
            e = r['error'][:100]
            errors[e] = errors.get(e, 0) + 1
        log('\nError distribution:')
        for e, count in sorted(errors.items(), key=lambda x: -x[1]):
            log(f'  [{count}x] {e}')

    # Save full results
    results_file = os.path.expanduser('~/news-trading/live/logs/mcx_matrix_results.json')
    with open(results_file, 'w') as f:
        json.dump({'sec_id': sec_id, 'ltp': ltp, 'limit_price': limit_price,
                   'csv_exch': csv_exch, 'csv_segment': csv_segment,
                   'results': results, 'successes': successes}, f, indent=2)
    log(f'\nFull results saved to {results_file}')


if __name__ == '__main__':
    run_matrix()
