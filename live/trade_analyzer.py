"""Trade Analyzer V2 — clean rewrite."""
import http.server
import json
import os
import base64
import requests
from pathlib import Path
from datetime import datetime

PORT = 8901

def get_gemini_key():
    env_file = Path(__file__).parent.parent / '.env'
    if env_file.exists():
        for line in env_file.read_text().split('\n'):
            if line.startswith('GEMINI_API_KEY='):
                return line.split('=', 1)[1].strip()
    return os.environ.get('GEMINI_API_KEY', '')

HTML = r"""<!DOCTYPE html>
<html>
<head>
<title>Trade Analyzer</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e0e0e0;padding:16px}
h1{font-size:20px;color:#4ade80;margin-bottom:4px}
.sub{font-size:12px;color:#666;margin-bottom:16px}
label{font-size:13px;color:#888;display:block;margin-bottom:4px}
input,textarea{width:100%;background:#1a1a1a;color:#e0e0e0;border:1px solid #333;border-radius:8px;padding:12px;font-size:14px;margin-bottom:8px}
textarea{height:100px;resize:vertical}
button{width:100%;padding:14px;margin-top:8px;background:#4ade80;color:#0a0a0a;border:none;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer}
button:active{background:#22c55e}
.result{margin-top:16px;padding:16px;border-radius:10px;font-size:14px;line-height:1.6;display:none}
.trade{background:#052e16;color:#4ade80;border:1px solid #166534}
.skip{background:#1a1a1a;color:#f87171;border:1px solid #7f1d1d}
.loading{background:#1a1a1a;color:#fbbf24;border:1px solid #854d0e}
.tabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
.tab{flex:1;padding:10px;text-align:center;background:#1a1a1a;border:1px solid #333;border-radius:8px;cursor:pointer;font-size:13px;color:#888;min-width:70px}
.tab.on{background:#222;border-color:#4ade80;color:#4ade80}
.box{display:none}.box.on{display:block}
.nav{display:flex;gap:8px;margin-bottom:16px}
.nav a{flex:1;padding:8px;text-align:center;background:#1a1a1a;border:1px solid #333;border-radius:8px;color:#60a5fa;text-decoration:none;font-size:12px}
b{color:#fff}
</style>
</head>
<body>
<h1>Trade Analyzer</h1>
<div class="sub">Paste signal or upload screenshot — get instant analysis</div>
<div class="nav">
<a href="//:8900">Signals</a>
<a href="//:8899">Tokens</a>
</div>
<div class="tabs">
<div class="tab on" id="t1" onclick="show(1)">Quick</div>
<div class="tab" id="t2" onclick="show(2)">Detailed</div>
<div class="tab" id="t3" onclick="show(3)">Paste</div>
<div class="tab" id="t4" onclick="show(4)">Screenshot</div>
<div class="tab" id="t5" onclick="show(5)" style="background:#052e16;border-color:#4ade80">Univest</div>
</div>

<div class="box on" id="b1">
<label>Stock Symbol</label>
<input id="sym" placeholder="e.g. HDFCBANK, ONGC, TCS">
<button onclick="go({type:'quick',symbol:document.getElementById('sym').value.toUpperCase()})">Analyze</button>
</div>

<div class="box" id="b2">
<label>Stock Symbol</label>
<input id="d_sym" placeholder="NSE symbol">
<label>Direction</label>
<select id="d_dir" style="width:100%;padding:12px;background:#1a1a1a;color:#e0e0e0;border:1px solid #333;border-radius:8px;margin-bottom:8px"><option>BUY</option><option>SELL</option></select>
<label>Entry Price</label>
<input id="d_price" placeholder="e.g. 1500" type="number">
<label>Target</label>
<input id="d_target" placeholder="e.g. 1600" type="number">
<label>Stop Loss</label>
<input id="d_sl" placeholder="e.g. 1450" type="number">
<button onclick="go({type:'detailed',symbol:document.getElementById('d_sym').value.toUpperCase(),direction:document.getElementById('d_dir').value,price:document.getElementById('d_price').value,target:document.getElementById('d_target').value,sl:document.getElementById('d_sl').value})">Deep Analysis</button>
</div>

<div class="box" id="b3">
<label>Paste Multyfi / Advisory Signal</label>
<textarea id="paste" placeholder="Paste the full signal text here..."></textarea>
<button onclick="go({type:'paste',text:document.getElementById('paste').value})">Analyze Signal</button>
</div>

<div class="box" id="b4">
<label>Upload Screenshot</label>
<input type="file" id="imgfile" accept="image/*" style="padding:8px" onchange="document.getElementById('imgprev').src=URL.createObjectURL(this.files[0]);document.getElementById('imgprev').style.display='block';document.getElementById('res').style.display='none'">
<img id="imgprev" style="max-width:100%;border-radius:8px;margin:8px 0;display:none">
<label>Context (optional)</label>
<input id="imgctx" placeholder="e.g. Multyfi equity signal">
<button onclick="goImg()">Analyze Screenshot</button>
</div>

<div class="box" id="b5">
<label>Stock Symbols (comma separated — check multiple at once)</label>
<input id="u_sym" placeholder="e.g. SBIN, RELIANCE, HAL, TECHM">
<button onclick="goUnivestBulk()" style="background:#4ade80">Check All</button>
<div style="font-size:11px;color:#666;margin-top:8px">Score>=4 + RSI 40-75 + ADX not 35-50 | 85% WR on 780 trades | No Gemini | CE only</div>
</div>

<div class="result" id="res"></div>

<script>
function show(n){
for(var i=1;i<=5;i++){
document.getElementById('t'+i).className='tab'+(i==n?' on':'');
document.getElementById('b'+i).className='box'+(i==n?' on':'');
}}

async function go(data){
var el=document.getElementById('res');
el.style.display='block';el.className='result loading';el.innerHTML='Sending to Gemini...';
try{
var r=await fetch('/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
var d=await r.json();
var jid=d.job_id;
el.innerHTML='Gemini analyzing... searching Google... reading fundamentals...';
var poll=setInterval(async function(){
try{var r2=await fetch('/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:jid})});
var d2=await r2.json();
if(d2.analysis||d2.error){
clearInterval(poll);
var t=d2.analysis||d2.error||'No response';
t=t.replace(/\n/g,'<br>').replace(/\*\*(.*?)\*\*/g,'<b>$1</b>');
el.innerHTML=t;
el.className='result '+(d2.decision=='TRADE'?'trade':'skip');
}}catch(e){}},3000);
}catch(e){el.innerHTML='Error: '+e.message;el.className='result skip'}}

async function goImg(){
var f=document.getElementById('imgfile').files[0];
if(!f){alert('Select image first');return}
var el=document.getElementById('res');
el.style.display='block';el.className='result loading';el.innerHTML='Compressing image...';
var img=new Image();
img.onload=async function(){
var c=document.createElement('canvas');
var maxW=800;var scale=maxW/img.width;
if(scale>1)scale=1;
c.width=img.width*scale;c.height=img.height*scale;
c.getContext('2d').drawImage(img,0,0,c.width,c.height);
var b64=c.toDataURL('image/jpeg',0.7).split(',')[1];
el.innerHTML='Sending to Gemini...';
try{
var r=await fetch('/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'image',image_base64:b64,mime_type:f.type||'image/jpeg',context:document.getElementById('imgctx').value})});
var d=await r.json();
var jid=d.job_id;
el.innerHTML='Gemini reading screenshot... searching Google... analyzing...';
var poll=setInterval(async function(){
try{var r2=await fetch('/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:jid})});
var d2=await r2.json();
if(d2.analysis||d2.error){
clearInterval(poll);
var t=d2.analysis||d2.error||'No response';
t=t.replace(/\n/g,'<br>').replace(/\*\*(.*?)\*\*/g,'<b>$1</b>');
el.innerHTML=t;
el.className='result '+(d2.decision=='TRADE'?'trade':'skip');
}}catch(e){}},3000);
}catch(e){el.innerHTML='Error: '+e.message;el.className='result skip'}};
img.src=URL.createObjectURL(f)}

async function goUnivest(){
var sym=document.getElementById('u_sym').value.trim().toUpperCase();
if(!sym){alert('Enter stock symbol');return}
var el=document.getElementById('res');
el.style.display='block';el.className='result loading';el.innerHTML='Checking V3 indicators for '+sym+'... (downloading price data)';
try{
var r=await fetch('/univest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:sym})});
var d=await r.json();
var jid=d.job_id;
var poll=setInterval(async function(){
try{var r2=await fetch('/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:jid})});
var d=await r2.json();
if(d.status=='processing')return;
clearInterval(poll);
if(d.error){el.innerHTML=d.error;el.className='result skip';return}
var h='<div style="font-size:18px;margin-bottom:12px">';
h+=d.decision=='TAKE'?'<span style="color:#4ade80;font-size:24px">TAKE</span>':'<span style="color:#f87171;font-size:24px">SKIP</span>';
h+=' <b>'+d.sym+'</b> @ Rs '+d.price+'</div>';
h+='<div style="margin-bottom:12px"><b>Score: '+d.score+'/5</b></div>';
for(var k in d.checks){h+='<div style="padding:2px 0">'+(d.checks[k]?'<span style="color:#4ade80">+</span>':'<span style="color:#f87171">-</span>')+' '+k+'</div>'}
h+='<div style="margin-top:12px"><b>RSI:</b> '+d.rsi+'</div>';
h+='<div><b>ADX:</b> '+d.adx+'</div>';
if(d.reasons&&d.reasons.length>0){h+='<div style="margin-top:12px;color:#f87171">';for(var i=0;i<d.reasons.length;i++){h+='<div>Skip: '+d.reasons[i]+'</div>'}h+='</div>'}
if(d.decision=='TAKE'){h+='<div style="margin-top:12px;color:#4ade80;font-weight:600">All checks passed! Enter with 20% SL on premium.</div>'}
el.innerHTML=h;
el.className='result '+(d.decision=='TAKE'?'trade':'skip');
}catch(e){}},2000);
}catch(e){el.innerHTML='Error: '+e.message;el.className='result skip'}}
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        # Parse notification text: /notify?text=BUY+SBIN+CE+Rs+7200
        if self.path.startswith('/notify'):
            from urllib.parse import urlparse, parse_qs
            import re as re_mod
            params = parse_qs(urlparse(self.path).query)
            text = params.get('text', [''])[0]
            if not text:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'no text'}).encode())
                return
            # Extract stock symbol from notification text
            # Common patterns: "BUY SBIN CE", "SELL RELIANCE PE", "SBIN 7200 CE"
            text_upper = text.upper()
            # Skip if PE trade
            if ' PE ' in text_upper or text_upper.endswith(' PE') or 'PUT' in text_upper:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'decision': 'SKIP', 'reason': 'PE trade — skip all PE', 'text': text}).encode())
                return
            # Skip if index
            index_words = ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY', 'MIDCPNIFTY']
            for idx in index_words:
                if idx in text_upper:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'decision': 'SKIP', 'reason': 'Index trade — skip', 'text': text}).encode())
                    return
            # Extract stock name: look for known NSE symbols
            # Try to find a word that looks like a stock symbol (all caps, 3-15 chars)
            words = re_mod.findall(r'[A-Z][A-Z0-9&-]{2,14}', text_upper)
            skip_words = {'BUY','SELL','CALL','PUT','TARGET','STOP','LOSS','SL','TGT','ENTRY','EXIT','CE','PE','LOT','MIS','CNC','NRML','NSE','BSE','RS','INR','NEAR','ATM','OTM','ITM','OPTION','OPTIONS','PREMIUM','PRICE','ABOVE','BELOW','MARKET','ORDER','LIMIT','TODAY','NOW','URGENT','ALERT','SIGNAL','TRADE','TRADING','ADVISORY','RECOMMENDATION'}
            candidates = [w for w in words if w not in skip_words and len(w) >= 3]
            if not candidates:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'decision': 'UNKNOWN', 'reason': 'Could not extract stock name', 'text': text}).encode())
                return
            sym = candidates[0]
            from univest_checker import check_signal
            result = check_signal(sym)
            if 'checks' in result:
                result['checks'] = {k: bool(v) for k, v in result['checks'].items()}
            result['parsed_from'] = text
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
            return

        # Quick check endpoint: /check/SBIN or /check/SBIN,RELIANCE,HAL
        if self.path.startswith('/check/'):
            symbols = self.path[7:].upper().split(',')
            from univest_checker import check_signal
            results = []
            for sym in symbols:
                sym = sym.strip()
                if not sym:
                    continue
                r = check_signal(sym)
                if 'checks' in r:
                    r['checks'] = {k: bool(v) for k, v in r['checks'].items()}
                results.append(r)
            # Summary line for quick view
            takes = [r for r in results if r.get('decision') == 'TAKE']
            skips = [r for r in results if r.get('decision') == 'SKIP']
            summary = {
                'total': len(results),
                'take': [r['sym'] for r in takes],
                'skip': [r['sym'] for r in skips],
                'results': results,
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(summary, indent=2).encode())
            return

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        # Fix nav links with actual IP
        html = HTML.replace('//:8900', 'http://35.238.32.244:8900').replace('//:8899', 'http://35.238.32.244:8899')
        self.wfile.write(html.encode())

    # Store results by job ID
    _jobs = {}

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == '/univest':
            import threading, uuid
            job_id = str(uuid.uuid4())[:8]
            Handler._jobs[job_id] = {'status': 'processing'}

            def run_check():
                try:
                    from univest_checker import check_signal
                    result = check_signal(body.get('symbol', ''))
                    # Convert bool values for JSON
                    if 'checks' in result:
                        result['checks'] = {k: bool(v) for k, v in result['checks'].items()}
                    Handler._jobs[job_id] = result
                except Exception as e:
                    Handler._jobs[job_id] = {'error': str(e)}

            threading.Thread(target=run_check, daemon=True).start()

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'job_id': job_id}).encode())
            return

        if self.path == '/analyze':
            import threading, uuid
            job_id = str(uuid.uuid4())[:8]
            Handler._jobs[job_id] = {'status': 'processing'}

            def run():
                try:
                    result = self._analyze(body)
                    Handler._jobs[job_id] = result
                except Exception as e:
                    Handler._jobs[job_id] = {'error': str(e), 'decision': 'SKIP'}

            threading.Thread(target=run, daemon=True).start()

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'job_id': job_id}).encode())
            return

        elif self.path == '/result':
            job_id = body.get('job_id', '')
            result = Handler._jobs.get(job_id, {'status': 'not_found'})
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
            return

        self.send_response(404)
        self.end_headers()

    def _analyze(self, data):
        key = get_gemini_key()
        if not key:
            return {'error': 'No Gemini API key', 'decision': 'SKIP'}

        today = datetime.now().strftime('%Y-%m-%d')
        req_type = data.get('type', 'quick')

        if req_type == 'quick':
            sym = data.get('symbol', '')
            prompt = f"You are a stock analyst. Today is {today}.\n\nAnalyze {sym} for trading today. Search via Google for current price, any news today, market cap, PE ratio, yesterday's movement, 1-month return, 52-week position.\n\nGive verdict:\nSIGNAL STRENGTH: STRONG / MODERATE / WEAK / AVOID\nDirection: BUY / SELL\nProjected move: +X%% to +Y%%\nEntry, Target 1, Target 2, Stop Loss\nRisk-Reward ratio\nKey risk\n\nBe brutally honest."

        elif req_type == 'detailed':
            sym = data.get('symbol', '')
            prompt = f"You are a stock analyst. Today is {today}.\n\nSignal: {data.get('direction','BUY')} {sym} @ Rs {data.get('price','?')}\nTarget: Rs {data.get('target','?')} | SL: Rs {data.get('sl','?')}\n\nSearch Google for current price, news, market cap, PE, yesterday's move.\n\nGive verdict:\nSIGNAL STRENGTH: STRONG / MODERATE / WEAK / AVOID\nProjected move (your estimate)\nIs entry/target/SL reasonable?\nRisk-Reward ratio\nCatalyst: news-driven or technical?\nKey risk\nSuggested modifications\n\nBe brutally honest."

        elif req_type == 'paste':
            prompt = f"You are a stock analyst. Today is {today}.\n\nSignal received:\n---\n{data.get('text','')}\n---\n\nParse and analyze. Search Google for current price, news, fundamentals.\n\nGive verdict:\nSIGNAL STRENGTH: STRONG / MODERATE / WEAK / AVOID\nProjected move (your estimate)\nRisk-Reward\nCatalyst type\nKey risk\nModifications suggested\n\nBe brutally honest."

        elif req_type == 'image':
            prompt = f"You are a stock analyst. Today is {today}.\n\n{data.get('context','This is a trade signal screenshot.')}\n\nRead the signal from the image. Search Google for current price, news, fundamentals.\n\nGive verdict:\nSIGNAL STRENGTH: STRONG / MODERATE / WEAK / AVOID\nProjected move (your estimate)\nRisk-Reward\nCatalyst type\nKey risk\nModifications suggested\n\nBe brutally honest."

            # Image call
            url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={key}'
            try:
                resp = requests.post(url, json={
                    'contents': [{'parts': [
                        {'inline_data': {'mime_type': data.get('mime_type', 'image/jpeg'), 'data': data.get('image_base64', '')}},
                        {'text': prompt}
                    ]}],
                    'tools': [{'google_search': {}}],
                    'generationConfig': {'temperature': 0, 'maxOutputTokens': 4096},
                }, timeout=120)
                if resp.status_code == 200:
                    text = ''.join(p.get('text', '') for p in resp.json()['candidates'][0]['content']['parts'])
                    dec = 'TRADE' if 'STRONG' in text.upper() or 'MODERATE' in text.upper() else 'SKIP'
                    return {'analysis': text, 'decision': dec}
                return {'error': f'Gemini {resp.status_code}', 'decision': 'SKIP'}
            except Exception as e:
                return {'error': str(e), 'decision': 'SKIP'}
        else:
            return {'error': 'Unknown type', 'decision': 'SKIP'}

        # Text-only call
        url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={key}'
        try:
            resp = requests.post(url, json={
                'contents': [{'parts': [{'text': prompt}]}],
                'tools': [{'google_search': {}}],
                'generationConfig': {'temperature': 0, 'maxOutputTokens': 4096},
            }, timeout=120)
            if resp.status_code == 200:
                text = ''.join(p.get('text', '') for p in resp.json()['candidates'][0]['content']['parts'])
                dec = 'TRADE' if 'STRONG' in text.upper() or 'MODERATE' in text.upper() else 'SKIP'
                return {'analysis': text, 'decision': dec}
            return {'error': f'Gemini {resp.status_code}', 'decision': 'SKIP'}
        except Exception as e:
            return {'error': str(e), 'decision': 'SKIP'}


if __name__ == '__main__':
    print(f'Trade Analyzer on port {PORT}')
    server = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    server.serve_forever()
