"""Simple token update web server.
Visit http://SERVER:8899 → paste token → bot restarts with new token.
Run: venv/bin/python token_updater.py
"""
import http.server
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from datetime import datetime

PORT = 8899
TOKEN_FILE = Path(__file__).parent / 'data' / 'indmoney_token.txt'
BOT_SCRIPT = 'live.basket_trader'

HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
    <title>Trading Bot Token Update</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, sans-serif; background: #0a0a0a; color: #e0e0e0;
               display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        .container { width: 90%%; max-width: 500px; padding: 24px; }
        h1 { font-size: 20px; margin-bottom: 8px; color: #4ade80; }
        .status { font-size: 13px; color: #888; margin-bottom: 20px; }
        .status.ok { color: #4ade80; }
        .status.expired { color: #f87171; }
        textarea { width: 100%%; height: 80px; background: #1a1a1a; color: #e0e0e0;
                   border: 1px solid #333; border-radius: 8px; padding: 12px;
                   font-family: monospace; font-size: 12px; resize: none; }
        textarea:focus { outline: none; border-color: #4ade80; }
        button { width: 100%%; padding: 14px; margin-top: 12px; background: #4ade80;
                 color: #0a0a0a; border: none; border-radius: 8px; font-size: 16px;
                 font-weight: 600; cursor: pointer; }
        button:active { background: #22c55e; }
        .result { margin-top: 16px; padding: 12px; border-radius: 8px;
                  font-size: 13px; font-family: monospace; white-space: pre-wrap; }
        .result.success { background: #052e16; color: #4ade80; border: 1px solid #166534; }
        .result.error { background: #2a0a0a; color: #f87171; border: 1px solid #7f1d1d; }
        .info { margin-top: 16px; font-size: 12px; color: #666; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Trading Bot</h1>
        <div class="status" id="status">Checking...</div>
        <textarea id="token" placeholder="Paste INDmoney token here..."></textarea>
        <button onclick="updateToken()">Update Token & Restart Bot</button>
        <div id="result"></div>
        <div style="display:flex;gap:8px;margin-top:12px;">
            <button onclick="stopBot()" style="flex:1;background:#f87171;padding:12px;font-size:14px;">Stop Bot</button>
            <button onclick="startBot()" style="flex:1;background:#60a5fa;padding:12px;font-size:14px;">Start Bot</button>
        </div>
        <a href="https://web.indstocks.com" target="_blank"
           style="display:block;width:100%%;padding:12px;margin-top:12px;background:#1a1a1a;
                  color:#60a5fa;border:1px solid #333;border-radius:8px;text-align:center;
                  text-decoration:none;font-size:14px;">
            Open INDstocks → Get Token
        </a>
        <div id="logs" style="margin-top:12px;max-height:200px;overflow-y:auto;background:#1a1a1a;
                              border:1px solid #333;border-radius:8px;padding:10px;font-size:11px;
                              font-family:monospace;color:#aaa;display:none;white-space:pre-wrap;"></div>
        <div id="predictions" style="margin-top:12px;background:#1a1a1a;border:1px solid #333;
                                       border-radius:8px;padding:10px;display:none;">
            <div style="font-size:13px;color:#4ade80;margin-bottom:8px;font-weight:600;">Live Predictions</div>
            <div id="pred-content" style="font-size:11px;font-family:monospace;color:#aaa;white-space:pre-wrap;"></div>
        </div>
        <div style="display:flex;gap:8px;margin-top:8px;">
            <button onclick="viewPredictions()" style="flex:1;background:#1a1a1a;
                    border:1px solid #333;padding:10px;color:#888;font-size:12px;">
                Predictions
            </button>
            <button onclick="viewLogs()" style="flex:1;background:#1a1a1a;
                    border:1px solid #333;padding:10px;color:#888;font-size:12px;">
                Logs
            </button>
        </div>
        <div class="info">
            Steps: Open INDstocks → Login → Copy token → Paste above → Update<br>
            Token expires daily at 7:00 AM. Update before 9:15 AM.
        </div>
    </div>
    <script>
        async function checkStatus() {
            try {
                const r = await fetch('/status');
                const d = await r.json();
                const el = document.getElementById('status');
                if (d.funds > 0) {
                    el.textContent = 'Token OK | Funds: Rs ' + d.funds.toLocaleString() +
                        ' | Bot: ' + d.bot_status + ' | Expires: ' + d.expires;
                    el.className = 'status ok';
                } else {
                    el.textContent = 'Token EXPIRED | Bot: ' + d.bot_status +
                        ' | Last updated: ' + d.last_updated;
                    el.className = 'status expired';
                }
            } catch(e) {
                document.getElementById('status').textContent = 'Cannot reach server';
            }
        }

        async function updateToken() {
            const token = document.getElementById('token').value.trim();
            if (!token || token.length < 50) {
                showResult('Token too short. Paste the full JWT.', 'error');
                return;
            }
            try {
                const r = await fetch('/update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({token: token})
                });
                const d = await r.json();
                if (d.success) {
                    showResult(d.message, 'success');
                    document.getElementById('token').value = '';
                    setTimeout(checkStatus, 3000);
                } else {
                    showResult(d.message, 'error');
                }
            } catch(e) {
                showResult('Error: ' + e.message, 'error');
            }
        }

        function showResult(msg, type) {
            const el = document.getElementById('result');
            el.textContent = msg;
            el.className = 'result ' + type;
        }

        async function stopBot() {
            try {
                const r = await fetch('/bot/stop', {method:'POST'});
                const d = await r.json();
                showResult(d.message, d.success ? 'success' : 'error');
                setTimeout(checkStatus, 2000);
            } catch(e) { showResult('Error: '+e.message, 'error'); }
        }

        async function startBot() {
            try {
                const r = await fetch('/bot/start', {method:'POST'});
                const d = await r.json();
                showResult(d.message, d.success ? 'success' : 'error');
                setTimeout(checkStatus, 3000);
            } catch(e) { showResult('Error: '+e.message, 'error'); }
        }

        async function viewLogs() {
            const el = document.getElementById('logs');
            if (el.style.display === 'none') {
                try {
                    const r = await fetch('/logs');
                    const d = await r.json();
                    el.textContent = d.logs;
                    el.style.display = 'block';
                    el.scrollTop = el.scrollHeight;
                } catch(e) { el.textContent = 'Error loading logs'; el.style.display = 'block'; }
            } else {
                el.style.display = 'none';
            }
        }

        async function viewPredictions() {
            const el = document.getElementById('predictions');
            const content = document.getElementById('pred-content');
            if (el.style.display === 'none') {
                try {
                    const r = await fetch('/predictions');
                    const d = await r.json();
                    let html = '';
                    if (d.trades && d.trades.length > 0) {
                        html += 'ENTRIES:\\n';
                        d.trades.forEach(t => {
                            // Highlight P(win) value
                            html += t.replace(/P\(win\)=(\d+%[^)]*\))/g,
                                '<span style="color:#4ade80">P(win)=$1</span>') + '\\n';
                        });
                    } else {
                        html += 'No predictions yet (trades start at 9:15 AM)\\n';
                    }
                    if (d.positions && d.positions.length > 0) {
                        html += '\\nEXITS:\\n';
                        d.positions.forEach(p => { html += p + '\\n'; });
                    }
                    content.innerHTML = html;
                    el.style.display = 'block';
                } catch(e) { content.textContent = 'Error: '+e.message; el.style.display='block'; }
            } else {
                el.style.display = 'none';
            }
        }

        checkStatus();
        setInterval(checkStatus, 30000);
    </script>
</body>
</html>"""


class TokenHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default logging

    def _send_json(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_html(self, html):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def do_GET(self):
        if self.path == '/status':
            self._handle_status()
        elif self.path == '/logs':
            self._handle_logs()
        elif self.path == '/predictions':
            self._handle_predictions()
        else:
            self._send_html(HTML_PAGE)

    def do_POST(self):
        if self.path == '/update':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            self._handle_update(body)
        elif self.path == '/bot/stop':
            self._handle_bot_stop()
        elif self.path == '/bot/start':
            self._handle_bot_start()
        else:
            self._send_json({'error': 'not found'}, 404)

    def _handle_status(self):
        # Check token validity
        funds = 0
        expires = '?'
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from live import indmoney_client as api
            funds = api.get_funds()

            # Decode JWT expiry
            import base64
            token = TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else ''
            if token:
                parts = token.split('.')
                if len(parts) == 3:
                    payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
                    data = json.loads(base64.urlsafe_b64decode(payload))
                    exp_dt = datetime.fromtimestamp(data.get('exp', 0))
                    expires = exp_dt.strftime('%H:%M %b %d')
        except Exception:
            pass

        # Check bot process
        try:
            result = subprocess.run(['pgrep', '-f', BOT_SCRIPT], capture_output=True, text=True)
            bot_running = result.returncode == 0
        except Exception:
            bot_running = False

        # Last modified time of token file
        last_updated = '?'
        if TOKEN_FILE.exists():
            mtime = datetime.fromtimestamp(TOKEN_FILE.stat().st_mtime)
            last_updated = mtime.strftime('%H:%M %b %d')

        self._send_json({
            'funds': funds,
            'bot_status': 'RUNNING' if bot_running else 'STOPPED',
            'expires': expires,
            'last_updated': last_updated,
        })

    def _handle_update(self, body):
        token = body.get('token', '').strip()
        if not token or len(token) < 50:
            self._send_json({'success': False, 'message': 'Invalid token'})
            return

        try:
            # Save token
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(token)

            # Verify token works
            sys.path.insert(0, str(Path(__file__).parent))
            # Force reimport to pick up new token
            if 'live.indmoney_client' in sys.modules:
                del sys.modules['live.indmoney_client']
            from live import indmoney_client as api
            funds = api.get_funds()

            if funds <= 0:
                self._send_json({
                    'success': False,
                    'message': f'Token saved but funds=0. Token may be invalid or market closed.'
                })
                return

            # Kill existing bot
            subprocess.run(['pkill', '-f', BOT_SCRIPT], capture_output=True)
            import time
            time.sleep(2)

            # Start new bot
            bot_dir = str(Path(__file__).parent)
            venv_python = str(Path(__file__).parent / 'venv' / 'bin' / 'python')
            subprocess.Popen(
                [venv_python, '-m', BOT_SCRIPT],
                cwd=bot_dir,
                stdout=open(os.path.join(bot_dir, 'basket_bot.log'), 'w'),
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

            self._send_json({
                'success': True,
                'message': f'Token updated. Funds: Rs {funds:,.0f}. Bot restarted.'
            })

        except Exception as e:
            self._send_json({
                'success': False,
                'message': f'Error: {str(e)}'
            })


    def _handle_bot_stop(self):
        try:
            result = subprocess.run(['pkill', '-f', BOT_SCRIPT], capture_output=True)
            if result.returncode == 0:
                self._send_json({'success': True, 'message': 'Bot stopped.'})
            else:
                self._send_json({'success': True, 'message': 'Bot was not running.'})
        except Exception as e:
            self._send_json({'success': False, 'message': f'Error: {e}'})

    def _handle_bot_start(self):
        try:
            # Check if already running
            result = subprocess.run(['pgrep', '-f', BOT_SCRIPT], capture_output=True, text=True)
            if result.returncode == 0:
                self._send_json({'success': False, 'message': 'Bot is already running.'})
                return

            bot_dir = str(Path(__file__).parent)
            venv_python = str(Path(__file__).parent / 'venv' / 'bin' / 'python')
            subprocess.Popen(
                [venv_python, '-m', BOT_SCRIPT],
                cwd=bot_dir,
                stdout=open(os.path.join(bot_dir, 'basket_bot.log'), 'w'),
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self._send_json({'success': True, 'message': 'Bot started.'})
        except Exception as e:
            self._send_json({'success': False, 'message': f'Error: {e}'})

    def _handle_logs(self):
        try:
            log_file = Path(__file__).parent / 'basket_bot.log'
            if log_file.exists():
                lines = log_file.read_text().splitlines()
                tail = '\n'.join(lines[-50:])
                self._send_json({'logs': tail})
            else:
                self._send_json({'logs': 'No log file found.'})
        except Exception as e:
            self._send_json({'logs': f'Error: {e}'})

    def _handle_predictions(self):
        """Show today's trade predictions from the log."""
        try:
            log_file = Path(__file__).parent / 'basket_bot.log'
            if not log_file.exists():
                self._send_json({'trades': [], 'message': 'No log file'})
                return
            lines = log_file.read_text().splitlines()
            # Find lines with P(win) predictions
            trades = []
            for line in lines:
                if 'P(win)=' in line:
                    trades.append(line.strip())
            # Also find position updates
            positions = []
            for line in lines[-100:]:
                if 'STOP LOSS' in line or 'TRAIL' in line or 'FILL' in line or 'FLIP' in line:
                    positions.append(line.strip())
            self._send_json({
                'trades': trades[-10:],
                'positions': positions[-10:],
            })
        except Exception as e:
            self._send_json({'trades': [], 'message': str(e)})


if __name__ == '__main__':
    print(f'Token updater running on port {PORT}')
    print(f'Open: http://136.111.68.229:{PORT}')
    server = http.server.HTTPServer(('0.0.0.0', PORT), TokenHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down')
        server.shutdown()
