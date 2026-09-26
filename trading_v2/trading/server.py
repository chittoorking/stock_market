"""HTTP Server — one server, routes to agents.

Routes:
  POST /notify     — route to appropriate agent
  POST /exit       — force exit by symbol
  GET  /health     — alive check
  GET  /status     — FM + positions
  GET  /positions  — active positions
"""
import http.server
import json
from datetime import datetime
from urllib.parse import urlparse

from trading.logger import get_logger, audit

log = get_logger('server')


class TradingServer:
    def __init__(self, port: int = 8905):
        self.port = port
        self._agents = {}
        self._fm = None
        self._pos = None
        self._monitor = None

    def set_core(self, fund_manager, positions, monitor):
        self._fm = fund_manager
        self._pos = positions
        self._monitor = monitor

    def register(self, agent):
        self._agents[agent.name] = agent
        log.info(f'Registered: {agent.name}')

    def start(self):
        ref = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path = urlparse(self.path).path
                if path == '/health':
                    self._j({'ok': True, 'agents': list(ref._agents.keys()),
                             'time': datetime.now().isoformat()})
                elif path == '/status':
                    status = ref._fm.status() if ref._fm else {}
                    status['positions'] = [
                        {'id': pos.id, 'sym': pos.symbol, 'side': pos.side.value,
                         'qty': pos.qty, 'entry': pos.entry_price, 'sl': pos.sl,
                         'target': pos.target, 'strategy': pos.strategy,
                         'opened': pos.opened_at}
                        for pos in ref._pos.active()
                    ] if ref._pos else []
                    self._j(status)
                elif path == '/positions':
                    positions = [
                        {'id': pos.id, 'sym': pos.symbol, 'qty': pos.qty,
                         'entry': pos.entry_price, 'sl': pos.sl, 'strategy': pos.strategy}
                        for pos in ref._pos.active()
                    ] if ref._pos else []
                    self._j({'trades': positions})
                else:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html')
                    self.end_headers()
                    self.wfile.write(b'<h1>Trading System v2</h1>')

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()
                path = urlparse(self.path).path

                if path.startswith('/notify'):
                    self._notify(body)
                elif path == '/exit':
                    self._force_exit(body)
                else:
                    self._j({'error': 'unknown endpoint'})

            def _notify(self, body):
                try:
                    sig = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    sig = {'text': body}

                # All notifications route to equity_notif
                agent = ref._agents.get('equity_notif')
                if not agent:
                    self._j({'error': 'equity_notif agent not loaded'})
                    return

                # Log full payload for debugging
                import json as _j
                log.info(f'RAW PAYLOAD: {_j.dumps(sig)[:500]}')
                raw_text = str(sig.get('text', sig.get('body', sig.get('stock', ''))))
                audit('server', 'NOTIFY', text=raw_text[:300])
                result = agent.on_signal(sig)
                self._j(result)

            def _force_exit(self, body):
                try:
                    sym = json.loads(body).get('symbol', '').upper()
                except Exception:
                    self._j({'error': 'need JSON with symbol field'})
                    return
                if not sym:
                    self._j({'error': 'symbol is empty'})
                    return
                ok = ref._monitor.force_exit(sym, 'HTTP_EXIT') if ref._monitor else False
                self._j({'symbol': sym, 'exited': ok})

            def _j(self, data):
                b = json.dumps(data, default=str).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.send_header('Content-Length', str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_OPTIONS(self):
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.end_headers()

        log.info(f'Server on port {self.port}')
        http.server.ThreadingHTTPServer(('0.0.0.0', self.port), H).serve_forever()
