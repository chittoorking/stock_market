"""
FUND MANAGER — The Master
Controls all soldiers. Allocates capital. Logs everything.
Port 8904

CAPITAL MATRIX (LOCKED):
  POOL Rs 10K:  News Rs 8K (1 trade)  | Buffer Rs 2K
  POOL Rs 25K:  News Rs 15K (3 trades) | Buffer Rs 10K
  POOL Rs 50K:  News Rs 25K (5 trades) | Buffer Rs 25K
  POOL Rs 75K:  News Rs 35K (5 trades) | Buffer Rs 40K
  POOL Rs 1L:   News Rs 40K (5 trades) | Buffer Rs 60K

Buffer = shared for IPO + Univest + CAS. First come first served.
News bot: 9:05 sees ALL signals, Master picks top N by quality.
CAS: from freed capital at 3:15 PM. Only conv>=8.
Bonus: 10% of POOL from buffer to top trades if standout exists.
Daily loss limit: 10% of POOL → all stop.
"""
import http.server
import json
import threading
from datetime import datetime, date
from pathlib import Path

PORT = 8904
LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)

# ============================================================
# CAPITAL MATRIX — LOCKED
# ============================================================
CAPITAL_MATRIX = [
    # (min_pool, max_pool, news_alloc, max_trades, soldiers)
    (0,      14999,  0.80, 1, ['news_bot', 'cas']),
    (15000,  29999,  0.60, 3, ['news_bot', 'cas', 'ipo']),
    (30000,  49999,  0.50, 4, ['news_bot', 'cas', 'ipo']),
    (50000,  74999,  0.50, 5, ['news_bot', 'cas', 'ipo', 'multyfi_equity', 'multyfi_options', 'multyfi_futures', 'commodity']),
    (75000,  99999,  0.47, 5, ['news_bot', 'cas', 'ipo', 'multyfi_equity', 'multyfi_options', 'multyfi_futures', 'commodity']),
    (100000, 999999, 0.40, 5, ['news_bot', 'cas', 'ipo', 'multyfi_equity', 'multyfi_options', 'multyfi_futures', 'commodity']),
]

def get_config(pool):
    """Lookup capital config from matrix."""
    for min_p, max_p, news_pct, max_trades, soldiers in CAPITAL_MATRIX:
        if min_p <= pool <= max_p:
            news_alloc = round(pool * news_pct)
            per_trade = round(news_alloc / max_trades)
            buffer = pool - news_alloc
            return {
                'news_alloc': news_alloc,
                'max_trades': max_trades,
                'per_trade': per_trade,
                'buffer': buffer,
                'soldiers': soldiers,
            }
    # Default for very large pools
    news_alloc = round(pool * 0.40)
    return {
        'news_alloc': news_alloc,
        'max_trades': 5,
        'per_trade': round(news_alloc / 5),
        'buffer': pool - news_alloc,
        'soldiers': ['news_bot', 'cas', 'ipo', 'multyfi_equity', 'multyfi_options', 'multyfi_futures', 'commodity'],
    }


# ============================================================
# STATE
# ============================================================
class FundState:
    def __init__(self, initial_pool=100000):
        self.pool = initial_pool
        self.deployed = {}          # {trade_id: {strategy, sym, amount, entry_time, conviction, projection}}
        self.daily_pnl = 0
        self.daily_loss_limit_pct = 10
        self.skipped = []           # [{sym, strategy, reason, time, amount, conviction}]
        self.closed = []            # [{sym, strategy, pnl, time}]
        self.today = date.today()
        self.trade_counter = 0
        self.lock = threading.Lock()
        self.bonus_used = 0         # bonus from buffer used today

    def reset_daily(self):
        if date.today() != self.today:
            self.today = date.today()
            self.daily_pnl = 0
            self.deployed = {}  # clear ghost positions from yesterday
            self.skipped = []
            self.closed = []
            self.bonus_used = 0

    def config(self):
        return get_config(self.pool)

    def total_deployed(self):
        return sum(t['amount'] for t in self.deployed.values())

    def news_deployed(self):
        return sum(t['amount'] for t in self.deployed.values() if t['strategy'] == 'news_bot')

    def buffer(self):
        return self.pool - self.total_deployed()

    def min_buffer(self):
        return round(self.pool * 0.10)

    def daily_loss_hit(self):
        return self.daily_pnl <= -(self.pool * self.daily_loss_limit_pct / 100)

    def available_strategies(self):
        return self.config()['soldiers']


