"""
Univest Notifier — Android app (PWA)
Install on phone, grant notification access, auto-checks Univest signals.

How it works:
1. Open http://35.238.32.244:8902 on phone
2. Tap "Install" when prompted (or Add to Home Screen)
3. Paste Univest notification text → instant TAKE/SKIP
4. Keeps history of all checks
"""
import http.server
import json
import os
import re
import requests as req
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs

PORT = 8902
PAPER_MODE = True  # Set to False for live trading

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<title>Univest Filter</title>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="theme-color" content="#0a0a0a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.json">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e0e0e0;padding:12px;max-width:500px;margin:0 auto}
h1{font-size:22px;color:#4ade80;margin-bottom:2px}
.sub{font-size:12px;color:#666;margin-bottom:16px}
.stats{display:flex;gap:8px;margin-bottom:16px}
.stat{flex:1;background:#1a1a1a;border-radius:10px;padding:10px;text-align:center}
.stat .num{font-size:24px;font-weight:700}
.stat .label{font-size:10px;color:#888;margin-top:2px}
.input-area{position:relative;margin-bottom:12px}
textarea{width:100%;background:#1a1a1a;color:#e0e0e0;border:1px solid #333;border-radius:12px;padding:14px;font-size:15px;height:80px;resize:none}
textarea:focus{border-color:#4ade80;outline:none}
button{width:100%;padding:14px;background:#4ade80;color:#0a0a0a;border:none;border-radius:12px;font-size:16px;font-weight:700;cursor:pointer;letter-spacing:0.5px}
button:active{transform:scale(0.98);background:#22c55e}
.result-card{margin-top:16px;padding:16px;border-radius:14px;animation:fadeIn 0.3s}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.take{background:#052e16;border:2px solid #4ade80}
.skip{background:#1a0a0a;border:2px solid #f87171}
.loading{background:#1a1a0a;border:2px solid #fbbf24}
.decision{font-size:28px;font-weight:800;margin-bottom:8px}
.decision.take-text{color:#4ade80}
.decision.skip-text{color:#f87171}
.stock-name{font-size:20px;color:#fff;margin-bottom:12px}
.indicator{display:flex;align-items:center;padding:6px 0;font-size:14px}
.indicator .dot{width:20px;height:20px;border-radius:50%;margin-right:10px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700}
.dot.pass{background:#052e16;color:#4ade80;border:1px solid #4ade80}
.dot.fail{background:#1a0a0a;color:#f87171;border:1px solid #f87171}
.metric{display:flex;gap:8px;margin-top:12px}
.metric-box{flex:1;background:#111;border-radius:8px;padding:8px;text-align:center}
.metric-box .val{font-size:18px;font-weight:700;color:#fff}
.metric-box .lbl{font-size:10px;color:#888}
.reasons{margin-top:12px;padding:10px;background:#1a0a0a;border-radius:8px}
.reasons div{color:#f87171;font-size:13px;padding:3px 0}
.sl-reminder{margin-top:12px;padding:10px;background:#052e16;border-radius:8px;color:#4ade80;font-size:13px;font-weight:600;text-align:center}
.history{margin-top:24px}
.history h2{font-size:14px;color:#888;margin-bottom:8px}
.hist-item{display:flex;justify-content:space-between;padding:10px;background:#1a1a1a;border-radius:8px;margin-bottom:6px;font-size:13px}
.hist-item .sym{font-weight:700}
.hist-take{color:#4ade80}
.hist-skip{color:#f87171}
.time{color:#555;font-size:11px}
.or-line{text-align:center;color:#444;font-size:12px;margin:8px 0}
.quick-input{display:flex;gap:8px;margin-bottom:12px}
.quick-input input{flex:1;background:#1a1a1a;color:#e0e0e0;border:1px solid #333;border-radius:12px;padding:14px;font-size:15px}
.quick-input button{width:auto;padding:14px 20px;border-radius:12px}
</style>
</head>
<body>

<h1>Univest Filter</h1>
<div class="sub">V3 Score + RSI + ADX | 85% WR | Instant check</div>

<div class="stats">
<div class="stat"><div class="num" id="s_total">0</div><div class="label">Checked</div></div>
<div class="stat"><div class="num" style="color:#4ade80" id="s_take">0</div><div class="label">TAKE</div></div>
<div class="stat"><div class="num" style="color:#f87171" id="s_skip">0</div><div class="label">SKIP</div></div>
</div>

<div class="quick-input">
<input id="qsym" placeholder="Stock name" autocapitalize="characters">
<button onclick="checkQuick()">Check</button>
</div>

<div class="or-line">or paste full notification</div>

<textarea id="notif" placeholder="Paste Univest notification text here...&#10;e.g. BUY SBIN CE Rs 7200 SL 6800 TGT 7600"></textarea>
<button onclick="checkNotif()">Check Signal</button>

<div id="result"></div>

<div class="history" id="hist_section" style="display:none">
<h2>Recent Checks</h2>
<div id="hist"></div>
</div>

<script>
var history = JSON.parse(localStorage.getItem('uvhist') || '[]');
var stats = JSON.parse(localStorage.getItem('uvstats') || '{"total":0,"take":0,"skip":0}');
updateStats();
renderHistory();

function updateStats(){
document.getElementById('s_total').textContent=stats.total;
document.getElementById('s_take').textContent=stats.take;
document.getElementById('s_skip').textContent=stats.skip;
}

function checkQuick(){
var sym=document.getElementById('qsym').value.trim().toUpperCase();
if(!sym)return;
doCheck('/check/'+sym);
}

function checkNotif(){
var text=document.getElementById('notif').value.trim();
if(!text)return;
doCheck('/notify?text='+encodeURIComponent(text));
}

async function doCheck(url){
var el=document.getElementById('result');
el.innerHTML='<div class="result-card loading"><div style="text-align:center;padding:20px;font-size:16px;color:#fbbf24">Checking indicators...</div></div>';
try{
var r=await fetch(url);
var d=await r.json();
if(d.results)d=d.results[0];
showResult(d);
// Save history
var item={sym:d.sym||'?',decision:d.decision||'?',score:d.score||0,time:new Date().toLocaleTimeString()};
history.unshift(item);
if(history.length>20)history=history.slice(0,20);
localStorage.setItem('uvhist',JSON.stringify(history));
stats.total++;
if(d.decision=='TAKE')stats.take++;else stats.skip++;
localStorage.setItem('uvstats',JSON.stringify(stats));
updateStats();
renderHistory();
}catch(e){el.innerHTML='<div class="result-card skip"><div style="color:#f87171">Error: '+e.message+'</div></div>'}
}

function showResult(d){
var el=document.getElementById('result');
if(d.error){el.innerHTML='<div class="result-card skip"><div class="decision skip-text">ERROR</div><div>'+d.error+'</div></div>';return}
if(d.reason){el.innerHTML='<div class="result-card skip"><div class="decision skip-text">SKIP</div><div style="color:#f87171;font-size:15px">'+d.reason+'</div></div>';return}
var isTake=d.decision=='TAKE';
var h='<div class="result-card '+(isTake?'take':'skip')+'">';
h+='<div class="decision '+(isTake?'take-text':'skip-text')+'">'+(isTake?'TAKE':'SKIP')+'</div>';
h+='<div class="stock-name">'+d.sym+' @ Rs '+d.price+'</div>';
if(d.checks){for(var k in d.checks){h+='<div class="indicator"><div class="dot '+(d.checks[k]?'pass':'fail')+'">'+(d.checks[k]?'+':'-')+'</div>'+k+'</div>'}}
h+='<div class="metric">';
h+='<div class="metric-box"><div class="val">'+d.score+'/5</div><div class="lbl">Score</div></div>';
h+='<div class="metric-box"><div class="val">'+d.rsi+'</div><div class="lbl">RSI</div></div>';
h+='<div class="metric-box"><div class="val">'+d.adx+'</div><div class="lbl">ADX</div></div>';
h+='</div>';
if(d.reasons&&d.reasons.length>0){h+='<div class="reasons">';for(var i=0;i<d.reasons.length;i++){h+='<div>'+d.reasons[i]+'</div>'}h+='</div>'}
if(isTake){h+='<div class="sl-reminder">Enter with 20% SL on premium</div>'}
h+='</div>';
el.innerHTML=h;
}

function renderHistory(){
if(history.length==0){document.getElementById('hist_section').style.display='none';return}
document.getElementById('hist_section').style.display='block';
var h='';
for(var i=0;i<history.length;i++){
var it=history[i];
h+='<div class="hist-item"><div><span class="sym">'+it.sym+'</span> <span class="'+(it.decision=='TAKE'?'hist-take':'hist-skip')+'">'+it.decision+'</span> <span style="color:#555">'+it.score+'/5</span></div><div class="time">'+it.time+'</div></div>';
}
document.getElementById('hist').innerHTML=h;
}
</script>
</body>
</html>"""

MANIFEST = json.dumps({
    "name": "Univest Filter",
    "short_name": "UV Filter",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0a0a0a",
    "theme_color": "#0a0a0a",
    "icons": [{"src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📊</text></svg>", "sizes": "any", "type": "image/svg+xml"}]
})


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        if self.path == '/manifest.json':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(MANIFEST.encode())
            return

        if self.path.startswith('/notify'):
            params = parse_qs(urlparse(self.path).query)
            text = params.get('text', [''])[0]
            if not text:
                self._json({'error': 'no text'})
                return
            text_upper = text.upper()
            if ' PE ' in text_upper or text_upper.endswith(' PE') or 'PUT' in text_upper:
                self._json({'decision': 'SKIP', 'reason': 'PE trade - skip all PE'})
                return
            for idx in ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY', 'MIDCPNIFTY']:
                if idx in text_upper:
                    self._json({'decision': 'SKIP', 'reason': 'Index trade - skip'})
                    return
            words = re.findall(r'[A-Z][A-Z0-9&-]{2,14}', text_upper)
            skip_words = {'BUY','SELL','CALL','PUT','TARGET','STOP','LOSS','SL','TGT','ENTRY','EXIT','CE','PE','LOT','MIS','CNC','NRML','NSE','BSE','RS','INR','NEAR','ATM','OTM','ITM','OPTION','OPTIONS','PREMIUM','PRICE','ABOVE','BELOW','MARKET','ORDER','LIMIT','TODAY','NOW','URGENT','ALERT','SIGNAL','TRADE','TRADING','ADVISORY','RECOMMENDATION','UNIVEST','MULTYFI','PROFIT','RETURNS'}
            candidates = [w for w in words if w not in skip_words and len(w) >= 3]
            if not candidates:
                self._json({'decision': 'UNKNOWN', 'reason': 'Could not extract stock name'})
                return
            self._do_check(candidates[0])
            return

        if self.path.startswith('/check/'):
            sym = self.path[7:].strip().upper()
            self._do_check(sym)
            return

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(HTML.encode())

    def _do_check(self, sym, auto_trade=False):
        try:
            from univest_checker import check_signal
            result = check_signal(sym)
            if 'checks' in result:
                result['checks'] = {k: bool(v) for k, v in result['checks'].items()}
            # Log to file
            log_file = Path(__file__).parent / 'logs' / 'univest_checks.log'
            log_file.parent.mkdir(exist_ok=True)
            with open(log_file, 'a') as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {sym} | {result.get('decision','?')} | Score={result.get('score','?')} RSI={result.get('rsi','?')} ADX={result.get('adx','?')}\n")

            # Auto-trade if TAKE and auto_trade enabled
            if result.get('decision') == 'TAKE' and auto_trade:
                trade_result = self._auto_execute(sym)
                result['trade'] = trade_result

            self._json(result)
        except Exception as e:
            self._json({'error': str(e)})

    def _auto_execute(self, sym):
        """Auto-buy ATM CE option for the stock."""
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from indmoney_client import get_option_chain, place_fno_order, lookup_scrip

            # Ask Master for allocation
            master_trade_id = None
            try:
                r = req.post('http://localhost:8904/request', json={
                    'strategy': 'univest_options', 'symbol': sym, 'conviction': 8, 'projection': 5.0
                }, timeout=5)
                if r.status_code == 200:
                    master_resp = r.json()
                    if not master_resp.get('approved'):
                        log_file = Path(__file__).parent / 'logs' / 'univest_trades.log'
                        log_file.parent.mkdir(exist_ok=True)
                        with open(log_file, 'a') as f:
                            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | MASTER REJECTED | {sym} | {master_resp.get('reason','?')}\n")
                        return {'status': 'master_rejected', 'reason': master_resp.get('reason', '?')}
                    master_trade_id = master_resp.get('trade_id')
            except:
                pass  # Master unavailable, proceed without

            # Get ATM CE option
            chain = get_option_chain(sym)
            if not chain:
                return {'status': 'no_option_chain', 'fallback': 'MIS'}

            # Find ATM CE
            spot = chain.get('spot_price', 0)
            options = chain.get('options', [])
            atm_ce = None
            min_diff = float('inf')
            for opt in options:
                if opt.get('option_type') == 'CE':
                    diff = abs(opt.get('strike_price', 0) - spot)
                    if diff < min_diff:
                        min_diff = diff
                        atm_ce = opt

            if atm_ce:
                sec_id = atm_ce.get('security_id')
                lot_size = atm_ce.get('lot_size', 1)
                ltp = atm_ce.get('ltp', 0)

                # 20% SL on premium
                sl_price = round(ltp * 0.80, 2)  # exit if drops 20%

                # Paper mode check
                paper_mode = PAPER_MODE
                if paper_mode:
                    log_file = Path(__file__).parent / 'logs' / 'univest_trades.log'
                    log_file.parent.mkdir(exist_ok=True)
                    with open(log_file, 'a') as f:
                        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | PAPER | BUY CE {sym} | strike={atm_ce.get('strike_price')} | lot={lot_size} | ltp={ltp} | sl={sl_price}\n")
                    return {'status': 'paper_trade', 'strike': atm_ce.get('strike_price'), 'lot': lot_size, 'ltp': ltp, 'sl': sl_price}

                # LIVE: place order with SL
                result = place_fno_order(sym, lot_size, 'BUY', sec_id)
                log_file = Path(__file__).parent / 'logs' / 'univest_trades.log'
                log_file.parent.mkdir(exist_ok=True)
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | LIVE | BUY CE {sym} | strike={atm_ce.get('strike_price')} | lot={lot_size} | ltp={ltp} | sl={sl_price} | order={result}\n")

                # Monitor for 20% SL in background thread
                import threading
                def monitor_sl():
                    import time as tm
                    entry_ltp = ltp
                    sl_level = sl_price
                    while True:
                        tm.sleep(30)  # check every 30 sec
                        try:
                            from indmoney_client import get_option_chain
                            chain = get_option_chain(sym)
                            if not chain:
                                continue
                            for opt in chain.get('options', chain if isinstance(chain, list) else []):
                                if str(opt.get('security_id')) == str(sec_id):
                                    curr_ltp = opt.get('ltp', 0)
                                    if curr_ltp <= sl_level:
                                        # SL HIT — exit
                                        exit_result = place_fno_order(sym, lot_size, 'SELL', sec_id)
                                        with open(log_file, 'a') as f:
                                            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | SL HIT | SELL CE {sym} | entry={entry_ltp} | exit={curr_ltp} | sl={sl_level} | order={exit_result}\n")
                                        return
                                    break
                        except:
                            pass
                        # Exit by 3:10 PM
                        now = datetime.now()
                        if now.hour >= 15 and now.minute >= 10:
                            try:
                                exit_result = place_fno_order(sym, lot_size, 'SELL', sec_id)
                                with open(log_file, 'a') as f:
                                    f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | EOD EXIT | SELL CE {sym} | order={exit_result}\n")
                            except:
                                pass
                            return

                threading.Thread(target=monitor_sl, daemon=True).start()
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | SL MONITOR STARTED | {sym} | sl={sl_price} | check every 30s\n")

                return {'status': 'order_placed', 'strike': atm_ce.get('strike_price'), 'lot': lot_size, 'ltp': ltp, 'sl': sl_price, 'order': str(result)}
            else:
                return {'status': 'no_atm_ce_found'}
        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode() if length else ''

        if self.path == '/notify' or self.path.startswith('/notify'):
            # Accept POST with JSON body or form data
            text = ''
            try:
                data = json.loads(body)
                # Different apps send different formats
                # Prefer body over title (title is usually "Univest")
                text = data.get('text', '') or data.get('body', '') or data.get('message', '') or data.get('notification', {}).get('body', '') or data.get('notification', {}).get('text', '') or data.get('title', '') or str(data)
            except:
                text = body

            if not text.strip():
                self._json({'error': 'no text'})
                return

            text_upper = text.upper()
            # Skip PE
            if ' PE ' in text_upper or text_upper.endswith(' PE') or 'PUT' in text_upper:
                self._json({'decision': 'SKIP', 'reason': 'PE trade - skip'})
                return
            # Skip index
            for idx in ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY', 'MIDCPNIFTY']:
                if idx in text_upper:
                    self._json({'decision': 'SKIP', 'reason': 'Index trade - skip'})
                    return
            # Extract stock name
            words = re.findall(r'[A-Z][A-Z0-9&-]{2,14}', text_upper)
            skip_words = {'BUY','SELL','CALL','PUT','TARGET','STOP','LOSS','SL','TGT','ENTRY','EXIT','CE','PE','LOT','MIS','CNC','NRML','NSE','BSE','RS','INR','NEAR','ATM','OTM','ITM','OPTION','OPTIONS','PREMIUM','PRICE','ABOVE','BELOW','MARKET','ORDER','LIMIT','TODAY','NOW','URGENT','ALERT','SIGNAL','TRADE','TRADING','ADVISORY','RECOMMENDATION','UNIVEST','MULTYFI','PROFIT','RETURNS'}
            candidates = [w for w in words if w not in skip_words and len(w) >= 3]
            if not candidates:
                self._json({'decision': 'UNKNOWN', 'reason': 'Could not extract stock name', 'raw': text[:100]})
                return
            # Auto-trade on TAKE — only during market hours
            hour = datetime.now().hour
            minute = datetime.now().minute
            market_open = (hour == 9 and minute >= 15) or (hour >= 10 and hour < 15) or (hour == 15 and minute <= 10)
            self._do_check(candidates[0], auto_trade=market_open)
            return

        if self.path == '/check':
            try:
                data = json.loads(body)
                sym = data.get('symbol', '').upper()
                if sym:
                    self._do_check(sym)
                    return
            except:
                pass
            self._json({'error': 'invalid request'})
            return

        self._json({'error': 'unknown endpoint'})

    def _json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


if __name__ == '__main__':
    print(f'Univest Filter on port {PORT}')
    server = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    server.serve_forever()
