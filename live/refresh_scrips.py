"""Refresh scrip codes + FnO instruments from INDmoney instrument master API."""
import sys; sys.path.insert(0, "/home/ai18developer/news-trading")
import json, requests, csv, io
from datetime import datetime
from live.indmoney_client import headers

DATA_DIR = "/home/ai18developer/news-trading/data"
h = headers()

# ================================================================
# 1. EQUITY — all NSE + BSE stocks
# ================================================================
r = requests.get("https://api.indstocks.com/market/instruments?source=equity", headers=h, timeout=30)
reader = csv.DictReader(io.StringIO(r.text))
new_scrips = {}
for row in reader:
    exch = row.get("EXCH", "")
    sym = row.get("TRADING_SYMBOL", "").strip()
    sec_id = row.get("SECURITY_ID", "").strip()
    if not sym or not sec_id or row.get("SEGMENT", "") != "E":
        continue
    if sym not in new_scrips:
        new_scrips[sym] = exch + "_" + sec_id

# Load old and merge (keep existing, add new)
scrip_path = DATA_DIR + "/all_nse_scrips.json"
try:
    with open(scrip_path) as f:
        old_scrips = json.load(f)
except:
    old_scrips = {}

merged = dict(old_scrips)
added = 0
for sym, scrip in new_scrips.items():
    if sym not in merged:
        merged[sym] = scrip
        added += 1

with open(scrip_path, "w") as f:
    json.dump(merged, f)
print("Equity scrips:", len(merged), "(+" + str(added) + " new)")

# ================================================================
# 2. FnO — all futures + options with sec_id, expiry, strike
# ================================================================
r2 = requests.get("https://api.indstocks.com/market/instruments?source=fno", headers=h, timeout=60)
reader2 = csv.DictReader(io.StringIO(r2.text))

fno_instruments = {}  # trading_symbol -> {sec_id, expiry, strike, opt_type, lot_size, ...}
today = datetime.now().strftime("%Y-%m-%d")

for row in reader2:
    sym = row.get("TRADING_SYMBOL", "").strip()
    sec_id = row.get("SECURITY_ID", "").strip()
    expiry = row.get("EXPIRY_DATE", "").strip()
    strike = row.get("STRIKE_PRICE", "0").strip()
    opt_type = row.get("OPT_TYPE", "").strip()
    lot = row.get("LOT_UNITS", "0").strip()
    custom = row.get("CUSTOM_SYMBOL", "").strip()
    inst = row.get("INSTRUMENT_NAME", "").strip()

    if not sym or not sec_id:
        continue

    # Skip expired instruments (format: "11/23/2026 14:00" or "YYYY-MM-DD")
    if expiry:
        try:
            if '/' in expiry:
                exp_date = datetime.strptime(expiry.split(' ')[0], '%m/%d/%Y').strftime('%Y-%m-%d')
            else:
                exp_date = expiry[:10]
            if exp_date < today:
                continue
            expiry = exp_date
        except:
            pass

    fno_instruments[sym] = {
        "sec_id": sec_id,
        "expiry": expiry,
        "strike": strike,
        "opt_type": opt_type,
        "lot": lot,
        "custom": custom,
        "instrument": inst,
    }

fno_path = DATA_DIR + "/fno_instruments.json"
with open(fno_path, "w") as f:
    json.dump(fno_instruments, f)
print("FnO instruments:", len(fno_instruments), "(active, not expired)")

# ================================================================
# 3. MCX — commodities
# ================================================================
r3 = requests.get("https://api.indstocks.com/market/instruments?source=mcx", headers=h, timeout=30)
reader3 = csv.DictReader(io.StringIO(r3.text))
mcx_instruments = {}
for row in reader3:
    sym = row.get("TRADING_SYMBOL", "").strip()
    sec_id = row.get("SECURITY_ID", "").strip()
    expiry = row.get("EXPIRY_DATE", "").strip()
    if not sym or not sec_id:
        continue
    if expiry:
        try:
            if '/' in expiry:
                exp_date = datetime.strptime(expiry.split(' ')[0], '%m/%d/%Y').strftime('%Y-%m-%d')
            else:
                exp_date = expiry[:10]
            if exp_date < today:
                continue
            expiry = exp_date
        except:
            pass
    mcx_instruments[sym] = {
        "sec_id": sec_id,
        "expiry": expiry,
        "lot": row.get("LOT_UNITS", "0").strip(),
        "custom": row.get("CUSTOM_SYMBOL", "").strip(),
    }

mcx_path = DATA_DIR + "/mcx_instruments.json"
with open(mcx_path, "w") as f:
    json.dump(mcx_instruments, f)
print("MCX instruments:", len(mcx_instruments), "(active)")

print("Done -", datetime.now().strftime("%Y-%m-%d %H:%M"))
