"""Auto-refresh INDmoney access token using TOTP.
Called by cron at 8:55 AM daily before market opens.

Flow: client_id + TOTP code → new access token → save to file
"""
import pyotp
import requests
import json
import sys
import time
from pathlib import Path
from datetime import datetime

# Config
TOTP_SECRET = "MCHLRP6JPELHYMNZTSUBMSBW36MTAJZ2"
CLIENT_ID = "5QnEBdpgG00xmbGOpI5d"
MPIN = "3001"
TOKEN_FILE = Path("/home/ai18developer/news-trading/data/indmoney_token.txt")
BASE = "https://api.indstocks.com"

TG_TOKEN = '8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
TG_CHAT = '866752968'


def send_tg(msg):
    try:
        requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
                      json={'chat_id': TG_CHAT, 'text': msg}, timeout=5)
    except:
        pass


def generate_totp():
    """Generate current TOTP code."""
    totp = pyotp.TOTP(TOTP_SECRET)
    return totp.now()


def refresh_token():
    """Generate new access token using TOTP + client ID + MPIN."""
    totp_code = generate_totp()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] TOTP code: {totp_code}")

    # INDmoney endpoint: POST /generate/token
    # Headers: x-api-key = client ID
    # Body: mpin + totp
    headers = {
        'x-api-key': CLIENT_ID,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    payload = json.dumps({
        'mpin': MPIN,
        'totp': totp_code,
    })

    for endpoint in [
        f'{BASE}/generate/token',
        f'{BASE}/trading/generate/token',
        f'{BASE}/v1/generate/token',
    ]:
        try:
            r = requests.post(endpoint, data=payload, headers=headers, timeout=30)
            print(f"  {endpoint}: {r.status_code} {r.text[:300]}")
            if r.status_code in (200, 201):
                data = r.json()
                token = (data.get('data', {}).get('token') or
                         data.get('token') or
                         data.get('access_token') or
                         data.get('data', {}).get('access_token'))
                if token:
                    return token
        except Exception as e:
            print(f"  {endpoint}: ERROR {e}")

    return None


def verify_token(token):
    """Verify token works by checking funds."""
    try:
        r = requests.get(f'{BASE}/user/profile',
                        headers={'Authorization': token},
                        timeout=10)
        return r.status_code == 200
    except:
        return False


def main():
    print(f"=== INDmoney Token Refresh === {datetime.now()}")

    token = refresh_token()
    if token:
        # Verify
        if verify_token(token):
            TOKEN_FILE.write_text(token)
            print(f"Token refreshed and saved!")
            send_tg("Token refreshed. All bots active.")
            return token
        else:
            print("Token generated but verification failed!")
            send_tg("Token generated but INVALID. Check manually.")
    else:
        print("Token refresh FAILED!")
        send_tg("TOKEN REFRESH FAILED! Paste token manually: http://35.238.32.244:8899")

    return None


if __name__ == '__main__':
    token = main()
    sys.exit(0 if token else 1)