STATE = FundState()


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'{ts} | {msg}'
    print(line)
    with open(LOG_DIR / 'fund_manager.log', 'a') as f:
        f.write(line + '\n')


# ============================================================
# SIZING LOGIC
# ============================================================
def size_news_trades(state, num_signals, projections):
    """
    Pick top N signals. Even split from news budget.
    Bonus from buffer to top 3 if standout exists.
    Returns: [(index, amount), ...]
    """
    cfg = state.config()
    max_trades = min(cfg['max_trades'], num_signals)
    per_trade = min(round(cfg['news_alloc'] / max_trades), 20000) if max_trades > 0 else 0
    # Cap at 50% of news budget per trade
    per_trade = min(per_trade, round(cfg['news_alloc'] * 0.50))

    # Rank by projection, pick top N
    ranked = sorted(range(len(projections)), key=lambda i: projections[i], reverse=True)
    selected = ranked[:max_trades]

    # Bonus check
    bonus_pool = round(state.pool * 0.10)
    avg_proj = sum(projections[i] for i in selected) / max(len(selected), 1)
    top_3 = selected[:3]
    standout = any(projections[i] - avg_proj >= 1.5 for i in top_3)

    allocations = []
    bonus_total = 0
    for idx in selected:
        amount = per_trade
        if standout and idx in top_3:
            bonus = round(bonus_pool / len(top_3))
            if state.buffer() - bonus_total - bonus >= state.min_buffer():
                amount += bonus
                bonus_total += bonus
        allocations.append((idx, amount))

    return allocations, bonus_total


