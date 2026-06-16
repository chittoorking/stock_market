#!/usr/bin/env python3
"""Verify all SCRIP_CODES are valid by fetching LTP for each.

Run after refreshing token: python verify_scrips.py
Prints which codes work, which fail, and suggests fixes.
"""
import sys, json, time, requests
sys.path.insert(0, '.')
from live import indmoney_client as api

BASE = 'https://api.indstocks.com'

# Check token first
token = api.get_token()
if not token:
    print('ERROR: No token found. Save to data/indmoney_token.txt')
    sys.exit(1)

print(f'Testing {len(api.SCRIP_CODES)} scrip codes...')
print()

# Split into NIFTY 50 (verified) and Next 50 (estimated)
NIFTY50 = {
    'ADANIENT','ADANIPORTS','APOLLOHOSP','ASIANPAINT','AXISBANK','BAJAJ-AUTO','BAJFINANCE',
    'BAJAJFINSV','BPCL','BHARTIARTL','BRITANNIA','CIPLA','COALINDIA','DIVISLAB','DRREDDY',
    'EICHERMOT','GRASIM','HCLTECH','HDFCBANK','HDFCLIFE','HEROMOTOCO','HINDALCO','HINDUNILVR',
    'ICICIBANK','ITC','INDUSINDBK','INFY','JSWSTEEL','KOTAKBANK','LT','M&M','MARUTI','NTPC',
    'NESTLEIND','ONGC','POWERGRID','RELIANCE','SBILIFE','SBIN','SHRIRAMFIN','SUNPHARMA','TCS',
    'TATACONSUM','TATAMOTORS','TATASTEEL','TECHM','TITAN','UPL','ULTRACEMCO','WIPRO'
}

all_syms = list(api.SCRIP_CODES.keys())
scrips = list(api.SCRIP_CODES.values())

# Fetch in batches of 15
ok = {}
bad = {}

for i in range(0, len(scrips), 15):
    batch_scrips = scrips[i:i+15]
    batch_syms = all_syms[i:i+15]
    keys = ','.join(batch_scrips)
    try:
        r = requests.get(f'{BASE}/market/quotes/ltp?scrip-codes={keys}',
                        headers=api.headers(), timeout=15)
        if r.status_code == 200:
            data = r.json().get('data', {})
            for sym, scrip in zip(batch_syms, batch_scrips):
                if scrip in data:
                    ltp = data[scrip].get('live_price', 0)
                    ok[sym] = (scrip, ltp)
                else:
                    bad[sym] = scrip
        else:
            print(f'  Batch {i//15+1}: HTTP {r.status_code}')
            for sym, scrip in zip(batch_syms, batch_scrips):
                bad[sym] = scrip
    except Exception as e:
        print(f'  Batch error: {e}')
    time.sleep(0.3)

print(f'OK: {len(ok)}/{len(all_syms)} | BAD: {len(bad)}')
print()

# Show results
if ok:
    n50_ok = [(s,c,p) for s,(c,p) in ok.items() if s in NIFTY50]
    next50_ok = [(s,c,p) for s,(c,p) in ok.items() if s not in NIFTY50]
    print(f'NIFTY 50 working: {len(n50_ok)}/50')
    print(f'NIFTY Next 50 working: {len(next50_ok)}/40')

if bad:
    print()
    print(f'FAILED codes ({len(bad)}):')
    for sym, scrip in sorted(bad.items()):
        tag = '' if sym in NIFTY50 else ' [estimated]'
        print(f'  {sym}: {scrip}{tag}')
    print()
    print('To fix: brute-force search around the estimated ID +/- 500')
    print('  Example: python brute_force_scrip.py SYMBOL estimated_id')

# Show all working Next 50 codes
if next50_ok:
    print()
    print('Working NIFTY Next 50 codes (add to indmoney_client.py if not already):')
    for sym, code, ltp in sorted(next50_ok):
        print(f"  '{sym}': '{code}',  # Rs {ltp:.2f}")
