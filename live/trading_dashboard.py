"""Trading Dashboard — real-time monitoring on port 8900.

Reads audit JSONL + trading logs. Shows:
- Live P&L, positions, signals
- Daily/weekly/monthly stats
- Strategy health (WR, edge decay, consecutive losses)
- Early warning alerts
"""
import http.server
import json
import os
import glob
from datetime import datetime, date
from pathlib import Path


# ── PID Lock — prevent duplicates ───────────────────────────────────
import fcntl
LOCK_FILE = os.path.expanduser('~/dashboard.lock')
_lock_fp = open(LOCK_FILE, 'w')
try:
    fcntl.flock(_lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lock_fp.write(str(os.getpid()))
    _lock_fp.flush()
except BlockingIOError:
    print('Dashboard already running. Exiting.')
    exit(0)

PORT = 8899
LOG_DIR = Path(os.path.expanduser('~/news-trading/live/logs'))

HTML = """<!DOCTYPE html>
<html>
<head>
<title>Trading Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e0e0e0;padding:12px}
h1{font-size:20px;color:#4ade80;margin-bottom:12px}
h2{font-size:15px;color:#60a5fa;margin:16px 0 8px;border-bottom:1px solid #222;padding-bottom:4px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.card{background:#111;border:1px solid #222;border-radius:8px;padding:14px}
.big{font-size:28px;font-weight:700}
.green{color:#4ade80}.red{color:#f87171}.yellow{color:#fbbf24}.gray{color:#666}
table{width:100%%;border-collapse:collapse;font-size:12px;margin-top:6px}
th,td{padding:4px 6px;text-align:left;border-bottom:1px solid #1a1a1a}
th{color:#888;font-weight:500}
.mono{font-family:monospace;font-size:11px}
.warn{background:#2a1a00;border:1px solid #92400e;color:#fbbf24;padding:8px;border-radius:6px;margin:8px 0;font-size:12px}
.alert{background:#2a0a0a;border:1px solid #7f1d1d;color:#f87171;padding:8px;border-radius:6px;margin:8px 0;font-size:12px}
.ok{background:#052e16;border:1px solid #166534;color:#4ade80;padding:8px;border-radius:6px;margin:8px 0;font-size:12px}
.bar{height:4px;border-radius:2px;margin-top:4px}
.bar-green{background:#4ade80}.bar-red{background:#f87171}
</style>
</head>
<body>
<h1>Trading Dashboard</h1>
<div id="content">Loading...</div>
<script>
async function load(){
  try{
    const r=await fetch('/api/dashboard');
    const d=await r.json();
    document.getElementById('content').innerHTML=render(d);
  }catch(e){
    document.getElementById('content').innerHTML='<div class="alert">Error: '+e.message+'</div>';
  }
}

function render(d){
  let h='';

  // Alerts
  if(d.alerts && d.alerts.length>0){
    h+=d.alerts.map(a=>'<div class="'+(a.level=='red'?'alert':a.level=='yellow'?'warn':'ok')+'">'+a.msg+'</div>').join('');
  }

  // Summary cards
  h+='<div class="grid">';
  h+=card('Today P&L','<span class="big '+(d.today_pnl>=0?'green':'red')+'">Rs '+fmt(d.today_pnl)+'</span><br><span class="gray">'+d.today_trades+' trades | '+d.today_wr+'%% WR</span>');
  h+=card('Week P&L','<span class="big '+(d.week_pnl>=0?'green':'red')+'">Rs '+fmt(d.week_pnl)+'</span><br><span class="gray">'+d.week_trades+' trades | '+d.week_wr+'%% WR</span>');
  h+=card('Total P&L','<span class="big '+(d.total_pnl>=0?'green':'red')+'">Rs '+fmt(d.total_pnl)+'</span><br><span class="gray">'+d.total_trades+' trades</span>');
  h+=card('Pool','<span class="big">Rs '+fmt(d.pool)+'</span><br><span class="gray">Deployed: Rs '+fmt(d.deployed)+' | Free: Rs '+fmt(d.available)+'</span>');
  h+='</div>';

  // Active positions
  h+='<h2>Active Positions</h2>';
  if(d.positions && d.positions.length>0){
    h+='<table><tr><th>Symbol</th><th>Strategy</th><th>Side</th><th>Entry</th><th>Time</th></tr>';
    d.positions.forEach(p=>{h+='<tr><td>'+p.sym+'</td><td>'+p.strategy+'</td><td>'+p.side+'</td><td>'+p.entry+'</td><td>'+p.time+'</td></tr>'});
    h+='</table>';
  }else h+='<div class="gray">No open positions</div>';

  // Strategy health
  h+='<h2>Strategy Health (last 7 days)</h2>';
  h+='<table><tr><th>Strategy</th><th>Trades</th><th>WR</th><th>P&L</th><th>Avg Win</th><th>Avg Loss</th><th>Consec Loss</th><th>Status</th></tr>';
  (d.strategy_health||[]).forEach(s=>{
    let status=s.wr>=70?'<span class="green">HEALTHY</span>':s.wr>=55?'<span class="yellow">WATCH</span>':'<span class="red">DANGER</span>';
    h+='<tr><td>'+s.name+'</td><td>'+s.trades+'</td><td>'+s.wr+'%%</td><td class="'+(s.pnl>=0?'green':'red')+'">'+fmt(s.pnl)+'</td><td class="green">'+fmt(s.avg_win)+'</td><td class="red">'+fmt(s.avg_loss)+'</td><td>'+s.consec_loss+'</td><td>'+status+'</td></tr>';
  });
  h+='</table>';

  // Recent signals
  h+='<h2>Recent Signals (last 50)</h2>';
  h+='<table><tr><th>Time</th><th>Type</th><th>Symbol</th><th>Details</th></tr>';
  (d.signals||[]).slice(-50).reverse().forEach(s=>{
    let cls=s.action.includes('TRADE')?'green':s.action.includes('SKIP')?'gray':'yellow';
    h+='<tr><td class="mono">'+s.ts+'</td><td class="'+cls+'">'+s.action+'</td><td>'+s.symbol+'</td><td class="mono">'+s.detail+'</td></tr>';
  });
  h+='</table>';

  // Daily P&L chart
  h+='<h2>Daily P&L</h2>';
  (d.daily_pnl||[]).forEach(day=>{
    let w=Math.min(Math.abs(day.pnl)/500,100);
    let cls=day.pnl>=0?'bar-green':'bar-red';
    h+='<div style="display:flex;align-items:center;gap:8px;margin:2px 0"><span class="mono" style="width:80px">'+day.date+'</span><div class="bar '+cls+'" style="width:'+w+'%%"></div><span class="mono '+(day.pnl>=0?'green':'red')+'">'+fmt(day.pnl)+'</span><span class="gray mono">'+day.trades+'t</span></div>';
  });

  // Token status
  h+='<h2>System</h2>';
  h+='<div class="gray mono">Token: '+(d.token_ok?'<span class="green">VALID</span>':'<span class="red">EXPIRED</span>')+' | TOTP: '+d.totp_status+' | MCX bot: '+(d.mcx_running?'<span class="green">RUNNING</span>':'<span class="red">DOWN</span>')+'</div>';
  h+='<div class="gray mono" style="margin-top:4px">Last refresh: '+d.last_refresh+'</div>';

  return h;
}

function card(title,body){return '<div class="card"><div class="gray" style="font-size:11px;margin-bottom:4px">'+title+'</div>'+body+'</div>';}
function fmt(n){return n?Number(n).toLocaleString('en-IN'):'0';}

load();
setInterval(load,30000);
</script>
</body>
</html>"""


def load_audit_records(days_back=7):
    """Load audit JSONL for last N days."""
    records = []
    today = date.today()
    for i in range(days_back):
        d = today.__class__(today.year, today.month, today.day)
        from datetime import timedelta
        d = today - timedelta(days=i)
        path = LOG_DIR / f'audit_{d.strftime("%Y%m%d")}.jsonl'
        if path.exists():
            for line in open(path):
                try:
                    records.append(json.loads(line.strip()))
                except:
                    pass
    return records


def compute_dashboard():
    """Compute all dashboard data from audit trail."""
    records = load_audit_records(30)
    today_str = date.today().strftime('%Y-%m-%d')

    # Trades = APPROVE + RELEASE pairs
    trades = []
    approvals = {}
    for r in records:
        if r.get('action') == 'APPROVE':
            approvals[r.get('trade_id', '')] = r
        elif r.get('action') == 'RELEASE':
            tid = r.get('trade_id', '')
            entry = approvals.get(tid, {})
            trades.append({
                'date': r['ts'][:10],
                'symbol': r.get('symbol', ''),
                'strategy': entry.get('strategy', ''),
                'pnl': r.get('pnl', 0),
                'trade_id': tid,
            })

    # Today
    today_trades = [t for t in trades if t['date'] == today_str]
    today_pnl = sum(t['pnl'] for t in today_trades)
    today_wins = sum(1 for t in today_trades if t['pnl'] > 0)
    today_wr = int(today_wins / len(today_trades) * 100) if today_trades else 0

    # Week (last 5 trading days)
    from datetime import timedelta
    week_dates = set()
    d = date.today()
    while len(week_dates) < 5:
        if d.weekday() < 5:
            week_dates.add(d.strftime('%Y-%m-%d'))
        d -= timedelta(days=1)
    week_trades = [t for t in trades if t['date'] in week_dates]
    week_pnl = sum(t['pnl'] for t in week_trades)
    week_wins = sum(1 for t in week_trades if t['pnl'] > 0)
    week_wr = int(week_wins / len(week_trades) * 100) if week_trades else 0

    # Total
    total_pnl = sum(t['pnl'] for t in trades)

    # Strategy health
    strategy_health = []
    for strat in ['news_orb', 'multyfi_options', 'ipo', 'equity_notif']:
        st = [t for t in trades if t['strategy'] == strat and t['date'] in week_dates]
        if not st:
            continue
        wins = sum(1 for t in st if t['pnl'] > 0)
        losses = [t for t in st if t['pnl'] <= 0]
        winners = [t for t in st if t['pnl'] > 0]

        # Consecutive losses
        max_consec = 0
        consec = 0
        for t in st:
            if t['pnl'] <= 0:
                consec += 1
                max_consec = max(max_consec, consec)
            else:
                consec = 0

        strategy_health.append({
            'name': strat,
            'trades': len(st),
            'wr': int(wins / len(st) * 100) if st else 0,
            'pnl': round(sum(t['pnl'] for t in st)),
            'avg_win': round(sum(t['pnl'] for t in winners) / len(winners)) if winners else 0,
            'avg_loss': round(sum(t['pnl'] for t in losses) / len(losses)) if losses else 0,
            'consec_loss': max_consec,
        })

    # Signals
    signals = []
    for r in records:
        if r.get('component') == 'gemini' and r.get('action', '').startswith('SIGNAL_'):
            signals.append({
                'ts': r['ts'][11:19],
                'action': r['action'],
                'symbol': r.get('symbol', ''),
                'detail': r.get('why', r.get('projection', r.get('reason', '')))[:80],
            })
        elif r.get('component') == 'multyfi' and r.get('action', '').startswith(('SIGNAL_', 'ENTRY_', 'EXIT_')):
            signals.append({
                'ts': r['ts'][11:19],
                'action': r['action'],
                'symbol': r.get('symbol', ''),
                'detail': f"score={r.get('score', '')} {r.get('opt_type', '')}",
            })
        elif r.get('action') == 'HEADLINE_FILTERED':
            signals.append({
                'ts': r['ts'][11:19],
                'action': 'HEADLINE',
                'symbol': r.get('symbol', '')[:40],
                'detail': r.get('source', ''),
            })

    # Daily P&L
    daily_pnl = {}
    for t in trades:
        daily_pnl.setdefault(t['date'], {'pnl': 0, 'trades': 0})
        daily_pnl[t['date']]['pnl'] += t['pnl']
        daily_pnl[t['date']]['trades'] += 1
    daily_list = [{'date': d, 'pnl': round(v['pnl']), 'trades': v['trades']}
                  for d, v in sorted(daily_pnl.items())]

    # Active positions (from fund manager deployed)
    positions = []
    deployed_total = 0
    for r in records:
        if r.get('action') == 'APPROVE' and r['ts'][:10] == today_str:
            tid = r.get('trade_id', '')
            released = any(r2.get('trade_id') == tid and r2.get('action') == 'RELEASE'
                          for r2 in records)
            if not released:
                positions.append({
                    'sym': r.get('symbol', ''),
                    'strategy': r.get('strategy', ''),
                    'side': 'OPEN',
                    'entry': r.get('amount', 0),
                    'time': r['ts'][11:19],
                })
                deployed_total += r.get('amount', 0)

    # Pool
    pool = 0
    for r in records:
        if r.get('action') == 'INIT' and r.get('component') == 'fm':
            pool = r.get('pool', 0)

    # Token check
    token_ok = False
    try:
        import requests
        token = open(os.path.expanduser('~/news-trading/data/indmoney_token.txt')).read().strip()
        rr = requests.get('https://api.indstocks.com/user/profile',
                         headers={'Authorization': token}, timeout=5)
        token_ok = rr.status_code == 200
    except:
        pass

    # MCX bot
    mcx_running = os.popen('pgrep -f mcx_commodity_bot').read().strip() != ''

    # Alerts
    alerts = []
    if not token_ok:
        alerts.append({'level': 'red', 'msg': 'TOKEN EXPIRED — bots cannot trade!'})
    if not mcx_running:
        alerts.append({'level': 'yellow', 'msg': 'MCX commodity bot is not running'})
    for sh in strategy_health:
        if sh['consec_loss'] >= 3:
            alerts.append({'level': 'red', 'msg': f'{sh["name"]}: {sh["consec_loss"]} consecutive losses!'})
        if sh['wr'] < 55 and sh['trades'] >= 5:
            alerts.append({'level': 'red', 'msg': f'{sh["name"]}: WR {sh["wr"]}% — strategy may be dead'})
        if sh['wr'] < 70 and sh['trades'] >= 5:
            alerts.append({'level': 'yellow', 'msg': f'{sh["name"]}: WR {sh["wr"]}% — below normal'})
    if not alerts:
        alerts.append({'level': 'green', 'msg': 'All systems normal'})

    # TOTP status
    totp_status = 'OK'
    try:
        refresh_log = open(os.path.expanduser('~/news-trading/live/logs/token_refresh.log')).read()
        if 'FAILED' in refresh_log.split('\n')[-2]:
            totp_status = 'LAST REFRESH FAILED'
    except:
        totp_status = 'Unknown'

    last_refresh = ''
    try:
        stat = os.stat(os.path.expanduser('~/news-trading/data/indmoney_token.txt'))
        last_refresh = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
    except:
        pass

    return {
        'today_pnl': round(today_pnl), 'today_trades': len(today_trades), 'today_wr': today_wr,
        'week_pnl': round(week_pnl), 'week_trades': len(week_trades), 'week_wr': week_wr,
        'total_pnl': round(total_pnl), 'total_trades': len(trades),
        'pool': round(pool), 'deployed': round(deployed_total),
        'available': round(pool - deployed_total),
        'positions': positions,
        'strategy_health': strategy_health,
        'signals': signals[-50:],
        'daily_pnl': daily_list[-22:],
        'token_ok': token_ok,
        'mcx_running': mcx_running,
        'totp_status': totp_status,
        'last_refresh': last_refresh,
        'alerts': alerts,
    }


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == '/api/dashboard':
            data = compute_dashboard()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML.encode())


if __name__ == '__main__':
    print(f'Dashboard: http://35.238.32.244:{PORT}')
    server = http.server.HTTPServer(('0.0.0.0', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