def size_ipo(state, num_ipos):
    """IPO sizing from buffer. Max 25% per IPO, max 30% total."""
    max_total = round(state.pool * 0.30)
    if num_ipos == 1:
        each = min(round(state.pool * 0.25), state.buffer() - state.min_buffer())
    elif num_ipos == 2:
        each = min(round(state.pool * 0.15), (state.buffer() - state.min_buffer()) // 2)
    else:
        each = min(round(state.pool * 0.10), (state.buffer() - state.min_buffer()) // num_ipos)
    return max(0, each)


def size_buffer_trade(state, strategy):
    """Size a trade from the buffer pool. Strategy-aware.
    
    Budget from buffer (Rs 60K at Rs 1L pool):
      - Multyfi options: Rs 5-7K per trade (premium). Max 3 concurrent.
      - Multyfi futures: Rs 31-35K per trade (margin). Max 1 concurrent.
      - Multyfi equity: Rs 15K per trade. Max 2 concurrent.
      - IPO: handled by size_ipo()
      - CAS: handled by size_cas()
      - Commodity: Rs 5-10K per trade (option premium). Max 2 concurrent.
    """
    available = state.buffer() - state.min_buffer()
    if available < 3000:
        return 0
    
    # Count current positions by strategy
    active = sum(1 for t in state.deployed.values() if t["strategy"] == strategy)
    
    if strategy == "multyfi_futures":
        # Futures needs Rs 31K margin. Max 1 at a time.
        if active >= 1: return 0
        return min(35000, available)
    
    elif strategy == "multyfi_options":
        # Options need Rs 3-7K premium. Max 3 at a time.
        if active >= 3: return 0
        return min(10000, available)
    
    elif strategy == "multyfi_equity":
        # Equity MIS. Max 2 at a time.
        if active >= 2: return 0
        return min(15000, available)
    
    elif strategy == "commodity":
        # Commodity options. Max 2 at a time.
        if active >= 2: return 0
        return min(10000, available)
    
    else:
        # Generic fallback
        return min(15000, available)


def size_univest(state):
    """Legacy compatibility — redirects to size_buffer_trade."""
    return size_buffer_trade(state, "generic")


def size_cas(state, conviction):
    """CAS sizing. Only conv>=8."""
    if conviction < 8:
        return 0
    available = state.buffer() - state.min_buffer()
    if conviction >= 9:
        return min(round(state.pool * 0.08), max(0, available))
    else:
        return min(round(state.pool * 0.05), max(0, available))


# ============================================================
# HTTP HANDLER
# ============================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        STATE.reset_daily()
        cfg = STATE.config()

        if self.path == '/status':
            self._json({
                'pool': STATE.pool,
                'deployed': STATE.total_deployed(),
                'buffer': STATE.buffer(),
                'min_buffer': STATE.min_buffer(),
                'daily_pnl': STATE.daily_pnl,
                'daily_loss_hit': STATE.daily_loss_hit(),
                'active_strategies': STATE.available_strategies(),
                'news_budget': cfg['news_alloc'],
                'news_max_trades': cfg['max_trades'],
                'news_per_trade': cfg['per_trade'],
                'news_deployed': STATE.news_deployed(),
                'bonus_used': STATE.bonus_used,
                'positions': list(STATE.deployed.values()),
                'skipped_today': STATE.skipped,
                'closed_today': STATE.closed,
            })
            return

        if self.path == '/dashboard':
            self._serve_dashboard()
            return

        if self.path == '/config':
            self._json(cfg)
            return

        self._json({'service': 'Fund Manager', 'port': PORT})

    def do_POST(self):
        STATE.reset_daily()
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == '/request':
            self._handle_request(body)
        elif self.path == '/batch_request':
            self._handle_batch_request(body)
        elif self.path == '/report':
            self._handle_report(body)
        elif self.path == '/set_pool':
            STATE.pool = body.get('pool', STATE.pool)
            log(f'POOL set to Rs {STATE.pool:,}')
            self._json({'pool': STATE.pool, 'config': STATE.config()})
        elif self.path == '/stop':
            log('EMERGENCY STOP')
            self._json({'status': 'stopped'})
        else:
            self._json({'error': 'unknown endpoint'})

    def _handle_batch_request(self, body):
        """News bot sends ALL signals at once. Master picks top N and sizes."""
        with STATE.lock:
            if STATE.daily_loss_hit():
                log('BATCH REJECTED — daily loss limit hit')
                self._json({'approved': [], 'reason': 'daily loss limit'})
                return

            signals = body.get('signals', [])
            # signals = [{sym, projection, conviction, catalyst}, ...]

            if not signals:
                self._json({'approved': [], 'reason': 'no signals'})
                return

            projections = [s.get('projection', 0) for s in signals]
            allocations, bonus = size_news_trades(STATE, len(signals), projections)

            STATE.bonus_used = bonus
            approved = []

            for idx, amount in allocations:
                sig = signals[idx]
                STATE.trade_counter += 1
                tid = f'news_{STATE.trade_counter}'
                STATE.deployed[tid] = {
                    'trade_id': tid,
                    'strategy': 'news_bot',
                    'sym': sig.get('sym', '?'),
                    'amount': amount,
                    'entry_time': datetime.now().strftime('%H:%M'),
                    'conviction': sig.get('conviction', 5),
                    'projection': sig.get('projection', 0),
                    'catalyst': sig.get('catalyst', '')[:50],
                }
                approved.append({'trade_id': tid, 'sym': sig['sym'], 'amount': amount})
                log(f"APPROVED news {sig['sym']} Rs {amount:,} proj={sig.get('projection',0)}% | Deployed: Rs {STATE.total_deployed():,} Buffer: Rs {STATE.buffer():,}")

            # Log skipped signals
            selected_indices = set(idx for idx, _ in allocations)
            for i, sig in enumerate(signals):
                if i not in selected_indices:
                    STATE.skipped.append({
                        'sym': sig.get('sym', '?'),
                        'strategy': 'news_bot',
                        'reason': f"Not in top {STATE.config()['max_trades']} by projection",
                        'time': datetime.now().strftime('%H:%M'),
                        'amount': STATE.config()['per_trade'],
                        'conviction': sig.get('conviction', 0),
                        'projection': sig.get('projection', 0),
                    })
                    log(f"SKIPPED news {sig.get('sym','?')} proj={sig.get('projection',0)}% — not in top {STATE.config()['max_trades']}")

            self._json({
                'approved': approved,
                'bonus_used': bonus,
                'total_deployed': STATE.total_deployed(),
                'buffer': STATE.buffer(),
            })

    def _handle_request(self, body):
        """Single trade request from IPO/Univest/CAS."""
        with STATE.lock:
            strategy = body.get('strategy', '')
            sym = body.get('symbol', '')
            conviction = body.get('conviction', 5)
            projection = body.get('projection', 0)

            # Check daily loss
            if STATE.daily_loss_hit():
                reason = f'Daily loss limit hit (Rs {STATE.daily_pnl:+,.0f})'
                STATE.skipped.append({'sym': sym, 'strategy': strategy, 'reason': reason,
                    'time': datetime.now().strftime('%H:%M'), 'amount': 0, 'conviction': conviction, 'projection': projection})
                log(f'REJECTED {strategy} {sym} — {reason}')
                self._json({'approved': False, 'reason': reason})
                return

            # Check strategy available
            if strategy not in STATE.available_strategies():
                reason = f'{strategy} OFF at POOL Rs {STATE.pool:,}'
                STATE.skipped.append({'sym': sym, 'strategy': strategy, 'reason': reason,
                    'time': datetime.now().strftime('%H:%M'), 'amount': 0, 'conviction': conviction, 'projection': projection})
                log(f'REJECTED {strategy} {sym} — {reason}')
                self._json({'approved': False, 'reason': reason})
                return

            # Size based on strategy
            if strategy == 'ipo':
                num_ipos = body.get('num_ipos', 1)
                amount = size_ipo(STATE, num_ipos)
            elif strategy in ('multyfi_options', 'multyfi_futures', 'multyfi_equity', 'commodity'):
                amount = size_buffer_trade(STATE, strategy)
            elif strategy == 'cas':
                amount = size_cas(STATE, conviction)
            else:
                amount = min(body.get('amount', 0), STATE.buffer() - STATE.min_buffer())

            if amount <= 0:
                reason = f'Buffer too low: Rs {STATE.buffer():,} (min Rs {STATE.min_buffer():,})'
                STATE.skipped.append({'sym': sym, 'strategy': strategy, 'reason': reason,
                    'time': datetime.now().strftime('%H:%M'), 'amount': body.get('amount', 0),
                    'conviction': conviction, 'projection': projection})
                log(f'SKIPPED {strategy} {sym} — {reason}')
                self._json({'approved': False, 'reason': reason, 'skipped': True})
                return

            # APPROVED
            STATE.trade_counter += 1
            tid = f'{strategy}_{STATE.trade_counter}'
            STATE.deployed[tid] = {
                'trade_id': tid,
                'strategy': strategy,
                'sym': sym,
                'amount': amount,
                'entry_time': datetime.now().strftime('%H:%M'),
                'conviction': conviction,
                'projection': projection,
            }
            log(f'APPROVED {strategy} {sym} Rs {amount:,} conv={conviction} | Deployed: Rs {STATE.total_deployed():,} Buffer: Rs {STATE.buffer():,}')
            self._json({'approved': True, 'trade_id': tid, 'amount': amount,
                'deployed': STATE.total_deployed(), 'buffer': STATE.buffer()})

    def _handle_report(self, body):
        """Trade exit report."""
        with STATE.lock:
            tid = body.get('trade_id', '')
            pnl = body.get('pnl', 0)

            if tid in STATE.deployed:
                trade = STATE.deployed.pop(tid)
                STATE.daily_pnl += pnl
                STATE.closed.append({
                    'sym': trade['sym'], 'strategy': trade['strategy'],
                    'amount': trade['amount'], 'pnl': pnl,
                    'time': datetime.now().strftime('%H:%M'),
                })
                log(f"CLOSED {trade['strategy']} {trade['sym']} Rs {pnl:+,.0f} | Daily: Rs {STATE.daily_pnl:+,.0f} | Buffer: Rs {STATE.buffer():,}")

                if STATE.daily_loss_hit():
                    log('*** DAILY LOSS LIMIT HIT — ALL STOP ***')

                self._json({'status': 'closed', 'daily_pnl': STATE.daily_pnl, 'buffer': STATE.buffer()})
            else:
                self._json({'error': f'{tid} not found'})

    def _serve_dashboard(self):
        cfg = STATE.config()
        html = """<!DOCTYPE html>
<html><head><title>Fund Manager</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="10">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e0e0e0;padding:12px;max-width:600px;margin:0 auto}
h1{color:#4ade80;font-size:20px;margin-bottom:4px}
.sub{color:#666;font-size:12px;margin-bottom:12px}
.card{background:#1a1a1a;border-radius:10px;padding:12px;margin-bottom:10px}
.row{display:flex;justify-content:space-between;padding:4px 0;font-size:14px}
.label{color:#888}.value{font-weight:700}
.green{color:#4ade80}.red{color:#f87171}.yellow{color:#fbbf24}
h2{color:#60a5fa;font-size:15px;margin:14px 0 6px}
.trade{background:#111;border-radius:8px;padding:8px;margin:4px 0;font-size:13px}
.skip{background:#1a0a0a;border:1px solid #7f1d1d;border-radius:8px;padding:8px;margin:4px 0;font-size:13px}
.btn{padding:4px 12px;background:#4ade80;color:#000;border:none;border-radius:6px;font-size:11px;font-weight:700;cursor:pointer}
.meter{height:8px;background:#333;border-radius:4px;margin:4px 0}
.meter-fill{height:100%;border-radius:4px}
</style></head><body>
<h1>Fund Manager</h1>
<div class="sub">The Master — controls all soldiers</div>
<div class="card" id="s"></div>
<h2>Active Positions</h2><div id="p"></div>
<h2>Skipped Today</h2><div id="sk"></div>
<h2>Closed Today</h2><div id="cl"></div>
<script>
fetch('/status').then(r=>r.json()).then(d=>{
var pc=d.daily_pnl>=0?'green':'red';
var dp=d.deployed/d.pool*100;
document.getElementById('s').innerHTML=`
<div class="row"><span class="label">POOL</span><span class="value">Rs ${d.pool.toLocaleString()}</span></div>
<div class="meter"><div class="meter-fill" style="width:${dp}%;background:${dp>50?'#f87171':dp>30?'#fbbf24':'#4ade80'}"></div></div>
<div class="row"><span class="label">Deployed</span><span class="value yellow">Rs ${d.deployed.toLocaleString()} (${dp.toFixed(0)}%)</span></div>
<div class="row"><span class="label">Buffer</span><span class="value green">Rs ${d.buffer.toLocaleString()}</span></div>
<div class="row"><span class="label">Min Buffer</span><span class="value">Rs ${d.min_buffer.toLocaleString()}</span></div>
<div class="row"><span class="label">Daily P&L</span><span class="value ${pc}">Rs ${d.daily_pnl>=0?'+':''}${d.daily_pnl.toLocaleString()}</span></div>
<div class="row"><span class="label">News</span><span class="value">Rs ${d.news_budget.toLocaleString()} (${d.news_max_trades} trades x Rs ${d.news_per_trade.toLocaleString()}) used: Rs ${d.news_deployed.toLocaleString()}</span></div>
<div class="row"><span class="label">Bonus Used</span><span class="value">${d.bonus_used>0?'Rs '+d.bonus_used.toLocaleString():'none'}</span></div>
<div class="row"><span class="label">Soldiers</span><span class="value">${d.active_strategies.join(' + ')}</span></div>
${d.daily_loss_hit?'<div style="background:#7f1d1d;color:#f87171;padding:8px;border-radius:6px;text-align:center;font-weight:700;margin-top:8px">DAILY LOSS LIMIT — ALL STOPPED</div>':''}`;
var ph='';d.positions.forEach(p=>{ph+='<div class="trade"><b>'+p.sym+'</b> ('+p.strategy+') Rs '+p.amount.toLocaleString()+' | conv='+p.conviction+' proj='+p.projection+'% | '+p.entry_time+'</div>'});
document.getElementById('p').innerHTML=ph||'<div class="trade" style="color:#666">No active positions</div>';
var sh='';d.skipped_today.forEach(s=>{sh+='<div class="skip"><b>'+s.sym+'</b> ('+s.strategy+') | '+s.reason+' | proj='+s.projection+'% conv='+s.conviction+' | '+s.time+'</div>'});
document.getElementById('sk').innerHTML=sh||'<div class="trade" style="color:#666">No skips</div>';
var ch='';d.closed_today.forEach(c=>{var cl=c.pnl>=0?'green':'red';ch+='<div class="trade"><b>'+c.sym+'</b> ('+c.strategy+') <span class="'+cl+'">Rs '+(c.pnl>=0?'+':'')+c.pnl.toLocaleString()+'</span> | '+c.time+'</div>'});
document.getElementById('cl').innerHTML=ch||'<div class="trade" style="color:#666">No closes</div>';
});
</script></body></html>"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def _json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


if __name__ == '__main__':
    log(f'Fund Manager started | POOL: Rs {STATE.pool:,} | Config: {STATE.config()}')
    server = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    server.serve_forever()
