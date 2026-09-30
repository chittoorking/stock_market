"""
Multyfi Equity Intraday Auto-Trader
Port 8905 | SL<3% + Close>VWAP filter | Fund Manager integration

How it works:
1. Receives Multyfi/IndStocks equity signals via POST /notify
2. Filters: SL distance < 3% AND Close > VWAP
3. If TAKE: requests capital from Fund Manager, places MIS order
4. Monitors with Multyfi's SL (already tight at <3%)
5. Exits all positions by 3:10 PM
"""
import http.server
import json
import logging
import os
import re
import sys
import threading
import time as tm
import warnings
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pandas as pd
import requests
import yfinance as yf

# Optional INDmoney client — only needed for LIVE mode
try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from live import indmoney_client as api
    INDMONEY_AVAILABLE = True
    print(f'INDmoney client loaded: {len(api.SCRIP_CODES)} scrips')
except Exception as e:
    INDMONEY_AVAILABLE = False
    print(f'INDmoney client FAILED: {e}')
    import traceback
    traceback.print_exc()

warnings.filterwarnings('ignore')

PORT = 8905
PAPER_MODE = False
MASTER_URL = 'http://localhost:8904'
LOG_DIR = Path(__file__).parent / 'logs'
LOG_FILE = LOG_DIR / 'multyfi_equity_trades.log'

# Active trades being monitored
active_trades = {}  # trade_id -> {symbol, entry, sl, qty, master_trade_id, ...}
trade_history = []  # [{symbol, entry, exit, sl, pnl, decision, time, ...}]
stats = {'signals': 0, 'take': 0, 'skip': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0.0}
LOCK = threading.Lock()

# Setup logging
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('multyfi_equity')


# ─── Filters ────────────────────────────────────────────────────────────────

def check_sl_distance(entry, sl):
    """SL must be < 3% of entry price."""
    sl_pct = abs(entry - sl) / entry * 100
    return sl_pct, sl_pct < 3.0


