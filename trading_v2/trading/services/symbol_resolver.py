"""Symbol resolution — map Multyfi symbols to broker instrument IDs.

Uses locally cached instrument master files (refreshed daily).
Falls back to broker option chain API during market hours.
"""
import json
import os
import re
from datetime import datetime

from trading.logger import get_logger

log = get_logger('symbol_resolver')

DATA_DIR = os.path.expanduser('~/news-trading/data')

_fno_cache = None
_mcx_cache = None


def _load_fno():
    global _fno_cache
    if _fno_cache is not None:
        return _fno_cache
    path = os.path.join(DATA_DIR, 'fno_instruments.json')
    try:
        with open(path) as f:
            _fno_cache = json.load(f)
        log.info(f'Loaded {len(_fno_cache)} FnO instruments')
    except:
        _fno_cache = {}
    return _fno_cache


def _load_mcx():
    global _mcx_cache
    if _mcx_cache is not None:
        return _mcx_cache
    path = os.path.join(DATA_DIR, 'mcx_instruments.json')
    try:
        with open(path) as f:
            _mcx_cache = json.load(f)
        log.info(f'Loaded {len(_mcx_cache)} MCX instruments')
    except:
        _mcx_cache = {}
    return _mcx_cache


def resolve_fno_sec_id(trading_symbol: str) -> str:
    """Resolve any FnO trading symbol to sec_id from instrument master.

    SIEMENS26OCTFUT -> sec_id
    NIFTY26OCT22800PE -> sec_id
    DLF26OCT660CE -> sec_id
    """
    instruments = _load_fno()

    # Direct match first
    if trading_symbol in instruments:
        sec_id = instruments[trading_symbol]['sec_id']
        log.info(f'Resolved {trading_symbol} -> sec_id={sec_id} (direct)')
        return sec_id

    # Try with spaces (INDmoney sometimes uses "NIFTY 26OCT 22800 PE")
    for sym, data in instruments.items():
        # Normalize both to uppercase, no spaces
        if sym.replace(' ', '').upper() == trading_symbol.replace(' ', '').upper():
            log.info(f'Resolved {trading_symbol} -> sec_id={data["sec_id"]} (normalized)')
            return data['sec_id']

    # Parse Multyfi format: NIFTY26OCT22800PE, SIEMENS26OCTFUT
    base = re.sub(r'\d{2}(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\w*(?:\d+)?(?:CE|PE|FUT)$',
                  '', trading_symbol, flags=re.IGNORECASE).strip()

    if not base:
        log.warning(f'Cannot extract base from {trading_symbol}')
        return ''

    is_fut = trading_symbol.upper().endswith('FUT')
    is_ce = trading_symbol.upper().endswith('CE')
    is_pe = trading_symbol.upper().endswith('PE')

    strike_match = re.search(r'(\d+)(?:CE|PE)$', trading_symbol, re.IGNORECASE)
    target_strike = float(strike_match.group(1)) if strike_match else 0

    # Extract month from Multyfi symbol
    month_match = re.search(r'\d{2}(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)',
                            trading_symbol, re.IGNORECASE)
    target_month = month_match.group(1).upper() if month_match else ''

    # INDmoney format: NIFTY-Oct2026-22800-PE, SIEMENS-Oct2026-FUT
    # Match by: base name + type + strike + month
    month_map = {'JAN': 'Jan', 'FEB': 'Feb', 'MAR': 'Mar', 'APR': 'Apr',
                 'MAY': 'May', 'JUN': 'Jun', 'JUL': 'Jul', 'AUG': 'Aug',
                 'SEP': 'Sep', 'OCT': 'Oct', 'NOV': 'Nov', 'DEC': 'Dec'}
    target_month_long = month_map.get(target_month, '')

    candidates = []
    for sym, data in instruments.items():
        # Check base name match (NIFTY in NIFTY-Oct2026-22800-PE)
        sym_base = sym.split('-')[0].upper() if '-' in sym else sym.upper()
        if sym_base != base.upper():
            continue

        # Check type match
        sym_upper = sym.upper()
        if is_fut and 'FUT' not in sym_upper:
            continue
        if is_ce and not sym_upper.endswith('CE'):
            continue
        if is_pe and not sym_upper.endswith('PE'):
            continue

        # Check strike match for options
        if target_strike > 0:
            try:
                sym_strike = float(data.get('strike', 0))
                if sym_strike != target_strike:
                    continue
            except:
                continue

        # Check month match
        if target_month_long and target_month_long not in sym:
            continue

        candidates.append((sym, data))

    if not candidates:
        log.warning(f'No match for {trading_symbol} (base={base})')
        return ''

    # Pick nearest expiry
    today = datetime.now().strftime('%Y-%m-%d')
    candidates.sort(key=lambda x: x[1].get('expiry', '9999-99-99'))
    best = candidates[0]
    log.info(f'Resolved {trading_symbol} -> sec_id={best[1]["sec_id"]} ({best[0]}, expiry={best[1].get("expiry")})')
    return best[1]['sec_id']


