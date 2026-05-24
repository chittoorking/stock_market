"""Entry point — Upstox login (run once daily before market)."""
from live.auth_server import login

if __name__ == '__main__':
    token = login()
    if token:
        print(f'Login successful. Token saved.')
    else:
        print('Login failed. Check API key/secret.')
