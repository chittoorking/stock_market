"""
Master Client — helper for all bots to talk to the Fund Manager.
Import this in any bot to request/report capital.
"""
import requests
import json
import logging

MASTER_URL = 'http://localhost:8904'
log = logging.getLogger('master_client')


def request_batch(signals):
    """
    News bot: send ALL signals at once, get approved trades with amounts.
    signals = [{'sym': 'CEIGALL', 'projection': 8.3, 'conviction': 10, 'catalyst': 'LOI'}, ...]
    Returns: [{'trade_id': 'news_1', 'sym': 'CEIGALL', 'amount': 11333}, ...] or []
    """
    try:
        r = requests.post(f'{MASTER_URL}/batch_request',
                         json={'signals': signals}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return data.get('approved', [])
    except Exception as e:
        log.warning(f'Master batch_request failed: {e} — falling back to local sizing')
    return None  # None = master unavailable, use local fallback


def request_trade(strategy, symbol, conviction=5, projection=0, amount=0, num_ipos=1):
    """
    Single trade request from IPO/Univest/CAS.
    Returns: {'approved': True, 'trade_id': 'ipo_1', 'amount': 25000} or {'approved': False, 'reason': '...'}
    """
    try:
        r = requests.post(f'{MASTER_URL}/request',
                         json={
                             'strategy': strategy,
                             'symbol': symbol,
                             'conviction': conviction,
                             'projection': projection,
                             'amount': amount,
                             'num_ipos': num_ipos,
                         }, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning(f'Master request failed: {e} — falling back to local sizing')
    return None  # None = master unavailable


def report_exit(trade_id, pnl):
    """
    Report trade exit to Master. Frees capital.
    """
    try:
        r = requests.post(f'{MASTER_URL}/report',
                         json={'trade_id': trade_id, 'pnl': pnl}, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning(f'Master report failed: {e}')
    return None


def get_status():
    """Get current Master status."""
    try:
        r = requests.get(f'{MASTER_URL}/status', timeout=5)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None


def is_available():
    """Check if Master is running."""
    status = get_status()
    return status is not None
