from http.server import HTTPServer, BaseHTTPRequestHandler
import subprocess, os

class KillHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if '/kill' in self.path:
            subprocess.Popen(['bash', '/home/ai18developer/news-trading/kill_all.sh'])
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<h1>ALL BOTS KILLED</h1><p>Check Telegram for confirmation.</p>')
        else:
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<h1>Trading Bot Kill Switch</h1><a href="/kill" style="font-size:48px;color:red;padding:20px;border:3px solid red;text-decoration:none;">STOP ALL BOTS</a>')
    def log_message(self, *args): pass

HTTPServer(('0.0.0.0', 8900), KillHandler).serve_forever()
