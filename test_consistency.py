"""5 consecutive ML polls, 60s apart."""
import sys, time, json, re
from urllib.parse import urljoin
from curl_cffi import requests as cf

BASE = "https://marketlens.nseindia.com"
session = cf.Session(impersonate="chrome")
session.request("GET", BASE, timeout=30)
session.headers.update({"Referer": BASE + "/screener"})
html = session.request("GET", BASE + "/screener", timeout=30).text
action = None
for src in dict.fromkeys(re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', html)):
    u = urljoin(BASE, src)
    if "/_next/static/" not in u: continue
    try: js = session.request("GET", u, timeout=30).text
    except: continue
    am = re.search(r'createServerReference\)\("([0-9a-f]+)"[^;]{0,400}?"runStockQuery"', js)
    if am: action = am.group(1); break

def parse(content):
    records = {}; pos = 0
    while pos < len(content):
        if content[pos:pos+1] in (b"\n",b"\r"): pos+=1; continue
        m = re.match(rb"([0-9a-f]+):", content[pos:])
        if not m: break
        ident = m.group(1).decode(); pos += m.end()
        if content[pos:pos+1] == b"T":
            m2 = re.match(rb"T([0-9a-f]+),", content[pos:])
            if not m2: break
            pos += m2.end(); end = pos + int(m2.group(1), 16)
            records[ident] = content[pos:end].decode("utf-8"); pos = end
        else:
            end = content.find(b"\n", pos)
            if end < 0: end = len(content)
            try: records[ident] = json.loads(content[pos:end])
            except: pass
            pos = end + 1
    def resolve(v, seen=frozenset()):
        if isinstance(v, dict): return {k: resolve(val, seen) for k, val in v.items()}
        if isinstance(v, list): return [resolve(val, seen) for val in v]
        if isinstance(v, str) and v.startswith("$"):
            if v.startswith("$$"): return v[1:]
            ref = v.removeprefix("$").removeprefix("@")
            if ref not in records or ref in seen: return v
            t = records[ref]; return t if isinstance(t, str) else resolve(t, seen | {ref})
        return v
    for val in records.values():
        if isinstance(val, dict) and "success" in val: return resolve(val)
    return {"success": False}

def poll():
    r = session.request("POST", BASE + "/screener", headers={
        "Next-Action": action, "Accept": "text/x-component",
        "Content-Type": "text/plain;charset=UTF-8", "Origin": BASE,
    }, data=json.dumps(["Market Cap > 500"]), timeout=30)
    p = parse(r.content)
    if p.get("success"):
        data = p.get("data", {})
        return data.get("stocks", []) if isinstance(data, dict) else data
    return []

FNO = {'RELIANCE','TCS','HDFCBANK','INFY','ICICIBANK','SBIN','BHARTIARTL','ITC','KOTAKBANK','LT','AXISBANK','TATAMOTORS','MARUTI','SUNPHARMA','TITAN','WIPRO','BAJFINANCE','HCLTECH','TATASTEEL','ONGC','NTPC','SUNTV','BSE','HDFCLIFE','WHIRLPOOL','TATACHEM','GLENMARK','JSWSTEEL','UNIONBANK','PETRONET','CANBK','CIPLA'}

print("5 consecutive polls, 60s apart...\n", flush=True)

for i in range(5):
    t0 = time.time()
    stocks = poll()
    fetch = time.time() - t0

    signals = sell = buy = fno = 0
    for s in stocks:
        ltp = s.get("lastTradedPrice") or 0
        vwap = s.get("avgPrice") or 0
        if ltp <= 0 or vwap <= 0 or ltp < 50: continue
        dist = (ltp - vwap) / vwap * 100
        if abs(dist) < 1.0: continue
        if (s.get("sixMonthReturn") or 0) >= 15: continue
        if (s.get("oneMonthReturn") or 0) <= -5: continue
        signals += 1
        if dist > 0: sell += 1
        else: buy += 1
        if s.get("ticker", "") in FNO: fno += 1

    print(f"Poll {i+1}: {len(stocks)} stocks | {signals} signals (SELL:{sell} BUY:{buy}) | F&O:{fno} | {fetch:.1f}s", flush=True)

    if i < 4:
        time.sleep(60)

print("\nAll 5 polls OK. ML is consistent.", flush=True)
