"""OAuth2 token flow for Upstox — run once daily before market opens."""
import sys
import io
import webbrowser
import requests
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from . import config
from . import upstox_client as api

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

log = logging.getLogger('auth')


class CallbackHandler(BaseHTTPRequestHandler):
    """Handle OAuth2 callback."""
    token = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        code = query.get('code', [None])[0]

        if code:
            # Exchange code for access token
            token = exchange_code(code)
            if token:
                CallbackHandler.token = token
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(b'<h1>Token received! You can close this window.</h1>')
            else:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'Token exchange failed')
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'No code received')

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


def exchange_code(code):
    """Exchange auth code for access token."""
    try:
        r = requests.post(f'{config.UPSTOX_BASE}/login/authorization/token', data={
            'code': code,
            'client_id': config.UPSTOX_API_KEY,
            'client_secret': config.UPSTOX_API_SECRET,
            'redirect_uri': config.UPSTOX_REDIRECT_URI,
            'grant_type': 'authorization_code',
        }, timeout=15)
        if r.status_code == 200:
            data = r.json()
            token = data.get('access_token')
            if token:
                api.save_token(token)
                log.info(f'Token saved to {config.TOKEN_FILE}')
                return token
        log.error(f'Token exchange failed: {r.text[:200]}')
    except Exception as e:
        log.error(f'Token exchange error: {e}')
    return None


def login():
    """Open browser for Upstox login, get token via OAuth2 callback."""
    if not config.UPSTOX_API_KEY:
        print('ERROR: Set UPSTOX_API_KEY environment variable')
        return None

    auth_url = (f'https://api.upstox.com/v2/login/authorization/dialog?'
                f'response_type=code&client_id={config.UPSTOX_API_KEY}'
                f'&redirect_uri={config.UPSTOX_REDIRECT_URI}')

    print(f'Opening browser for Upstox login...')
    print(f'URL: {auth_url}')
    webbrowser.open(auth_url)

    # Start callback server
    server = HTTPServer(('localhost', 5000), CallbackHandler)
    print('Waiting for callback on http://localhost:5000/callback ...')
    server.handle_request()  # Handle single request

    return CallbackHandler.token


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    token = login()
    if token:
        print(f'Access token: {token[:20]}...')
    else:
        print('Login failed')
