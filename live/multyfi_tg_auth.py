"""Multyfi Telegram auth — tap /refresh to renew token.

Checks token once per hour. Alerts ONCE if expired. Stops alerting until fixed.
"""
import json
import os
import time
import threading
import requests
from pathlib import Path
import fcntl

TG_TOKEN = '8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
TG_CHAT = '866752968'
MOBILE = '918431126627'
CREDS_FILE = Path(os.path.expanduser('~/multyfi_creds.json'))

# PID lock
LOCK = os.path.expanduser('~/multyfi_tg_auth.lock')
_lock_fp = open(LOCK, 'w')
try:
    fcntl.flock(_lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lock_fp.write(str(os.getpid()))
    _lock_fp.flush()
except BlockingIOError:
    print('Already running')
    exit(0)

BASE = 'https://api.telegram.org/bot' + TG_TOKEN
_otp_req_id = None
_waiting_otp = False
_last_update_id = 0
_alerted = False  # only alert once per expiry


def send(text):
    try:
        requests.post(f'{BASE}/sendMessage',
                      json={'chat_id': TG_CHAT, 'text': text}, timeout=10)
    except Exception:
        pass


def check_token():
    try:
        import urllib3
        urllib3.disable_warnings()
        creds = json.loads(CREDS_FILE.read_text())
        h = {'authtoken': creds.get('authToken', ''), 'mobile': creds.get('mobile', ''),
             'Content-Type': 'application/json'}
        r = requests.get('https://app.multyfi.com/api/futures/premium',
                         headers=h, timeout=15, verify=False)
        return r.status_code == 200
    except Exception:
        return False


def send_otp():
    global _otp_req_id, _waiting_otp
    try:
        r = requests.post('https://multyfi.com/api/auth/send/otp',
                          json={'mobile': MOBILE}, timeout=10)
        if r.status_code == 200:
            _otp_req_id = r.json().get('data', '')
            _waiting_otp = True
            send('OTP sent. Reply with 4-digit code.')
        else:
            send('OTP failed: ' + str(r.text[:100]))
    except Exception as e:
        send('Error: ' + str(e)[:80])


def verify_otp(code):
    global _waiting_otp, _alerted
    try:
        r = requests.post('https://multyfi.com/api/auth/verify/otp',
                          json={'mobile': MOBILE, 'otp': code, 'otpReqId': _otp_req_id},
                          timeout=10)
        if r.status_code == 200:
            token = r.json().get('data', {}).get('authToken')
            if token:
                CREDS_FILE.write_text(json.dumps({'authToken': token, 'mobile': MOBILE}, indent=2))
                _waiting_otp = False
                _alerted = False  # reset alert flag
                send('Token refreshed. Multyfi active.')
                return
        send('OTP wrong: ' + str(r.json().get('message', ''))[:80])
    except Exception as e:
        send('Error: ' + str(e)[:80])


def token_monitor():
    global _alerted
    while True:
        time.sleep(3600)  # 1 hour
        if not check_token() and not _alerted:
            _alerted = True
            send('Multyfi token EXPIRED. Tap /refresh')


def run():
    global _last_update_id
    print('Multyfi auth bot started')

    threading.Thread(target=token_monitor, daemon=True).start()

    while True:
        try:
            r = requests.get(f'{BASE}/getUpdates',
                             params={'offset': _last_update_id + 1, 'timeout': 30}, timeout=35)
            if r.status_code != 200:
                time.sleep(5)
                continue
            for u in r.json().get('result', []):
                _last_update_id = u['update_id']
                msg = u.get('message', {})
                if str(msg.get('chat', {}).get('id', '')) != TG_CHAT:
                    continue
                text = str(msg.get('text', '')).strip()
                if text == '/refresh':
                    send_otp()
                elif text == '/status':
                    ok = check_token()
                    send('Multyfi: ' + ('ACTIVE' if ok else 'EXPIRED. Tap /refresh'))
                elif _waiting_otp and text.isdigit() and len(text) == 4:
                    verify_otp(text)
        except Exception:
            time.sleep(5)


if __name__ == '__main__':
    run()
