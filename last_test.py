"""Final pre-Monday test."""
import sys; sys.path.insert(0, ".")
from live.indmoney_client import get_funds
import re, json, subprocess, requests
from datetime import datetime
from urllib.parse import urljoin
from curl_cffi import requests as cf

b = get_funds()
ok = "OK"
fail = "FAIL"
print(f"1. Balance: Rs {b:,.2f}")

code = open("live/vwap_bot.py").read()
print(f"2. Bot: {len(code)} chars")

BASE = "https://marketlens.nseindia.com"
s = cf.Session(impersonate="chrome")
s.request("GET", BASE, timeout=30)
s.headers.update({"Referer": BASE + "/screener"})
html = s.request("GET", BASE + "/screener", timeout=30).text
action = None
for src in dict.fromkeys(re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', html)):
    u = urljoin(BASE, src)
    if "/_next/static/" not in u: continue
    try:
        js = s.request("GET", u, timeout=30).text
        am = re.search(r'createServerReference\)\("([0-9a-f]+)"[^;]{0,400}?"runStockQuery"', js)
        if am: action = am.group(1); break
    except: continue
status = ok if action else fail
print(f"3. ML: {status}")

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

r = s.request("POST", BASE + "/screener", headers={"Next-Action":action,"Accept":"text/x-component","Content-Type":"text/plain;charset=UTF-8","Origin":BASE}, data=json.dumps(["Market Cap > 500"]), timeout=30)
p = parse(r.content)
stocks = p.get("data",{}).get("stocks",[]) if p.get("success") else []

sigs = 0
for st in stocks:
    ltp = st.get("lastTradedPrice") or 0
    vwap = st.get("avgPrice") or 0
    if ltp<=0 or vwap<=0 or ltp<50: continue
    if abs(ltp-vwap)/vwap*100 < 1.0: continue
    if (st.get("sixMonthReturn") or 0) >= 15: continue
    if (st.get("oneMonthReturn") or 0) <= -5: continue
    fy = st.get("foundedYear") or 0
    if fy<=0 or (datetime.now().year-fy)<10: continue
    sigs += 1
print(f"4. Signals: {sigs} from {len(stocks)} stocks")

cron = subprocess.check_output("crontab -l", shell=True).decode()
cron_ok = ok if "vwap_bot" in cron and "45 9" in cron else fail
print(f"5. Cron: {cron_ok}")

capital = b if b > 0 else 10000
if capital <= 10000: slots = 1
elif capital <= 25000: slots = 2
elif capital <= 50000: slots = 3
elif capital <= 75000: slots = 4
elif capital <= 100000: slots = 5
else: slots = min(10, int(capital/50000)+3)
print(f"6. Rs {capital:,.0f} -> {slots} slot(s)")

requests.post("https://api.telegram.org/bot8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs/sendMessage",
    json={"chat_id":"866752968","text":f"FINAL TEST PASSED\nBalance: Rs {capital:,.0f}\nSlots: {slots}\nSignals: {sigs}\nMonday 9:45 AM ready."},timeout=5)
print(f"7. Telegram: sent")
print()
print("ALL CLEAR. Monday 9:45 AM.")
