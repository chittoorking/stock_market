"""Multyfi API — auth, polling, signal fetching.

Used by: multyfi_options agent, mcx_commodity_bot.
No trading logic — just API calls and response parsing.
"""
import json
import os

import requests

from trading.logger import get_logger, audit

log = get_logger('multyfi_api')
_401_sent = False

from trading.config import MULTYFI_BASE as BASE, TG_TOKEN, TG_CHAT_ID as TG_CHAT


def load_creds() -> dict:
    path = os.path.expanduser('~/multyfi_creds.json')
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        log.warning('multyfi_creds.json not found')
        return {}


def headers(creds: dict) -> dict:
    return {
        'authtoken': creds.get('authToken', ''),
        'mobile': creds.get('mobile', ''),
        'Content-Type': 'application/json',
    }


def fetch_premium(creds: dict, endpoint: str) -> list:
    """Fetch /options/premium or /futures/premium. Returns list of items."""
    try:
        r = requests.get(f'{BASE}{endpoint}',
                         headers=headers(creds), timeout=15, verify=False)
        if r.status_code == 200:
            return r.json().get('data', [])
        elif r.status_code == 401:
            global _401_sent
            if not _401_sent:
                _401_sent = True
                log.error(f'{endpoint}: Multyfi token expired (401)')
                audit('multyfi_api', 'TOKEN_EXPIRED', endpoint=endpoint)
                _alert_telegram('Multyfi token EXPIRED. Tap /refresh')
    except Exception as e:
        if 'SSL' not in str(e):
            log.warning(f'{endpoint}: {e}')
    return []


def fetch_streams(creds: dict, limit: int = 20) -> list:
    """Fetch /streams. Returns list of stream items."""
    try:
        r = requests.get(f'{BASE}/streams',
                         headers=headers(creds),
                         params={'page': 1, 'limit': limit},
                         timeout=15, verify=False)
        if r.status_code == 200:
            return r.json().get('data', [])
    except Exception as e:
        if 'SSL' not in str(e):
            log.warning(f'Streams: {e}')
    return []


def _alert_telegram(msg: str):
    try:
        requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
                      json={'chat_id': TG_CHAT, 'text': msg}, timeout=10)
    except Exception:
        pass
