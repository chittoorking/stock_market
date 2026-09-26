"""Symbol resolution — map Multyfi symbols to broker instrument IDs.

MCX futures: resolve via instrument master CSV.
NSE options: resolve via option chain or Multyfi instrument ID.
"""
import re
from datetime import datetime

from trading.logger import get_logger

log = get_logger('symbol_resolver')


def resolve_mcx_sec_id(sym: str, broker) -> str:
    """Resolve Multyfi futures symbol to INDmoney numeric sec_id.

    NATURALGAS26SEPFUT → sec_id from MCX instrument master.
    """
    import requests
    try:
        h = {'Authorization': broker._api.get_token()}
        r = requests.get('https://api.indstocks.com/market/instruments?source=mcx',
                         headers=h, timeout=30)
        if r.status_code != 200:
            return ''

        base = re.sub(r'\d{2}[A-Z]{3}FUT$', '', sym).strip() or sym
        today = datetime.now()
        best_id = ''
        best_expiry = None

        for line in r.text.split('\n'):
            parts = line.split(',')
            if len(parts) < 7 or parts[3] != 'FUTCOM':
                continue
            trading_sym = parts[5].strip().upper()
            if base.upper() not in trading_sym.replace(' ', ''):
                continue
            if 'MINI' in trading_sym and 'MINI' not in sym.upper() and sym[-1:] != 'M':
                continue

            sec_id = parts[2].strip()
            expiry_str = parts[4].strip()
            try:
                expiry = datetime.strptime(expiry_str, '%d %b %Y')
                if expiry < today:
                    continue
                if best_expiry is None or expiry < best_expiry:
                    best_expiry = expiry
                    best_id = sec_id
            except Exception:
                continue

        if best_id:
            log.info(f'Resolved {sym} -> sec_id={best_id} expiry={best_expiry}')
        return best_id
    except Exception as e:
        log.warning(f'Resolve error {sym}: {e}')
        return ''


def resolve_option_sec_id(sym: str, opt_type: str, strike: float,
                          instrument: str, broker) -> tuple:
    """Resolve option instrument. Returns (sec_id, premium).

    Tries: 1) Multyfi instrument ID, 2) broker option chain, 3) give up.
    """
    best_sec_id = None
    premium = 0

    # Try 1: Multyfi instrument ID
    if instrument:
        best_sec_id = instrument.split(':')[-1] if ':' in instrument else instrument
        log.info(f'{sym}: using Multyfi instrument {best_sec_id}')
        return best_sec_id, premium

    # Try 2: Option chain from broker
    chain = broker.option_chain(sym)
    if chain:
        target_strike = strike or broker.ltp_safe(sym)
        if target_strike <= 0:
            return None, 0

        best_diff = float('inf')
        for sd in chain:
            s = sd.get('strike_price', 0)
            if abs(s - target_strike) < best_diff:
                best_diff = abs(s - target_strike)
                od = sd.get('ce' if opt_type == 'CE' else 'pe', {})
                best_sec_id = od.get('security_id')
                premium = od.get('last_price', 0) or premium

    if not best_sec_id:
        log.warning(f'{sym}: no option found')

    return best_sec_id, premium