def resolve_mcx_sec_id(sym: str, broker=None) -> str:
    """Resolve MCX futures symbol to sec_id from instrument master.

    CRUDEOIL26OCTFUT -> sec_id
    """
    instruments = _load_mcx()

    # Direct match
    if sym in instruments:
        sec_id = instruments[sym]['sec_id']
        log.info(f'MCX resolved {sym} -> sec_id={sec_id}')
        return sec_id

    # Extract base
    base = re.sub(r'\d{2}[A-Z]{3}FUT$', '', sym, flags=re.IGNORECASE).strip()
    if not base:
        return ''

    candidates = []
    for isym, data in instruments.items():
        if base.upper() in isym.upper().replace(' ', ''):
            if 'MINI' in isym.upper() and 'MINI' not in sym.upper():
                continue
            candidates.append((isym, data))

    if not candidates:
        log.warning(f'MCX no match for {sym}')
        return ''

    # Nearest expiry
    candidates.sort(key=lambda x: x[1].get('expiry', '9999-99-99'))
    best = candidates[0]
    log.info(f'MCX resolved {sym} -> sec_id={best[1]["sec_id"]} ({best[0]})')
    return best[1]['sec_id']


def resolve_option_sec_id(sym: str, opt_type: str, strike: float,
                          instrument: str, broker) -> tuple:
    """Resolve option instrument. Returns (sec_id, premium).

    Tries: 1) FnO instrument master, 2) Multyfi instrument ID,
           3) broker option chain, 4) give up.
    """
    premium = 0

    # Try 1: FnO instrument master (fastest, works offline)
    if instrument:
        # Multyfi sends "NFO:SBIN26SEPFUT" or "NFO:NIFTY26OCT22800PE"
        trading_sym = instrument.split(':')[-1] if ':' in instrument else instrument
        sec_id = resolve_fno_sec_id(trading_sym)
        if sec_id:
            return sec_id, premium

    # Try 2: Build trading symbol from components and look up
    # This handles cases where Multyfi doesn't send the instrument field
    if sym and opt_type and strike:
        # Try common formats
        now = datetime.now()
        months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                  'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
        month = months[now.month - 1]
        year = str(now.year)[2:]
        strike_str = str(int(strike)) if strike == int(strike) else str(strike)

        for try_sym in [
            f'{sym}{year}{month}{strike_str}{opt_type}',
            f'{sym} {year}{month} {strike_str} {opt_type}',
        ]:
            sec_id = resolve_fno_sec_id(try_sym)
            if sec_id:
                return sec_id, premium

    # Try 3: Broker option chain (only works during market hours)
    chain = broker.option_chain(sym)
    if chain:
        target_strike = strike or broker.ltp_safe(sym)
        if target_strike <= 0:
            return None, 0

        best_sec_id = None
        best_diff = float('inf')
        for sd in chain:
            s = sd.get('strike_price', 0)
            if abs(s - target_strike) < best_diff:
                best_diff = abs(s - target_strike)
                od = sd.get('ce' if opt_type == 'CE' else 'pe', {})
                best_sec_id = od.get('security_id')
                premium = od.get('last_price', 0) or premium

        if best_sec_id:
            return best_sec_id, premium

    log.warning(f'Cannot resolve: {sym} {opt_type} {strike}')
    return None, 0
