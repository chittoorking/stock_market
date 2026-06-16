#!/usr/bin/env python3
"""Find INDstocks scrip codes for NIFTY Next 50 stocks.

Step 1: Fetch all instruments → data/instruments.json (one-time, save to disk)
Step 2: Match NIFTY Next 50 symbols → print scrip codes to add
Step 3: Verify codes via LTP

Run: python find_next50_scrips.py
"""
import sys, json, time, requests
from pathlib import Path
sys.path.insert(0, '.')
from live import indmoney_client as api

BASE = 'https://api.indstocks.com'
INSTRUMENTS_FILE = Path('data/instruments.json')

# NIFTY Next 50 stocks (these are NOT in current NIFTY 50 universe)
NEXT50_SYMBOLS = [
    'ABB', 'ADANIENSOL', 'ADANIGREEN', 'ADANIPOWER',
    'AMBUJACEM', 'BAJAJHLDNG', 'BANKBARODA', 'BOSCHLTD',
    'CANBK', 'CGPOWER', 'CHOLAFIN', 'CUMMINSIND',
    'COLPAL', 'DLF', 'DMART', 'GAIL', 'GODREJCP',
    'HAL', 'HAVELLS', 'HDFCAMC', 'HINDZINC',
    'ICICIPRULI', 'INDHOTEL', 'INDUSTOWER', 'IOC',
    'IRCTC', 'IRFC', 'JINDALSTEL', 'LODHA',
    'LTIM', 'MCDOWELL-N', 'MOTHERSON', 'MUTHOOTFIN',
    'OFSS', 'PFC', 'PIDILITIND', 'PNB', 'RECLTD',
    'SHREECEM', 'SIEMENS', 'SOLARINDS', 'TATAPOWER',
    'TORNTPHARM', 'TVSMOTOR', 'UNIONBANK', 'UNITDSPR',
    'VBL', 'VEDL', 'ZYDUSLIFE',
]


def fetch_and_save_instruments():
    """Fetch full instruments list and save to disk."""
    print('Fetching instruments from INDstocks...')
    # Try different endpoints
    endpoints = [
        '/market/instruments?source=equity',
        '/market/instruments',
        '/market/instruments?exchange=NSE',
        '/market/instruments?limit=5000',
    ]
    for ep in endpoints:
        try:
            r = requests.get(f'{BASE}{ep}', headers=api.headers(), timeout=30)
            print(f'  {ep}: HTTP {r.status_code}')
            if r.status_code == 200:
                data = r.json()
                # Find the list of instruments
                if isinstance(data, list):
                    instruments = data
                elif isinstance(data, dict):
                    for key in ['data', 'instruments', 'results', 'items']:
                        if key in data and isinstance(data[key], list):
                            instruments = data[key]
                            break
                    else:
                        instruments = []
                else:
                    instruments = []

                if instruments:
                    INSTRUMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
                    INSTRUMENTS_FILE.write_text(json.dumps(instruments, indent=2))
                    print(f'  Saved {len(instruments)} instruments to {INSTRUMENTS_FILE}')
                    return instruments
                else:
                    print(f'  No instruments in response: {str(data)[:200]}')
        except Exception as e:
            print(f'  Error: {e}')
    return None


def load_instruments():
    if INSTRUMENTS_FILE.exists():
        data = json.loads(INSTRUMENTS_FILE.read_text())
        print(f'Loaded {len(data)} instruments from {INSTRUMENTS_FILE}')
        return data
    return None


def find_scrip_codes(instruments):
    """Search instruments list for NIFTY Next 50 symbols."""
    found = {}
    not_found = []

    # Build index: symbol → scrip_code
    # INDstocks instrument fields may vary — try common names
    sym_index = {}
    for inst in instruments:
        # Try various field names for symbol/ticker
        for sym_field in ['symbol', 'ticker', 'scrip_symbol', 'tradingSymbol', 'trading_symbol']:
            sym = inst.get(sym_field, '').upper().strip()
            if sym:
                # Try various field names for scrip code
                for code_field in ['scrip_code', 'scripCode', 'scrip', 'instrumentKey']:
                    code = inst.get(code_field, '')
                    if code and 'NSE' in str(code).upper():
                        sym_index[sym] = str(code)
                        break
                break

    print(f'Built index with {len(sym_index)} NSE symbols')
    if sym_index:
        # Show a few examples
        sample = list(sym_index.items())[:5]
        print(f'Sample: {sample}')

    for sym in NEXT50_SYMBOLS:
        code = sym_index.get(sym)
        if code:
            found[sym] = code
            print(f'  FOUND  {sym}: {code}')
        else:
            not_found.append(sym)
            print(f'  MISS   {sym}')

    return found, not_found


