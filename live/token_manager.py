"""Simple web page to update Upstox token — runs on port 8899."""
import http.server
import json
import os
from pathlib import Path
from datetime import datetime

PORT = 8899
UPSTOX_TOKEN_FILE = Path(__file__).parent.parent / 'data' / 'upstox_token.txt'
UPSTOX_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Trading Bot — Token Manager</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, sans-serif; background: #0a0a0a; color: #e0e0e0;
               display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        .container { width: 90%%; max-width: 600px; padding: 24px; }
        h1 { font-size: 22px; margin-bottom: 16px; color: #4ade80; }
        h2 { font-size: 16px; margin: 20px 0 8px; color: #60a5fa; }
        .status { font-size: 13px; color: #888; margin-bottom: 12px; }
        .status.ok { color: #4ade80; }
        .status.expired { color: #f87171; }
        textarea { width: 100%%; height: 80px; background: #1a1a1a; color: #e0e0e0;
                   border: 1px solid #333; border-radius: 8px; padding: 12px;
                   font-family: monospace; font-size: 11px; resize: none; }
        textarea:focus { outline: none; border-color: #4ade80; }
        button { width: 100%%; padding: 14px; margin-top: 12px; background: #4ade80;
                 color: #0a0a0a; border: none; border-radius: 8px; font-size: 16px;
                 font-weight: 600; cursor: pointer; }
        button:active { background: #22c55e; }
        .section { background: #111; border: 1px solid #222; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
        .result { margin-top: 12px; padding: 10px; border-radius: 8px; font-size: 13px; font-family: monospace; }
        .result.success { background: #052e16; color: #4ade80; border: 1px solid #166534; }
        .result.error { background: #2a0a0a; color: #f87171; border: 1px solid #7f1d1d; }
        a.link { display: block; width: 100%%; padding: 12px; margin-top: 8px; background: #1a1a1a;
                 color: #60a5fa; border: 1px solid #333; border-radius: 8px; text-align: center;
                 text-decoration: none; font-size: 14px; }
        .info { font-size: 12px; color: #666; margin-top: 12px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Trading Bot — Token Manager</h1>

        <div class="section">
            <h2>INDmoney Token</h2>
            <div class="status" id="ind-status">Checking...</div>
            <a class="link" href="https://www.indstocks.com/app/api-trading/access-tokens" target="_blank">
                Open INDstocks → Get Token
            </a>
            <textarea id="ind-token" placeholder="Paste INDmoney token here..."></textarea>
            <button onclick="updateToken('indmoney')">Update INDmoney Token</button>
            <button onclick="autoRefresh('indmoney')" style="background:#60a5fa;margin-top:8px;">
                Auto-Refresh (TOTP)
            </button>
            <div id="ind-result"></div>
        </div>

        <div class="section">
            <h2>Upstox Token</h2>
            <div class="status" id="upstox-status">Checking...</div>
            <a class="link" href="https://login.upstox.com" target="_blank">
                Open Upstox → Login → Get Token
            </a>
            <textarea id="upstox-token" placeholder="Paste Upstox access token here..."></textarea>
            <button onclick="updateToken('upstox')" style="background:#f59e0b;">Update Upstox Token</button>
            <div id="upstox-result"></div>
        </div>

        <div class="section">
            <h2>Bot Status</h2>
            <div id="bot-status" style="font-size:13px;font-family:monospace;color:#aaa;">Loading...</div>
        </div>

        <div class="info">
            INDmoney: auto-refreshes at 8:55 AM via TOTP<br>
            Upstox: paste token daily before market (expires at midnight)
        </div>
    </div>
    <script>
        async function updateToken(broker) {
            const token = document.getElementById(broker === 'indmoney' ? 'ind-token' : 'upstox-token').value.trim();
            if (!token || token.length < 20) {
                showResult(broker, 'Token too short', 'error');
                return;
            }
            try {
                const r = await fetch('/update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({broker: broker, token: token})
                });
                const d = await r.json();
                showResult(broker, d.message, d.success ? 'success' : 'error');
                if (d.success) {
                    document.getElementById(broker === 'indmoney' ? 'ind-token' : 'upstox-token').value = '';
                    setTimeout(checkStatus, 2000);
                }
            } catch(e) {
                showResult(broker, 'Error: ' + e.message, 'error');
            }
        }

        async function autoRefresh(broker) {
            try {
                const r = await fetch('/auto-refresh', {method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({broker: broker})});
                const d = await r.json();
                showResult(broker, d.message, d.success ? 'success' : 'error');
                setTimeout(checkStatus, 3000);
            } catch(e) {
                showResult(broker, 'Error: ' + e.message, 'error');
            }
        }

        function showResult(broker, msg, type) {
            const el = document.getElementById(broker === 'indmoney' ? 'ind-result' : 'upstox-result');
            el.textContent = msg;
            el.className = 'result ' + type;
        }

        async function checkStatus() {
            try {
                const r = await fetch('/status');
                const d = await r.json();
                // INDmoney
                const ind = document.getElementById('ind-status');
                if (d.indmoney_funds > 0) {
                    ind.textContent = 'Token OK | Funds: Rs ' + d.indmoney_funds.toLocaleString();
                    ind.className = 'status ok';
                } else {
                    ind.textContent = 'Token expired or market closed';
                    ind.className = 'status expired';
                }
                // Upstox
                const upstox = document.getElementById('upstox-status');
                upstox.textContent = d.upstox_status || 'Not configured';
                upstox.className = 'status ' + (d.upstox_ok ? 'ok' : 'expired');
                // Bots
                document.getElementById('bot-status').textContent = d.bots || 'No bots info';
            } catch(e) {
                document.getElementById('ind-status').textContent = 'Cannot reach server';
            }
        }
        checkStatus();
        setInterval(checkStatus, 30000);
    </script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _html(self, html):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def do_GET(self):
        if self.path == '/status':
            self._handle_status()
        else:
            self._html(HTML)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == '/update':
            self._handle_update(body)
        elif self.path == '/auto-refresh':
            self._handle_auto_refresh(body)
        else:
            self._json({'error': 'not found'}, 404)

    def _handle_status(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))

        # INDmoney
        ind_funds = 0
        try:
            from live import indmoney_client as api
            ind_funds = api.get_funds()
        except:
            pass

        # Upstox
        upstox_ok = False
        upstox_status = 'Not configured'
        if UPSTOX_TOKEN_FILE.exists():
            token = UPSTOX_TOKEN_FILE.read_text().strip()
            if token:
                try:
                    import requests
                    r = requests.get('https://api.upstox.com/v2/user/get-funds-and-margin',
                                   headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                                   timeout=10)
                    if r.status_code == 200:
                        upstox_ok = True
                        upstox_status = 'Token OK'
                    else:
                        upstox_status = f'Token expired ({r.status_code})'
                except:
                    upstox_status = 'Connection error'

        # Bots
        import subprocess
        bots = []
        for name, grep in [('News Bot', 'indian_news_bot'), ('IPO', 'ipo_trader'),
                          ('Commodity', 'gold_bot'), ('US News', 'us_news_bot')]:
            r = subprocess.run(['pgrep', '-f', grep], capture_output=True)
            status = 'RUNNING' if r.returncode == 0 else 'idle'
            bots.append(f'{name}: {status}')

        self._json({
            'indmoney_funds': ind_funds,
            'upstox_ok': upstox_ok,
            'upstox_status': upstox_status,
            'bots': ' | '.join(bots),
        })

    def _handle_update(self, body):
        broker = body.get('broker', '')
        token = body.get('token', '').strip()

        if not token or len(token) < 20:
            self._json({'success': False, 'message': 'Token too short'})
            return

        if broker == 'indmoney':
            ind_file = Path(__file__).parent.parent / 'data' / 'indmoney_token.txt'
            ind_file.write_text(token)
            self._json({'success': True, 'message': 'INDmoney token updated'})

        elif broker == 'upstox':
            UPSTOX_TOKEN_FILE.write_text(token)
            self._json({'success': True, 'message': 'Upstox token updated'})

        else:
            self._json({'success': False, 'message': f'Unknown broker: {broker}'})

    def _handle_auto_refresh(self, body):
        broker = body.get('broker', '')
        if broker == 'indmoney':
            try:
                import subprocess
                r = subprocess.run(
                    ['/home/ai18developer/news-trading/live/refresh_token.sh'],
                    capture_output=True, text=True, timeout=30
                )
                if 'Token:' in r.stdout:
                    self._json({'success': True, 'message': 'INDmoney token auto-refreshed via TOTP'})
                else:
                    self._json({'success': False, 'message': f'Refresh failed: {r.stdout[:100]}'})
            except Exception as e:
                self._json({'success': False, 'message': str(e)})
        else:
            self._json({'success': False, 'message': 'Auto-refresh only for INDmoney'})


if __name__ == '__main__':
    print(f'Token Manager running on port {PORT}')
    print(f'Open: http://35.238.32.244:{PORT}')
    server = http.server.HTTPServer(('0.0.0.0', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('Stopped')
        server.shutdown()