def check_vwap(sym):
    """Close > VWAP (20-period rolling mean as proxy)."""
    ticker = sym.upper() + '.NS'
    try:
        data = yf.download(ticker, period='3mo', progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        if len(data) < 5:
            return None, None, False
        close = float(data['Close'].iloc[-1])
        vwap = float(data['Close'].rolling(20).mean().iloc[-1])
        return close, vwap, close > vwap
    except Exception as e:
        log.warning(f'VWAP check failed for {sym}: {e}')
        return None, None, False


def evaluate_signal(symbol, entry, sl, target=None):
    """Run both filters. Returns (decision, details_dict)."""
    sl_pct, sl_ok = check_sl_distance(entry, sl)
    close, vwap, vwap_ok = check_vwap(symbol)

    checks = {
        'SL < 3%': sl_ok,
        'Close > VWAP': vwap_ok,
    }
    reasons = []
    if not sl_ok:
        reasons.append(f'SL too wide: {sl_pct:.1f}% (need <3%)')
    if not vwap_ok:
        if close is None:
            reasons.append('Could not fetch VWAP data')
        else:
            reasons.append(f'Close {close:.1f} < VWAP {vwap:.1f}')

    decision = 'TAKE' if (sl_ok and vwap_ok) else 'SKIP'
    conviction = 9 if decision == 'TAKE' else 0
    target_pct = ((target - entry) / entry * 100) if target else 0

    details = {
        'sym': symbol,
        'decision': decision,
        'entry': entry,
        'sl': sl,
        'sl_pct': round(sl_pct, 2),
        'target': target,
        'target_pct': round(target_pct, 2) if target else None,
        'close': round(close, 2) if close else None,
        'vwap': round(vwap, 2) if vwap else None,
        'checks': {k: bool(v) for k, v in checks.items()},
        'reasons': reasons,
        'conviction': conviction,
    }
    return decision, details


# ─── Fund Manager ────────────────────────────────────────────────────────────

def request_trade(symbol, conviction):
    """Ask Fund Manager for capital allocation."""
    try:
        resp = requests.post(f'{MASTER_URL}/request', json={
            'strategy': 'multyfi_equity',
            'symbol': symbol,
            'conviction': conviction,
            'projection': 3.0,
        }, timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log.warning(f'Fund Manager unavailable: {e} -- using fallback')
    return {'approved': True, 'capital': 10000, 'trade_id': f'mfeq_local_{int(tm.time())}'}


def report_exit(trade_id, pnl):
    """Report trade exit to Fund Manager."""
    try:
        resp = requests.post(f'{MASTER_URL}/report', json={
            'trade_id': trade_id,
            'pnl': pnl,
        }, timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log.warning(f'Fund Manager report failed: {e}')
    return None


# ─── Trade Execution ─────────────────────────────────────────────────────────

def execute_trade(symbol, entry, sl, target, details):
    """Execute a BUY MIS order (paper or live)."""
    # Request capital from Fund Manager
    master_resp = request_trade(symbol, details.get('conviction', 9))
    if not master_resp.get('approved', True):
        reason = master_resp.get('reason', 'unknown')
        log.info(f'MASTER REJECTED | {symbol} | {reason}')
        return {'status': 'master_rejected', 'reason': reason}

    capital = master_resp.get('capital', 10000)
    trade_id = master_resp.get('trade_id', f'mfeq_{int(tm.time())}')
    qty = max(1, int(capital / entry))

    trade = {
        'trade_id': trade_id,
        'symbol': symbol,
        'entry': entry,
        'sl': sl,
        'target': target,
        'qty': qty,
        'capital': capital,
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    if PAPER_MODE:
        log.info(f'PAPER BUY | {symbol} | qty={qty} | entry={entry} | sl={sl} | target={target} | capital={capital}')
        with LOCK:
            active_trades[trade_id] = trade
        # Start SL monitor
        threading.Thread(target=monitor_trade, args=(trade_id,), daemon=True).start()
        return {'status': 'paper_trade', **trade}

    # LIVE: place order via INDmoney
    log.info(f'LIVE BUY | {symbol} | qty={qty} | entry={entry} | sl={sl} | target={target} | capital={capital}')
    if INDMONEY_AVAILABLE:
        try:
            scrip_code = api.SCRIP_CODES.get(symbol)
            if scrip_code:
                sec_id = scrip_code
                result = api.place_order(symbol, qty, 'BUY', price=0, order_type='MARKET', product='INTRADAY')
                trade['order_result'] = str(result)
                log.info(f'ORDER PLACED | {symbol} | {result}')
        except Exception as e:
            log.error(f'ORDER FAILED | {symbol} | {e}')
            trade['order_result'] = f'error: {e}'
    else:
        log.error(f'ORDER FAILED | {symbol} | indmoney_client not available')
        trade['order_result'] = 'error: indmoney_client not available'

    with LOCK:
        active_trades[trade_id] = trade
    threading.Thread(target=monitor_trade, args=(trade_id,), daemon=True).start()
    return {'status': 'order_placed', **trade}


def monitor_trade(trade_id):
    """Monitor trade for SL hit or 3:10 PM exit."""
    with LOCK:
        trade = active_trades.get(trade_id)
    if not trade:
        return

    symbol = trade['symbol']
    entry = trade['entry']
    sl = trade['sl']
    target = trade['target']
    qty = trade['qty']

    log.info(f'MONITOR START | {symbol} | entry={entry} | sl={sl} | target={target}')

    while True:
        with LOCK:
            if trade_id not in active_trades:
                return
        tm.sleep(30)  # check every 30s

        now = datetime.now()
        # Exit by 3:10 PM
        if (now.hour > 15) or (now.hour == 15 and now.minute >= 10):
            exit_trade(trade_id, reason='EOD_EXIT')
            return

        # Check current price
        try:
            if INDMONEY_AVAILABLE:
                ltp_data = api.get_ltp([symbol])
                curr_price = ltp_data.get(symbol, 0)
            else:
                data = yf.download(symbol + '.NS', period='1d', interval='1m', progress=False)
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                if len(data) == 0:
                    continue
                curr_price = float(data['Close'].iloc[-1])
            if curr_price <= 0:
                continue

            # SL hit (use Multyfi's SL directly)
            if curr_price <= sl:
                exit_trade(trade_id, exit_price=curr_price, reason='SL_HIT')
                return

            # Target hit
            if target and curr_price >= target:
                exit_trade(trade_id, exit_price=curr_price, reason='TARGET_HIT')
                return

        except Exception as e:
            log.warning(f'MONITOR ERROR | {symbol} | {e}')
            continue


def exit_trade(trade_id, exit_price=None, reason='MANUAL'):
    """Exit a trade and report to Fund Manager."""
    with LOCK:
        trade = active_trades.pop(trade_id, None)
    if not trade:
        return

    symbol = trade['symbol']
    entry = trade['entry']
    qty = trade['qty']

    if exit_price is None:
        # Fetch current price
        try:
            data = yf.download(symbol + '.NS', period='1d', interval='1m', progress=False)
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            exit_price = float(data['Close'].iloc[-1])
        except Exception:
            exit_price = entry  # fallback

    pnl = (exit_price - entry) * qty
    pnl_pct = (exit_price - entry) / entry * 100

    log.info(f'{reason} | {symbol} | entry={entry} | exit={exit_price:.2f} | pnl={pnl:.0f} ({pnl_pct:+.1f}%) | qty={qty}')

    # LIVE: place SELL order
    if not PAPER_MODE and INDMONEY_AVAILABLE:
        try:
            api.place_order(symbol, qty, 'SELL', price=0, order_type='MARKET', product='INTRADAY')
            log.info(f'SELL ORDER | {symbol} | qty={qty}')
        except Exception as e:
            log.error(f'SELL FAILED | {symbol} | {e}')

    # Report to Fund Manager
    report_exit(trade_id, pnl)

    # Update stats + save to history
    with LOCK:
        if pnl >= 0:
            stats['wins'] += 1
        else:
            stats['losses'] += 1
        stats['total_pnl'] += pnl

        trade_history.insert(0, {
            'symbol': symbol,
            'entry': entry,
            'exit': round(exit_price, 2),
            'sl': trade['sl'],
            'target': trade['target'],
            'qty': qty,
            'pnl': round(pnl, 2),
            'pnl_pct': round(pnl_pct, 2),
            'reason': reason,
            'time': trade['time'],
            'exit_time': datetime.now().strftime('%H:%M:%S'),
        })
        # Cap history to prevent unbounded memory growth
        if len(trade_history) > 200:
            del trade_history[200:]

    # LIVE: place sell order
    if not PAPER_MODE and INDMONEY_AVAILABLE:
        try:
            scrip_code = api.SCRIP_CODES.get(symbol)
            if scrip_code:
                sec_id = scrip_code
                api.place_order(symbol, qty, 'SELL', price=0, order_type='MARKET', product='INTRADAY')
                log.info(f'SELL ORDER | {symbol} | qty={qty}')
        except Exception as e:
            log.error(f'SELL FAILED | {symbol} | {e}')


# ─── Parse notification text ─────────────────────────────────────────────────

SKIP_WORDS = {
    'BUY', 'SELL', 'CALL', 'PUT', 'TARGET', 'STOP', 'LOSS', 'SL', 'TGT',
    'ENTRY', 'EXIT', 'CE', 'PE', 'LOT', 'MIS', 'CNC', 'NRML', 'NSE', 'BSE',
    'RS', 'INR', 'NEAR', 'ATM', 'OTM', 'ITM', 'OPTION', 'OPTIONS', 'PREMIUM',
    'PRICE', 'ABOVE', 'BELOW', 'MARKET', 'ORDER', 'LIMIT', 'TODAY', 'NOW',
    'URGENT', 'ALERT', 'SIGNAL', 'TRADE', 'TRADING', 'ADVISORY',
    'RECOMMENDATION', 'UNIVEST', 'MULTYFI', 'PROFIT', 'RETURNS', 'EQUITY',
    'INTRADAY', 'DELIVERY', 'INDSTOCKS', 'AROUND', 'NEAR', 'APPROX',
}


def parse_notification(text):
    """
    Parse Multyfi/IndStocks notification text.
    Expected: "BUY SBIN around 850 SL 830 TGT 870"
    Returns: (symbol, entry, sl, target) or None
    """
    text_upper = text.upper()

    # Extract stock name
    words = re.findall(r'[A-Z][A-Z0-9&-]{2,14}', text_upper)
    candidates = [w for w in words if w not in SKIP_WORDS and len(w) >= 3]
    symbol = candidates[0] if candidates else None

    # Extract numbers (prices)
    numbers = re.findall(r'[\d]+(?:\.[\d]+)?', text)
    numbers = [float(n) for n in numbers]

    entry, sl, target = None, None, None

    # Try named extraction first
    entry_m = re.search(r'(?:entry|around|@|buy\s+\w+\s+(?:around\s+)?)([\d.]+)', text_upper)
    sl_m = re.search(r'(?:sl|stop\s*loss|stoploss)\s*(?:rs?\s*)?\.?\s*([\d.]+)', text_upper)
    tgt_m = re.search(r'(?:tgt|target)\s*(?:rs?\s*)?\.?\s*([\d.]+)', text_upper)

    if entry_m:
        entry = float(entry_m.group(1))
    if sl_m:
        sl = float(sl_m.group(1))
    if tgt_m:
        target = float(tgt_m.group(1))

    # Fallback to positional numbers
    if not entry and len(numbers) >= 1:
        entry = numbers[0]
    if not sl and len(numbers) >= 2:
        sl = numbers[1]
    if not target and len(numbers) >= 3:
        target = numbers[2]

    if symbol and entry and sl:
        return symbol, entry, sl, target
    return None


# ─── HTML PWA ─────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<title>Multyfi Equity</title>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="theme-color" content="#0a0a0a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.json">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e0e0e0;padding:12px;max-width:500px;margin:0 auto}
h1{font-size:22px;color:#60a5fa;margin-bottom:2px}
.sub{font-size:12px;color:#666;margin-bottom:16px}
.stats{display:flex;gap:8px;margin-bottom:16px}
.stat{flex:1;background:#1a1a1a;border-radius:10px;padding:10px;text-align:center}
.stat .num{font-size:24px;font-weight:700}
.stat .label{font-size:10px;color:#888;margin-top:2px}
textarea{width:100%;background:#1a1a1a;color:#e0e0e0;border:1px solid #333;border-radius:12px;padding:14px;font-size:15px;height:80px;resize:none;margin-bottom:12px}
textarea:focus{border-color:#60a5fa;outline:none}
.or-line{text-align:center;color:#444;font-size:12px;margin:8px 0}
.fields{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.fields input{background:#1a1a1a;color:#e0e0e0;border:1px solid #333;border-radius:10px;padding:12px;font-size:14px}
.fields input:focus{border-color:#60a5fa;outline:none}
button{width:100%;padding:14px;background:#60a5fa;color:#0a0a0a;border:none;border-radius:12px;font-size:16px;font-weight:700;cursor:pointer}
button:active{transform:scale(0.98);background:#3b82f6}
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
.active{margin-top:20px}
.active h2{font-size:14px;color:#60a5fa;margin-bottom:8px}
.active-item{padding:10px;background:#0a1a2a;border:1px solid #1e3a5f;border-radius:8px;margin-bottom:6px;font-size:13px}
</style>
</head>
<body>

<h1>Multyfi Equity</h1>
<div class="sub">SL&lt;3% + VWAP filter | Intraday MIS | Auto-exit 3:10 PM</div>

<div class="stats">
<div class="stat"><div class="num" id="s_signals">0</div><div class="label">Signals</div></div>
<div class="stat"><div class="num" style="color:#4ade80" id="s_take">0</div><div class="label">TAKE</div></div>
<div class="stat"><div class="num" style="color:#f87171" id="s_skip">0</div><div class="label">SKIP</div></div>
<div class="stat"><div class="num" style="color:#fbbf24" id="s_pnl">0</div><div class="label">PnL</div></div>
</div>

<textarea id="notif" placeholder="Paste Multyfi notification...&#10;e.g. BUY SBIN around 850 SL 830 TGT 870"></textarea>
<button onclick="checkNotif()">Check Signal</button>

<div class="or-line">or enter manually</div>

<div class="fields">
<input id="f_sym" placeholder="Stock (SBIN)" autocapitalize="characters">
<input id="f_entry" placeholder="Entry" type="number">
<input id="f_sl" placeholder="SL" type="number">
<input id="f_tgt" placeholder="Target" type="number">
</div>
<button onclick="checkManual()">Check Manual</button>

<div id="result"></div>

<div class="active" id="active_section" style="display:none">
<h2>Active Trades</h2>
<div id="active_trades"></div>
</div>

<div class="history" id="hist_section" style="display:none">
<h2>Trade History</h2>
<div id="hist"></div>
</div>

<script>
loadStats();
loadHistory();
setInterval(loadStats, 15000);
setInterval(loadActive, 10000);
loadActive();

function loadStats(){
fetch('/stats').then(r=>r.json()).then(d=>{
document.getElementById('s_signals').textContent=d.signals||0;
document.getElementById('s_take').textContent=d.take||0;
document.getElementById('s_skip').textContent=d.skip||0;
document.getElementById('s_pnl').textContent=(d.total_pnl||0).toFixed(0);
}).catch(()=>{});
}

function loadActive(){
fetch('/active').then(r=>r.json()).then(d=>{
var el=document.getElementById('active_trades');
var sec=document.getElementById('active_section');
if(!d.trades||d.trades.length==0){sec.style.display='none';return}
sec.style.display='block';
var h='';
for(var t of d.trades){
h+='<div class="active-item"><strong>'+t.symbol+'</strong> qty='+t.qty+' entry='+t.entry+' sl='+t.sl+' <span class="time">'+t.time+'</span></div>';
}
el.innerHTML=h;
}).catch(()=>{});
}

function checkNotif(){
var text=document.getElementById('notif').value.trim();
if(!text)return;
doPost('/notify',{text:text});
}

function checkManual(){
var sym=document.getElementById('f_sym').value.trim().toUpperCase();
var entry=parseFloat(document.getElementById('f_entry').value);
var sl=parseFloat(document.getElementById('f_sl').value);
var tgt=parseFloat(document.getElementById('f_tgt').value)||null;
if(!sym||!entry||!sl){alert('Need stock, entry, SL');return}
doPost('/notify',{stock:sym,entry:entry,sl:sl,target:tgt});
}

async function doPost(url,body){
var el=document.getElementById('result');
el.innerHTML='<div class="result-card loading"><div style="text-align:center;padding:20px;font-size:16px;color:#fbbf24">Checking VWAP + SL...</div></div>';
try{
var r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
var d=await r.json();
showResult(d);
loadStats();
loadHistory();
loadActive();
}catch(e){el.innerHTML='<div class="result-card skip"><div style="color:#f87171">Error: '+e.message+'</div></div>'}
}

function showResult(d){
var el=document.getElementById('result');
if(d.error){el.innerHTML='<div class="result-card skip"><div class="decision skip-text">ERROR</div><div>'+d.error+'</div></div>';return}
var isTake=d.decision=='TAKE';
var h='<div class="result-card '+(isTake?'take':'skip')+'">';
h+='<div class="decision '+(isTake?'take-text':'skip-text')+'">'+(isTake?'TAKE':'SKIP')+'</div>';
h+='<div class="stock-name">'+d.sym+' @ Rs '+(d.entry||'?')+'</div>';
if(d.checks){for(var k in d.checks){h+='<div class="indicator"><div class="dot '+(d.checks[k]?'pass':'fail')+'">'+(d.checks[k]?'+':'-')+'</div>'+k+'</div>'}}
h+='<div class="metric">';
h+='<div class="metric-box"><div class="val">'+(d.sl_pct||'?')+'%</div><div class="lbl">SL Dist</div></div>';
h+='<div class="metric-box"><div class="val">'+(d.close||'?')+'</div><div class="lbl">Close</div></div>';
h+='<div class="metric-box"><div class="val">'+(d.vwap||'?')+'</div><div class="lbl">VWAP</div></div>';
h+='</div>';
if(d.reasons&&d.reasons.length>0){h+='<div class="reasons">';for(var i=0;i<d.reasons.length;i++){h+='<div>'+d.reasons[i]+'</div>'}h+='</div>'}
if(isTake){h+='<div class="sl-reminder">Using Multyfi SL @ Rs '+(d.sl||'?')+' ('+(d.sl_pct||'?')+'%)</div>'}
if(d.trade){h+='<div class="sl-reminder">Trade: '+d.trade.status+' | qty='+d.trade.qty+' | capital='+d.trade.capital+'</div>'}
h+='</div>';
el.innerHTML=h;
}

function loadHistory(){
fetch('/history').then(r=>r.json()).then(d=>{
var el=document.getElementById('hist');
var sec=document.getElementById('hist_section');
if(!d.trades||d.trades.length==0){sec.style.display='none';return}
sec.style.display='block';
var h='';
for(var t of d.trades){
var cls=t.pnl>=0?'hist-take':'hist-skip';
h+='<div class="hist-item"><div><span class="sym">'+t.symbol+'</span> <span class="'+cls+'">'+(t.pnl>=0?'+':'')+t.pnl+'</span> <span style="color:#555">'+t.reason+'</span></div><div class="time">'+t.time+'</div></div>';
}
el.innerHTML=h;
}).catch(()=>{});
}
</script>
</body>
</html>"""

MANIFEST = json.dumps({
    "name": "Multyfi Equity",
    "short_name": "MF Equity",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0a0a0a",
    "theme_color": "#0a0a0a",
    "icons": [{"src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>E</text></svg>", "sizes": "any", "type": "image/svg+xml"}]
})


# ─── HTTP Handler ─────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path

        if path == '/manifest.json':
            self._json_response(json.loads(MANIFEST))
            return

        if path == '/stats':
            with LOCK:
                snapshot = dict(stats)
            self._json_response(snapshot)
            return

        if path == '/active':
            with LOCK:
                trades = [
                    {'symbol': t['symbol'], 'entry': t['entry'], 'sl': t['sl'],
                     'target': t['target'], 'qty': t['qty'], 'time': t['time']}
                    for t in active_trades.values()
                ]
            self._json_response({'trades': trades})
            return

        if path == '/history':
            with LOCK:
                history_snapshot = list(trade_history[:50])
            self._json_response({'trades': history_snapshot})
            return

        if path.startswith('/check/'):
            sym = path[7:].strip().upper()
            if not sym:
                self._json_response({'error': 'no symbol'})
                return
            # Quick VWAP check only (no SL info)
            close, vwap, vwap_ok = check_vwap(sym)
            self._json_response({
                'sym': sym,
                'close': round(close, 2) if close else None,
                'vwap': round(vwap, 2) if vwap else None,
                'vwap_ok': vwap_ok,
            })
            return

        # Serve HTML
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(HTML.encode())

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode() if length else ''

        if self.path == '/notify' or self.path.startswith('/notify'):
            self._handle_notify(body)
            return

        self._json_response({'error': 'unknown endpoint'})

    def _handle_notify(self, body):
        """Handle POST /notify — structured JSON or raw notification text.
        Also handles EXIT signals from INDmoney."""

        # Check for EXIT/CLOSE signal first
        try:
            raw_text = body if isinstance(body, str) else ''
            try:
                data_check = json.loads(body)
                raw_text = str(data_check.get('text', '')) or str(data_check.get('body', '')) or str(data_check.get('message', '')) or str(data_check)
            except:
                pass

            exit_keywords = ['EXIT', 'BOOK PROFIT', 'CLOSE', 'SQUARE OFF', 'TARGET HIT', 'SL HIT', 'TRAIL']
            text_upper = raw_text.upper()
            is_exit = any(kw in text_upper for kw in exit_keywords)

            if is_exit:
                # Find which stock to exit
                words = re.findall(r'[A-Z][A-Z0-9&-]{2,14}', text_upper)
                skip = {'EXIT', 'BOOK', 'PROFIT', 'CLOSE', 'SQUARE', 'OFF', 'TARGET', 'HIT', 'TRAIL', 'STOP', 'LOSS', 'SL', 'BUY', 'SELL', 'EQUITY', 'INTRADAY', 'TRADE', 'STOCK', 'NAME', 'THE', 'FOR', 'AND', 'MIS'}
                candidates = [w for w in words if w not in skip and len(w) >= 3]

                if candidates:
                    exit_sym = candidates[0]
                    log.info(f'EXIT SIGNAL | {exit_sym} | {raw_text[:100]}')

                    # Find matching active trade
                    with LOCK:
                        exit_trade_id = None
                        for tid, trade in active_trades.items():
                            if trade['symbol'] == exit_sym:
                                exit_trade_id = tid
                                break

                    if exit_trade_id:
                        trade = active_trades[exit_trade_id]
                        # Get current price
                        try:
                            ltp_data = api.get_ltp([exit_sym])
                            current = ltp_data.get(exit_sym, trade['entry'])
                        except:
                            current = trade['entry']

                        pnl = (current - trade['entry']) / trade['entry'] * 100
                        close_trade(exit_trade_id, current, f'EXIT_SIGNAL ({pnl:+.1f}%)')

                        self._json_response({
                            'action': 'EXIT', 'sym': exit_sym,
                            'entry': trade['entry'], 'exit': current,
                            'pnl_pct': round(pnl, 2), 'status': 'closed'
                        })
                    else:
                        log.info(f'EXIT SIGNAL | {exit_sym} | no active trade found')
                        self._json_response({
                            'action': 'EXIT', 'sym': exit_sym,
                            'status': 'no_active_trade'
                        })
                    return
        except Exception as e:
            log.warning(f'Exit check error: {e}')

        symbol, entry, sl, target = None, None, None, None

        try:
            data = json.loads(body)

            # Structured: {stock, entry, sl, target}
            if 'stock' in data or 'symbol' in data:
                symbol = (data.get('stock') or data.get('symbol', '')).upper()
                entry = float(data.get('entry', 0))
                sl = float(data.get('sl', 0))
                target = float(data.get('target', 0)) if data.get('target') else None

            # Raw text: {text: "BUY SBIN around 850 SL 830 TGT 870"}
            elif 'text' in data or 'body' in data or 'message' in data:
                text = data.get('text') or data.get('body') or data.get('message', '')
                parsed = parse_notification(text)
                if not parsed:
                    self._json_response({'error': 'Could not parse notification', 'raw': text[:200]})
                    return
                symbol, entry, sl, target = parsed

            # Notification forwarder format
            elif 'notification' in data:
                notif = data['notification']
                text = notif.get('body') or notif.get('text') or notif.get('title', '')
                parsed = parse_notification(text)
                if not parsed:
                    self._json_response({'error': 'Could not parse notification', 'raw': text[:200]})
                    return
                symbol, entry, sl, target = parsed

            else:
                self._json_response({'error': 'unrecognized format', 'raw': str(data)[:200]})
                return

        except json.JSONDecodeError:
            # Raw text body
            parsed = parse_notification(body)
            if not parsed:
                self._json_response({'error': 'Could not parse', 'raw': body[:200]})
                return
            symbol, entry, sl, target = parsed

        if not symbol or not entry or not sl:
            self._json_response({'error': 'Missing stock, entry, or SL'})
            return

        # Run filters
        with LOCK:
            stats['signals'] += 1
        decision, details = evaluate_signal(symbol, entry, sl, target)
        log.info(f'SIGNAL | {symbol} | entry={entry} | sl={sl} | tgt={target} | {decision} | sl_pct={details["sl_pct"]}%')

        if decision == 'TAKE':
            with LOCK:
                stats['take'] += 1
            # Check market hours
            now = datetime.now()
            market_open = (now.hour == 9 and now.minute >= 15) or (10 <= now.hour < 15) or (now.hour == 15 and now.minute < 10)
            if market_open:
                trade_result = execute_trade(symbol, entry, sl, target, details)
                details['trade'] = trade_result
            else:
                log.info(f'TAKE but market closed | {symbol}')
                details['trade'] = {'status': 'market_closed'}
        else:
            with LOCK:
                stats['skip'] += 1

        self._json_response(details)

    def _json_response(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


# ─── EOD exit scheduler ──────────────────────────────────────────────────────

def eod_exit_scheduler():
    """Background thread: force-exit all active trades at 3:10 PM."""
    while True:
        now = datetime.now()
        # Only run during market hours
        if now.weekday() < 5:  # Mon-Fri
            if now.hour == 15 and now.minute == 10:
                with LOCK:
                    trade_ids = list(active_trades.keys())
                if trade_ids:
                    log.info(f'EOD EXIT | Closing {len(trade_ids)} active trades')
                    for tid in trade_ids:
                        exit_trade(tid, reason='EOD_EXIT')
        tm.sleep(30)


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    mode_str = 'PAPER' if PAPER_MODE else 'LIVE'
    print(f'Multyfi Equity Trader on port {PORT} [{mode_str}]')
    print(f'  POST /notify  - receive signal (JSON: stock/entry/sl/target or text)')
    print(f'  GET  /check/SYM - quick VWAP check')
    print(f'  GET  /history - trade history')
    print(f'  GET  /active  - active trades')
    print(f'  GET  /stats   - signal stats')
    print(f'  GET  /        - PWA web interface')
    log.info(f'Multyfi Equity Trader started [{mode_str}] port={PORT}')

    # Start EOD exit scheduler
    threading.Thread(target=eod_exit_scheduler, daemon=True).start()

    server = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    server.serve_forever()
