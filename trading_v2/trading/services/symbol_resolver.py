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


def _parse_multyfi_symbol(trading_symbol: str) -> dict:
    """Parse Multyfi format: NIFTY26OCT22800PE, SIEMENS26OCTFUT, DLF26OCT660CE.
    Returns {base, month, year2, strike, opt_type, is_fut}."""
    ts = trading_symbol.upper().strip()

    is_fut = ts.endswith('FUT')
    is_ce = ts.endswith('CE') and not is_fut
    is_pe = ts.endswith('PE') and not is_fut

    # Extract month
    month_match = re.search(r'(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)', ts)
    if not month_match:
        return {}

    year2 = month_match.group(1)
    month_abbr = month_match.group(2)
    base = ts[:month_match.start()]

    # Extract strike for options
    strike = 0
    if is_ce or is_pe:
        suffix = ts[month_match.end():]  # e.g. "22800PE" or "660CE"
        strike_match = re.match(r'(\d+(?:\.\d+)?)', suffix)
        if strike_match:
            strike = float(strike_match.group(1))

    opt_type = 'CE' if is_ce else ('PE' if is_pe else ('FUT' if is_fut else ''))

    return {
        'base': base, 'month': month_abbr, 'year2': year2,
        'strike': strike, 'opt_type': opt_type, 'is_fut': is_fut,
    }


def _month_abbr_to_full(abbr: str) -> str:
    """OCT -> Oct"""
    m = {'JAN': 'Jan', 'FEB': 'Feb', 'MAR': 'Mar', 'APR': 'Apr',
         'MAY': 'May', 'JUN': 'Jun', 'JUL': 'Jul', 'AUG': 'Aug',
         'SEP': 'Sep', 'OCT': 'Oct', 'NOV': 'Nov', 'DEC': 'Dec'}
    return m.get(abbr.upper(), abbr)


def resolve_fno_sec_id(trading_symbol: str) -> str:
    """Resolve any FnO trading symbol to numeric sec_id from instrument master.

    Handles:
      Multyfi format:  NIFTY26OCT22800PE, SIEMENS26OCTFUT
      INDmoney format: NIFTY-Oct2026-22800-PE, SIEMENS-Oct2026-FUT
    """
    instruments = _load_fno()

    # 1. Direct key match (already in INDmoney format)
    if trading_symbol in instruments:
        sec_id = instruments[trading_symbol]['sec_id']
        log.info(f'Resolved {trading_symbol} -> sec_id={sec_id} (direct)')
        return sec_id

    # 2. Parse Multyfi format and match against INDmoney keys
    parsed = _parse_multyfi_symbol(trading_symbol)
    if not parsed:
        log.warning(f'Cannot parse: {trading_symbol}')
        return ''

    base = parsed['base']
    month_full = _month_abbr_to_full(parsed['month'])
    year4 = '20' + parsed['year2']
    strike = parsed['strike']
    is_fut = parsed['is_fut']
    opt_type = parsed['opt_type']

    # Build expected INDmoney key patterns:
    # Options: NIFTY-Oct2026-22800-PE  or  NIFTY-Oct2026-22800.0-PE
    # Futures: SIEMENS-Oct2026-FUT
    if is_fut:
        candidate_keys = [
            f'{base}-{month_full}{year4}-FUT',
            f'{base}-{month_full}{year4}--FUT',
        ]
    else:
        strike_strs = [str(int(strike))] if strike == int(strike) else [str(strike)]
        strike_strs.append(f'{strike:.5f}')  # some keys have 22350.00000
        candidate_keys = []
        for ss in strike_strs:
            candidate_keys.append(f'{base}-{month_full}{year4}-{ss}-{opt_type}')

    for ck in candidate_keys:
        if ck in instruments:
            sec_id = instruments[ck]['sec_id']
            log.info(f'Resolved {trading_symbol} -> {ck} -> sec_id={sec_id}')
            return sec_id

    # 3. Fuzzy match: search all instruments matching base + type + strike + month
    today = datetime.now().strftime('%Y-%m-%d')
    candidates = []

    for key, data in instruments.items():
        # Key format: BASE-MonYYYY-STRIKE-TYPE or BASE-MonYYYY-FUT
        parts = key.split('-')
        if len(parts) < 2:
            continue

        key_base = parts[0].upper()
        if key_base != base:
            continue

        # Check expired
        exp = data.get('expiry', '9999-99-99')
        if exp < today:
            continue

        key_upper = key.upper()

        # Type match
        if is_fut:
            if 'FUT' not in key_upper:
                continue
        elif opt_type == 'CE':
            if not key_upper.endswith('CE'):
                continue
        elif opt_type == 'PE':
            if not key_upper.endswith('PE'):
                continue

        # Month match
        if month_full and month_full not in key:
            continue

        # Strike match for options
        if strike > 0:
            key_strike = float(data.get('strike', 0) or 0)
            if abs(key_strike - strike) > 0.01:
                continue

        candidates.append((key, data))

    if not candidates:
        log.warning(f'No match for {trading_symbol} (base={base}, strike={strike}, type={opt_type})')
        return ''

    # Pick nearest expiry
    candidates.sort(key=lambda x: x[1].get('expiry', '9999-99-99'))
    best_key, best_data = candidates[0]
    sec_id = best_data['sec_id']
    log.info(f'Resolved {trading_symbol} -> {best_key} -> sec_id={sec_id} (fuzzy, expiry={best_data.get("expiry")})')
    return sec_id


def resolve_mcx_sec_id(sym: str, broker=None) -> str:
    """Resolve MCX futures symbol to sec_id from instrument master."""
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

    today = datetime.now().strftime('%Y-%m-%d')
    is_fut = sym.upper().endswith('FUT')
    candidates = []
    for isym, data in instruments.items():
        isym_upper = isym.upper()
        # Base name must match exactly (GOLD != GOLDM)
        # MCX keys: "GOLD 05 Oct Fut", "GOLDM 05 Oct Fut", "CRUDEOIL 19 Oct Fut"
        isym_base = isym_upper.split()[0] if ' ' in isym else isym_upper.split()[0]
        if isym_base != base.upper():
            continue
        # Skip MINI unless requested
        if 'MINI' in isym_upper and 'MINI' not in sym.upper():
            continue
        # If looking for futures, only match futures
        if is_fut and 'FUT' not in isym_upper:
            continue
        exp = data.get('expiry', '9999-99-99')
        if exp < today:
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

    Tries: 1) FnO instrument master, 2) broker option chain, 3) give up.
    """
    premium = 0

    # Try 1: Use Multyfi's instrument field to look up in master
    if instrument:
        trading_sym = instrument.split(':')[-1] if ':' in instrument else instrument
        sec_id = resolve_fno_sec_id(trading_sym)
        if sec_id:
            return sec_id, premium

    # Try 2: Build trading symbol from components and look up
    if sym and opt_type and strike:
        now = datetime.now()
        months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                  'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
        month = months[now.month - 1]
        year = str(now.year)[2:]
        strike_str = str(int(strike)) if strike == int(strike) else str(strike)
        built_sym = f'{sym}{year}{month}{strike_str}{opt_type}'
        sec_id = resolve_fno_sec_id(built_sym)
        if sec_id:
            return sec_id, premium

    # Try 3: Broker option chain (only works during market hours)
    try:
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
                log.info(f'Resolved {sym} {opt_type} {strike} -> sec_id={best_sec_id} (option chain)')
                return best_sec_id, premium
    except Exception as e:
        log.warning(f'Option chain fallback failed: {e}')

    log.warning(f'Cannot resolve: {sym} {opt_type} {strike}')
    return None, 0
