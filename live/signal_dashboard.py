"""Live Signal Dashboard — shows commodity bot signals in real-time."""
import http.server
import json
import os
from pathlib import Path
from datetime import datetime

PORT = 8900
LOG_DIR = Path('/home/ai18developer/news-trading/live/logs')

HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Trading Signals — Live</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="30">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, sans-serif; background: #0a0a0a; color: #e0e0e0; padding: 16px; }
        h1 { font-size: 20px; color: #4ade80; margin-bottom: 16px; }
        h2 { font-size: 16px; color: #60a5fa; margin: 20px 0 8px; border-bottom: 1px solid #222; padding-bottom: 4px; }
        .card { background: #111; border: 1px solid #222; border-radius: 10px; padding: 14px; margin-bottom: 10px; }
        .trade { border-left: 4px solid #4ade80; }
        .trade.sell { border-left-color: #f87171; }
        .skip { border-left: 4px solid #666; opacity: 0.6; }
        .symbol { font-size: 18px; font-weight: 700; }
        .buy { color: #4ade80; }
        .sell { color: #f87171; }
        .proj { font-size: 14px; color: #fbbf24; }
        .why { font-size: 12px; color: #888; margin-top: 4px; }
        .time { font-size: 11px; color: #555; }
        .section { margin-bottom: 24px; }
        .status { font-size: 13px; padding: 8px; background: #111; border-radius: 8px; margin-bottom: 16px; }
        .status.ok { border: 1px solid #166534; color: #4ade80; }
        .status.warn { border: 1px solid #854d0e; color: #fbbf24; }
        .no-signal { color: #555; font-style: italic; padding: 10px; }
        .refresh { font-size: 11px; color: #444; text-align: center; margin-top: 16px; }
    </style>
</head>
<body>
    <h1>Trading Signals</h1>
    <div class="status %STATUS_CLASS%">%STATUS_TEXT%</div>

    <div class="section">
        <h2>Commodity Signals (Gold + Crude + Crypto)</h2>
        %COMMODITY_SIGNALS%
    </div>

    <div class="section">
        <h2>Indian Stock Signals</h2>
        %STOCK_SIGNALS%
    </div>

    <div class="section">
        <h2>IPO Listings</h2>
        %IPO_SIGNALS%
    </div>

    <div class="section">
        <h2>Today's Executed Trades</h2>
        %EXECUTED%
    </div>

    <div class="refresh">Auto-refreshes every 30 seconds | %TIME%</div>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == '/api/signals':
            self._json_signals()
        else:
            self._html_dashboard()

    def _json_signals(self):
        data = self._get_signals()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _html_dashboard(self):
        signals = self._get_signals()

        # Status
        now = datetime.now()
        if 9 <= now.hour < 15 or (now.hour == 15 and now.minute <= 30):
            status_text = "NSE Market OPEN | MCX Open until 11:30 PM"
            status_class = "ok"
        elif now.hour < 9:
            status_text = "Pre-market | MCX opens 9:00 AM | NSE opens 9:15 AM"
            status_class = "warn"
        elif now.hour >= 23 and now.minute > 30:
            status_text = "All markets closed"
            status_class = "warn"
        else:
            status_text = "NSE Closed | MCX Open until 11:30 PM"
            status_class = "ok"

        # Commodity signals
        comm_html = ""
        if signals["commodity"]:
            for s in signals["commodity"]:
                direction = s.get("direction", "?")
                css_class = "trade sell" if direction == "SELL" else "trade"
                dir_class = "sell" if direction == "SELL" else "buy"
                cname = s.get("commodity", "")
                if not cname:
                    txt = (s.get("dominant","") + s.get("why","")).lower()
                    if any(w in txt for w in ["crude","oil","hormuz","opec","barrel","tanker"]):
                        cname = "CRUDE OIL"
                    elif any(w in txt for w in ["btc","bitcoin","crypto","eth"]):
                        cname = "CRYPTO"
                    else:
                        cname = "GOLD"
                comm_html += "<div class=\"card " + css_class + "\">"
                comm_html += "<span class=\"symbol\">" + cname + "</span> "
                comm_html += "<span class=\"" + dir_class + "\"> " + direction + "</span> "
                comm_html += "<span class=\"proj\">proj: " + str(s.get("projection", "?")) + "</span>"
                comm_html += "<div class=\"why\">" + str(s.get("dominant", "")) + "</div>"
                comm_html += "<div class=\"why\">" + str(s.get("why", ""))[:150] + "</div>"
                if cname == "CRUDE OIL":
                    act = "Buy CRUDEOILM nearest expiry ATM " + ("CE" if direction == "BUY" else "PE") + " | SL: -30% | Trail: +20%/10% | Exit 11:30PM"
                elif cname == "GOLD":
                    act = "Buy GOLDM nearest expiry ATM " + ("CE" if direction == "BUY" else "PE") + " | SL: -30% | Trail: +20%/10% | Exit 11:30PM"
                elif cname == "CRYPTO":
                    act = "BTC " + direction + " on exchange | SL: -30% | Trail: +20%/10%"
                else:
                    act = ""
                if act:
                    comm_html += '<div class="why" style="color:#fbbf24;font-weight:bold">' + act + '</div>'
                comm_html += "<div class=\"time\">" + str(s.get("time", "")) + "</div>"
                comm_html += "</div>"
        else:
            comm_html = "<div class=\"no-signal\">No commodity signals yet today</div>"

        # Stock signals
        stock_html = ""
        if signals['stocks']:
            for s in signals['stocks']:
                direction = s.get('call', '?')
                css_class = 'trade sell' if direction == 'SELL' else 'trade'
                dir_class = 'sell' if direction == 'SELL' else 'buy'
                stock_html += f'''<div class="card {css_class}">
                    <span class="symbol">{s.get('symbol', '?')}</span>
                    <span class="{dir_class}"> {direction}</span>
                    <span class="proj">proj: {s.get('projection', '?')}%</span>
                    <div class="why">{s.get('why', '')[:150]}</div>
                </div>'''
        else:
            stock_html = '<div class="no-signal">No stock signals yet today</div>'

        # IPO
        ipo_html = ""
        if signals['ipo']:
            for s in signals['ipo']:
                ipo_html += f'''<div class="card trade">
                    <span class="symbol">{s.get('symbol', '?')}</span>
                    <span class="buy"> IPO LISTING</span>
                    <div class="why">{s.get('companyName', s.get('symbol','?'))} | Issue: Rs {s.get('issuePrice', '?')} | Sub: {s.get('noOfTime', '?')}x</div>
                </div>'''
        else:
            ipo_html = '<div class="no-signal">No IPO listings today</div>'

        # Executed
        exec_html = ""
        if signals['executed']:
            for s in signals['executed']:
                pnl = s.get('pnl', 0)
                color = '#4ade80' if pnl > 0 else '#f87171' if pnl < 0 else '#888'
                exec_html += f'''<div class="card">
                    <span class="symbol">{s.get('symbol', '?')}</span>
                    <span style="color:{color}"> Rs {pnl:+,.0f}</span>
                    <div class="why">{s.get('action', '')} | Entry: {s.get('entry', '')} Exit: {s.get('exit', '')}</div>
                </div>'''
        else:
            exec_html = '<div class="no-signal">No executed trades yet today</div>'

        html = HTML.replace('%STATUS_TEXT%', status_text)
        html = html.replace('%STATUS_CLASS%', status_class)
        html = html.replace('%COMMODITY_SIGNALS%', comm_html)
        html = html.replace('%STOCK_SIGNALS%', stock_html)
        html = html.replace('%IPO_SIGNALS%', ipo_html)
        html = html.replace('%EXECUTED%', exec_html)
        html = html.replace('%TIME%', now.strftime('%H:%M:%S IST'))

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def _get_signals(self):
        today = datetime.now().strftime('%Y%m%d')
        date_str = datetime.now().strftime('%Y-%m-%d')
        signals = {'commodity': [], 'stocks': [], 'ipo': [], 'executed': []}

        # Commodity signals from JSON log
        comm_file = LOG_DIR / f'commodity_{date_str}.json'
        if comm_file.exists():
            try:
                data = json.loads(comm_file.read_text())
                # Get latest TRADE signals
                for entry in data:
                    if entry.get('decision') == 'TRADE':
                        signals['commodity'].append(entry)
                # Keep only last 5
                signals['commodity'] = signals['commodity'][-5:]
            except:
                pass

        # Stock signals from log file
        stock_log = LOG_DIR / f'options_{today}.log'
        if stock_log.exists():
            try:
                for line in stock_log.read_text().split('\n'):
                    if '  TRADE:' in line and 'proj=' in line:
                        # Parse: TRADE: HFCL BUY proj=+5.0%
                        import re
                        m = re.search(r'TRADE: (\S+) (BUY|SELL) proj=([+\-\d.]+)%.*\| (.+)', line)
                        if m:
                            signals['stocks'].append({
                                'symbol': m.group(1),
                                'call': m.group(2),
                                'projection': m.group(3),
                                'why': m.group(4)[:150],
                            })
                # Dedup by symbol, keep last
                seen = {}
                for s in signals['stocks']:
                    seen[s['symbol']] = s
                signals['stocks'] = list(seen.values())[-10:]
            except:
                pass

        # IPO signals
        ipo_log = LOG_DIR / f'ipo_{today}.log'
        if ipo_log.exists():
            try:
                import re as _re
                lines_ipo = ipo_log.read_text().split(chr(10))
                for j, line in enumerate(lines_ipo):
                    if "IPO:" in line and "(" in line:
                        m = _re.search(r"IPO:\s*(.+?)\s*\(([^)]+)\)", line)
                        if m:
                            entry = {"companyName": m.group(1).strip(), "symbol": m.group(2).strip(), "issuePrice": "?", "noOfTime": "?"}
                            if j+1 < len(lines_ipo) and "Price:" in lines_ipo[j+1]:
                                pm = _re.search(r"Price:\s*(\S+).*Sub:\s*(\S+)", lines_ipo[j+1])
                                if pm:
                                    entry["issuePrice"] = pm.group(1)
                                    entry["noOfTime"] = pm.group(2)
                            signals["ipo"].append(entry)
            except:
                pass

        # IPO JSON
        ipo_json = LOG_DIR / f'ipo_{date_str}.json'
        if ipo_json.exists():
            try:
                data = json.loads(ipo_json.read_text())
                if isinstance(data, list):
                    signals['ipo'] = data
            except:
                pass

        # Executed trades from positions
        try:
            import sys
            sys.path.insert(0, '/home/ai18developer/news-trading')
            from live import indmoney_client as api
            import requests
            token = api.get_token()
            h = {'Authorization': token}
            r = requests.get('https://api.indstocks.com/portfolio/positions?segment=equity&product=intraday',
                           headers=h, timeout=5)
            if r.status_code == 200:
                for p in r.json().get('data', []):
                    if p.get('realized_profit', 0) != 0 or p.get('net_qty', 0) != 0:
                        signals['executed'].append({
                            'symbol': p.get('symbol', '?'),
                            'pnl': p.get('realized_profit', 0),
                            'action': f"Buy {p.get('buy_qty',0)} @ {p.get('buy_avg',0):.1f}",
                            'entry': f"{p.get('buy_avg',0):.1f}",
                            'exit': f"{p.get('sell_avg',0):.1f}",
                        })
        except:
            pass

        return signals


if __name__ == '__main__':
    print(f'Signal Dashboard on port {PORT}')
    print(f'Open: http://35.238.32.244:{PORT}')
    server = http.server.HTTPServer(('0.0.0.0', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
