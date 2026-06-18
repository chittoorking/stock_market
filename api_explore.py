"""Explore all INDstocks API capabilities with live market."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)

from live.indmoney_client import headers, SCRIP_CODES
import requests, json

h = headers()

# 1. Proper market depth endpoint
print("=== MARKET DEPTH (proper endpoint) ===", flush=True)
scrips = ",".join([SCRIP_CODES[s] for s in ["RELIANCE","HDFCBANK","SBIN"]])
r = requests.get(f"https://api.indstocks.com/market/quotes/mkt?scrip-codes={scrips}", headers=h, timeout=10)
print(f"Status: {r.status_code}", flush=True)
if r.status_code == 200:
    data = r.json()
    for key, val in data.get("data", {}).items():
        md = val.get("market_depth", {})
        agg = md.get("aggregate", {})
        depth = md.get("depth", [])
        print(f"\n{key}:", flush=True)
        print(f"  Aggregate: {json.dumps(agg)}", flush=True)
        if depth:
            print(f"  Top 3 levels:", flush=True)
            for i, lvl in enumerate(depth[:3]):
                print(f"    L{i+1}: {json.dumps(lvl)}", flush=True)
else:
    print(f"  Response: {r.text[:200]}", flush=True)

# 2. Margin calculation
print("\n=== MARGIN CALCULATION ===", flush=True)
r = requests.get(
    "https://api.indstocks.com/margin?segment=EQUITY&exchange=NSE&securityID=2885&txnType=BUY&quantity=10&price=1330&product=INTRADAY",
    headers=h, timeout=10)
print(f"Status: {r.status_code}", flush=True)
print(json.dumps(r.json(), indent=2)[:500], flush=True)

# 3. Full quote with all fields
print("\n=== FULL QUOTE (all fields) ===", flush=True)
r = requests.get(f"https://api.indstocks.com/market/quotes/full?scrip-codes={SCRIP_CODES['RELIANCE']}", headers=h, timeout=10)
print(f"Status: {r.status_code}", flush=True)
if r.status_code == 200:
    print(json.dumps(r.json(), indent=2)[:800], flush=True)

# 4. WebSocket quote mode (not just LTP)
print("\n=== WEBSOCKET MODES AVAILABLE ===", flush=True)
print("ltp: just price", flush=True)
print("quote: price + depth (need to test)", flush=True)

# 5. Smart order format
print("\n=== SMART ORDER WITH SL+TARGET ===", flush=True)
smart = {
    "txn_type": "BUY", "exchange": "NSE", "segment": "EQUITY",
    "product": "INTRADAY", "order_type": "LIMIT", "validity": "DAY",
    "security_id": "2885", "qty": 1, "limit_price": 1300,
    "sl_trigger_price": 1295, "sl_limit_price": 1294,
    "tgt_trigger_price": 1340, "tgt_limit_price": 1340,
    "algo_id": "99999",
}
print(f"Payload: {json.dumps(smart, indent=2)}", flush=True)
print("(NOT placed - showing format only)", flush=True)

# 6. Check what WS quote mode returns vs LTP
print("\n=== TESTING WS QUOTE MODE ===", flush=True)
import websocket, threading, time

results = {}
def on_msg(ws, msg):
    data = json.loads(msg)
    mode = data.get('mode', '?')
    results[mode] = data
    print(f"  WS: {json.dumps(data)[:300]}", flush=True)

def on_open(ws):
    # Subscribe in quote mode
    ws.send(json.dumps({"action": "subscribe", "mode": "quote", "instruments": ["NSE:2885"]}))
    print("  Subscribed in QUOTE mode", flush=True)

def on_err(ws, err):
    print(f"  WS Error: {err}", flush=True)

token = open('/home/ai18developer/trading-bot/data/indmoney_token.txt').read().strip()
ws = websocket.WebSocketApp(
    "wss://ws-prices.indstocks.com/api/v1/ws/prices",
    header={"Authorization": token},
    on_open=on_open, on_message=on_msg, on_error=on_err)

t = threading.Thread(target=lambda: ws.run_forever(ping_interval=10), daemon=True)
t.start()
time.sleep(5)
ws.close()

if not results:
    print("  No data received in quote mode", flush=True)

print("\nDone", flush=True)