# --- Main ---
instruments = load_instruments()
if not instruments:
    instruments = fetch_and_save_instruments()

if not instruments:
    print()
    print('Could not fetch instruments. Token may be expired.')
    print('Refresh token at: https://app.indmoney.com → Settings → API')
    print('Then save to: data/indmoney_token.txt')
    print()
    # Fall back to brute-force LTP check with estimated NSE IDs
    print('Trying brute-force with known NSE security IDs...')
    # Known NSE security IDs for NIFTY Next 50 (from NSE bhavcopy data)
    ESTIMATED_IDS = {
        'ABB': 'NSE_20070', 'ADANIGREEN': 'NSE_21871', 'ADANIPOWER': 'NSE_18388',
        'AMBUJACEM': 'NSE_1270', 'BANKBARODA': 'NSE_1452', 'BOSCHLTD': 'NSE_1080',
        'CANBK': 'NSE_10180', 'CGPOWER': 'NSE_900', 'CHOLAFIN': 'NSE_2972',
        'COLPAL': 'NSE_772', 'CUMMINSIND': 'NSE_1700', 'DLF': 'NSE_14366',
        'DMART': 'NSE_20640', 'GAIL': 'NSE_4717', 'GODREJCP': 'NSE_10099',
        'HAL': 'NSE_14307', 'HAVELLS': 'NSE_14590', 'HDFCAMC': 'NSE_21807',
        'HINDZINC': 'NSE_1279', 'ICICIPRULI': 'NSE_18652', 'INDHOTEL': 'NSE_1550',
        'INDUSTOWER': 'NSE_29135', 'IOC': 'NSE_1624', 'IRCTC': 'NSE_28901',
        'IRFC': 'NSE_24143', 'JINDALSTEL': 'NSE_11243', 'LODHA': 'NSE_24954',
        'LTIM': 'NSE_17818', 'MUTHOOTFIN': 'NSE_17622', 'PFC': 'NSE_14299',
        'PIDILITIND': 'NSE_2664', 'PNB': 'NSE_2730', 'RECLTD': 'NSE_14383',
        'SHREECEM': 'NSE_3410', 'SIEMENS': 'NSE_3150', 'TATAPOWER': 'NSE_3426',
        'TORNTPHARM': 'NSE_3839', 'TVSMOTOR': 'NSE_3937', 'UNIONBANK': 'NSE_10355',
        'UNITDSPR': 'NSE_7269', 'VBL': 'NSE_16713', 'VEDL': 'NSE_3063',
        'ZYDUSLIFE': 'NSE_18097', 'MOTHERSON': 'NSE_3405', 'SOLARINDS': 'NSE_16213',
        'BAJAJHLDNG': 'NSE_1102', 'OFSS': 'NSE_18365',  # OFSS might share with APOLLOHOSP
        'MCDOWELL-N': 'NSE_5765',
    }
    print(f'Testing {len(ESTIMATED_IDS)} estimated IDs via LTP...')
    # Add all to API temporarily
    for sym, code in ESTIMATED_IDS.items():
        api.SCRIP_CODES[sym] = code
        api.SYM_FROM_SCRIP[code] = sym

    syms = list(ESTIMATED_IDS.keys())
    scrips = [ESTIMATED_IDS[s] for s in syms]
    keys = ','.join(scrips)
    try:
        r = requests.get(f'{BASE}/market/quotes/ltp?scrip-codes={keys}',
                        headers=api.headers(), timeout=20)
        print(f'LTP check: HTTP {r.status_code}')
        if r.status_code == 200:
            data = r.json().get('data', {})
            verified = {}
            failed = []
            for sym in syms:
                code = ESTIMATED_IDS[sym]
                if code in data:
                    ltp = data[code].get('live_price', 0)
                    verified[sym] = (code, ltp)
                    print(f'  OK  {sym}: {code} → Rs {ltp:.2f}')
                else:
                    failed.append(sym)
                    print(f'  BAD {sym}: {code} — no data')
            print()
            print(f'Verified: {len(verified)}, Failed: {len(failed)}')
            if failed:
                print(f'Need to find IDs for: {failed}')
    except Exception as e:
        print(f'LTP check failed: {e}')
    sys.exit(0)

found, not_found = find_scrip_codes(instruments)

print()
print(f'Found: {len(found)}, Not found: {len(not_found)}')
print()

if found:
    print('Add these to SCRIP_CODES in live/indmoney_client.py:')
    print()
    items = sorted(found.items())
    for i in range(0, len(items), 4):
        chunk = items[i:i+4]
        line = ', '.join(f"'{sym}': '{code}'" for sym, code in chunk)
        print(f'    {line},')

if not_found:
    print()
    print(f'Still missing ({len(not_found)}): {not_found}')
