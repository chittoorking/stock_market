"""MCX Commodity Futures Bot — Multyfi signals → Upstox MCX execution.

Strategy: 90% WR on 29 trades (Aug 2026 backtest)
- Silver 93%, NatGas 89%, Crude 80%, Gold 100%
- Tight SL (avg 0.35%), quick holds (10-60 min)
- Trust Multyfi entry + SL + exit (no trail, no scoring)

Mode: PAPER (log signals, track virtual P&L) until Upstox creds provided.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, date
from pathlib import Path

import requests


# ── PID Lock — prevent duplicates ───────────────────────────────────
import fcntl
LOCK_FILE = os.path.expanduser('~/mcx_bot.lock')
_lock_fp = open(LOCK_FILE, 'w')
try:
    fcntl.flock(_lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lock_fp.write(str(os.getpid()))
    _lock_fp.flush()
except BlockingIOError:
    print('MCX bot already running. Exiting.')
    exit(0)

# ── Config ──────────────────────────────────────────────────────────────
MULTYFI_BASE = 'https://app.multyfi.com/api'
UPSTOX_API = 'https://api.upstox.com/v2'
UPSTOX_HFT = 'https://api-hft.upstox.com/v2'

POLL_INTERVAL = 5
MCX_COMMODITIES = ('GOLD', 'GOLDM', 'SILVER', 'SILVERM', 'CRUDE', 'CRUDEOIL',
                    'CRUDEOILM', 'NATURALGAS', 'NATGAS')
STATE_FILE = os.path.expanduser('~/mcx_bot_state.json')
CREDS_FILE = os.path.expanduser('~/mcx_bot_creds.json')
LOG_DIR = os.path.expanduser('~/news-trading/live/logs')
TRADES_FILE = os.path.expanduser('~/news-trading/live/logs/mcx_paper_trades.json')

# ── Telegram ────────────────────────────────────────────────────────────
TG_TOKEN = '8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs'
TG_CHAT_ID = '866752968'

# ── Mode ────────────────────────────────────────────────────────────────
PAPER_MODE = True  # flip to False when Upstox creds are ready

# ── Logging ─────────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
log = logging.getLogger('mcx_bot')
log.setLevel(logging.INFO)
_fh = logging.FileHandler(f'{LOG_DIR}/mcx_bot_{date.today()}.log')
_fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
log.addHandler(_fh)
_ch = logging.StreamHandler()
_ch.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
log.addHandler(_ch)


def send_tg(msg):
    """Send Telegram alert."""
    try:
        requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
                      json={'chat_id': TG_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
                      timeout=10)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
# UPSTOX BROKER
# ═══════════════════════════════════════════════════════════════════════

class UpstoxBroker:
    """Upstox MCX order placement. Paper mode when no token."""

    def __init__(self, token: str = ''):
        self._token = token
        self._instruments = {}
        self._instrument_date = None

    def _headers(self):
        return {
            'Authorization': f'Bearer {self._token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def is_live(self) -> bool:
        return bool(self._token) and not PAPER_MODE

    def load_instruments(self):
        """Load MCX_FO instruments via search API."""
        today = date.today()
        if self._instrument_date == today and self._instruments:
            return
        if not self._token:
            log.info('No Upstox token — paper mode, skipping instrument load')
            self._instrument_date = today
            return

        log.info('Loading MCX instruments from Upstox...')
        self._instruments = {}
        for query in ['GOLD', 'SILVER', 'CRUDEOIL', 'NATURALGAS']:
            for expiry in ['current_month', 'next_month']:
                try:
                    r = requests.get(f'{UPSTOX_API}/instruments/search',
                                     params={'query': query, 'exchanges': 'MCX',
                                             'instrument_types': 'FUT', 'expiry': expiry,
                                             'records': 30},
                                     headers=self._headers(), timeout=15)
                    if r.status_code == 200:
                        for inst in r.json().get('data', []):
                            ts = inst.get('trading_symbol', '').upper()
                            self._instruments[ts] = {
                                'key': inst.get('instrument_key', ''),
                                'lot_size': inst.get('lot_size', 1),
                                'expiry': inst.get('expiry', ''),
                                'tick_size': inst.get('tick_size', 0.05),
                                'trading_symbol': ts,
                            }
                except Exception as e:
                    log.warning(f'Instrument search {query}/{expiry}: {e}')

        self._instrument_date = today
        log.info(f'Loaded {len(self._instruments)} MCX instruments')
        for ts, info in self._instruments.items():
            log.info(f'  {ts}: key={info["key"]} lot={info["lot_size"]}')

    def resolve_instrument(self, multyfi_sym: str) -> dict:
        """Map Multyfi symbol to Upstox instrument."""
        self.load_instruments()
        sym = multyfi_sym.upper().strip()
        base = re.sub(r'\d{2}[A-Z]{3}FUT$', '', sym).strip() or sym

        for ts, info in self._instruments.items():
            if base in ts:
                return info
        if base.endswith('M'):
            base_no_m = base[:-1]
            for ts, info in self._instruments.items():
                if base_no_m in ts:
                    return info

        log.warning(f'No instrument for {multyfi_sym} (base={base})')
        return {}

    def place_order(self, instrument_key: str, qty: int, side: str,
                    order_type: str = 'MARKET', price: float = 0) -> dict:
        """Place MCX futures order on Upstox."""
        if not self.is_live():
            log.info(f'[PAPER] {side} {instrument_key} qty={qty}')
            return {'status': 'ok', 'order_id': f'PAPER-{int(time.time())}'}

        payload = {
            'quantity': qty,
            'product': 'I',
            'validity': 'DAY',
            'price': price if order_type == 'LIMIT' else 0,
            'instrument_token': instrument_key,
            'order_type': order_type,
            'transaction_type': side,
            'disclosed_quantity': 0,
            'trigger_price': 0,
            'is_amo': False,
        }
        try:
            r = requests.post(f'{UPSTOX_HFT}/order/place',
                              json=payload, headers=self._headers(), timeout=15)
            resp = r.json()
            if resp.get('status') == 'success':
                oid = resp.get('data', {}).get('order_id', '')
                log.info(f'ORDER OK: {side} {instrument_key} qty={qty} -> {oid}')
                return {'status': 'ok', 'order_id': oid}
            else:
                log.error(f'ORDER FAIL: {resp}')
                return {'status': 'error', 'message': str(resp)}
        except Exception as e:
            log.error(f'ORDER ERROR: {e}')
            return {'status': 'error', 'message': str(e)}

    def get_ltp(self, instrument_key: str) -> float:
        """Get last traded price."""
        if not self._token:
            return 0
        try:
            r = requests.get(f'{UPSTOX_API}/market-quote/ltp',
                             params={'instrument_key': instrument_key},
                             headers=self._headers(), timeout=10)
            if r.status_code == 200:
                for v in r.json().get('data', {}).values():
                    return float(v.get('last_price', 0))
        except Exception as e:
            log.warning(f'LTP {instrument_key}: {e}')
        return 0


# ═══════════════════════════════════════════════════════════════════════
# MULTYFI SIGNAL READER
# ═══════════════════════════════════════════════════════════════════════

class MulSignalReader:
    """Poll Multyfi API for commodity futures signals."""

    def __init__(self, creds: dict):
        self._auth_token = creds.get('authToken', '')
        self._mobile = creds.get('mobile', '')
        self._seen = set()

    def _headers(self):
        return {
            'authtoken': self._auth_token,
            'mobile': self._mobile,
            'Content-Type': 'application/json',
        }

    def _is_commodity(self, sym: str) -> bool:
        return any(c in sym.upper() for c in MCX_COMMODITIES)

    def poll(self) -> list:
        """Return new commodity futures signals."""
        signals = []

        # 1. Futures premium
        try:
            r = requests.get(f'{MULTYFI_BASE}/futures/premium',
                             headers=self._headers(), timeout=15, verify=False)
            if r.status_code == 200:
                for item in r.json().get('data', []):
                    sig = self._parse_futures(item)
                    if sig:
                        signals.append(sig)
        except Exception as e:
            if 'SSL' not in str(e):
                log.warning(f'Futures poll: {e}')

        # 2. Skip options/premium — futures only (options = 75% WR, not worth it)

        # 3. Streams (EXIT signals)
        try:
            r = requests.get(f'{MULTYFI_BASE}/streams',
                             headers=self._headers(),
                             params={'page': 1, 'limit': 10},
                             timeout=15, verify=False)
            if r.status_code == 200:
                for stream in r.json().get('data', []):
                    sig = self._parse_stream(stream)
                    if sig:
                        signals.append(sig)
        except Exception as e:
            if 'SSL' not in str(e):
                log.warning(f'Streams poll: {e}')

        return signals

    def _parse_futures(self, item: dict) -> dict:
        """Parse signal from /futures/premium or /options/premium."""
        action = str(item.get('action', item.get('orderType', ''))).upper()
        base = item.get('baseSymbol', item.get('stockSymbol', ''))
        sym = str(base).replace(' ', '').strip().upper()

        if not sym or not self._is_commodity(sym):
            return None

        # FUTURES ONLY — skip options/strangles
        opt_type = str(item.get('optionType', '')).upper()
        if opt_type in ('CE', 'PE'):
            return None
        if 'FUT' not in sym and opt_type != 'FUT':
            return None

        entry_price = float(item.get('entryPrice', 0) or 0)
        sl = float(item.get('stopLoss', item.get('sl', 0)) or 0)
        target = float(item.get('target', item.get('targetPrice', 0)) or 0)
        entry_date = item.get('entryDate', '')

        sig_key = f"{sym}_{entry_date}_{action}"
        if sig_key in self._seen:
            return None
        self._seen.add(sig_key)

        # Skip if SL too tight (< 0.2%)
        if sl > 0 and entry_price > 0:
            sl_pct = abs(entry_price - sl) / entry_price * 100
            if sl_pct < 0.2:
                log.info(f'SKIP {sym}: SL {sl_pct:.2f}% < 0.2%')
                return None

        if action in ('BUY', 'SELL'):
            return {'symbol': sym, 'action': action, 'entry': entry_price,
                    'sl': sl, 'target': target, 'source': 'futures_premium',
                    'raw': item}
        return None

    def _parse_stream(self, stream: dict) -> dict:
        """Parse EXIT signals from /streams."""
        stream_id = stream.get('_id', '')
        if stream_id in self._seen:
            return None
        self._seen.add(stream_id)

        msg = stream.get('message', '').upper()
        fd = stream.get('fullDocument') or {}

        if 'EXIT' not in msg:
            return None

        base = fd.get('baseSymbol', fd.get('stockSymbol', ''))
        sym = str(base).replace(' ', '').strip().upper()

        if not sym or not self._is_commodity(sym):
            return None

        return {'symbol': sym, 'action': 'EXIT', 'entry': 0, 'sl': 0,
                'target': 0, 'source': 'streams_exit'}


# ═══════════════════════════════════════════════════════════════════════
# POSITION TRACKER — disk-persisted
# ═══════════════════════════════════════════════════════════════════════

class PositionTracker:
    def __init__(self):
        self._positions = {}
        self._trades = []  # completed trades for P&L
        self._load()

    def _load(self):
        try:
            state = json.loads(Path(STATE_FILE).read_text())
            self._positions = state.get('positions', {})
            if self._positions:
                log.info(f'Loaded {len(self._positions)} positions')
        except Exception:
            pass
        try:
            self._trades = json.loads(Path(TRADES_FILE).read_text())
        except Exception:
            self._trades = []

    def _save(self):
        try:
            state = {}
            if Path(STATE_FILE).exists():
                state = json.loads(Path(STATE_FILE).read_text())
            state['positions'] = self._positions
            Path(STATE_FILE).write_text(json.dumps(state, indent=2))
        except Exception as e:
            log.warning(f'State save: {e}')

    def _save_trades(self):
        try:
            Path(TRADES_FILE).write_text(json.dumps(self._trades, indent=2))
        except Exception as e:
            log.warning(f'Trades save: {e}')

    def has(self, base: str) -> bool:
        return base.upper() in self._positions

    def add(self, base: str, info: dict):
        self._positions[base.upper()] = info
        self._save()

    def remove(self, base: str) -> dict:
        info = self._positions.pop(base.upper(), None)
        self._save()
        return info

    def get(self, base: str) -> dict:
        return self._positions.get(base.upper(), {})

    def all(self) -> dict:
        return dict(self._positions)

    def record_trade(self, trade: dict):
        self._trades.append(trade)
        self._save_trades()
        # Print running P&L
        total_pnl = sum(t.get('pnl', 0) for t in self._trades)
        wins = sum(1 for t in self._trades if t.get('pnl', 0) > 0)
        total = len(self._trades)
        wr = wins / total * 100 if total > 0 else 0
        log.info(f'PAPER P&L: {total} trades, {wins}W/{total-wins}L = {wr:.0f}% WR, '
                 f'total={total_pnl:+,.0f}')

    def clear_stale(self, today: str):
        stale = [k for k, v in self._positions.items() if v.get('date', '') != today]
        for k in stale:
            log.info(f'STALE removed: {k}')
            del self._positions[k]
        if stale:
            self._save()


# ═══════════════════════════════════════════════════════════════════════
# MAIN BOT
# ═══════════════════════════════════════════════════════════════════════

class MCXBot:
    def __init__(self):
        # Load Multyfi creds (always needed)
        multyfi_creds = json.loads(Path(os.path.expanduser('~/multyfi_creds.json')).read_text())

        # Load Upstox token if available
        upstox_token = ''
        try:
            creds = json.loads(Path(CREDS_FILE).read_text())
            upstox_token = creds.get('upstox', {}).get('access_token', '')
        except Exception:
            pass
        if not upstox_token:
            try:
                upstox_token = Path(os.path.expanduser(
                    '~/news-trading/data/upstox_token.txt')).read_text().strip()
            except Exception:
                pass

        self._broker = UpstoxBroker(upstox_token)
        self._signals = MulSignalReader(multyfi_creds)
        self._positions = PositionTracker()
        self._max_positions = 6

        mode = 'PAPER' if PAPER_MODE or not self._broker.is_live() else 'LIVE'
        log.info(f'MCX Bot mode: {mode}')

    def _base(self, sym: str) -> str:
        return re.sub(r'\d{2}[A-Z]{3}FUT$', '', sym.upper()).strip() or sym.upper()

    def run(self):
        log.info('=' * 60)
        log.info(f'MCX Commodity Bot — {"PAPER" if PAPER_MODE else "LIVE"}')
        log.info('=' * 60)

        while True:
            now = datetime.now()

            # MCX: 9:00 - 23:30
            if now.hour < 9 or (now.hour >= 23 and now.minute >= 30):
                if now.hour == 0 and now.minute < 5:
                    self._positions.clear_stale(str(date.today()))
                time.sleep(60)
                continue

            try:
                signals = self._signals.poll()
                for sig in signals:
                    self._handle(sig)

                # Check SL
                self._check_sl()

                # EOD exit at 23:15
                if now.hour == 23 and now.minute >= 15:
                    for base in list(self._positions.all().keys()):
                        self._exit(base, 'EOD_2315')

            except Exception as e:
                log.error(f'Bot error: {e}', exc_info=True)

            time.sleep(POLL_INTERVAL)

    def _handle(self, sig: dict):
        sym = sig['symbol']
        action = sig['action']
        base = self._base(sym)

        if action == 'EXIT':
            self._exit(base, 'MULTYFI_EXIT')
            return

        if action == 'SELL':
            if self._positions.has(base):
                self._exit(base, 'MULTYFI_SELL')
            else:
                self._enter(sym, sig, 'SELL')
            return

        if action == 'BUY':
            self._enter(sym, sig, 'BUY')

    def _enter(self, sym: str, sig: dict, side: str):
        base = self._base(sym)

        if self._positions.has(base):
            log.info(f'SKIP {sym}: already in position')
            return

        if len(self._positions.all()) >= self._max_positions:
            log.info(f'SKIP {sym}: max positions reached')
            return

        entry = sig.get('entry', 0)
        sl = sig.get('sl', 0)
        target = sig.get('target', 0)
        sl_pct = abs(entry - sl) / entry * 100 if entry > 0 and sl > 0 else 0

        # Resolve instrument for live mode
        inst = self._broker.resolve_instrument(sym)
        instrument_key = inst.get('key', f'MCX_FO|{sym}')
        lot_size = inst.get('lot_size', 1)

        result = self._broker.place_order(instrument_key, lot_size, side)

        if result.get('status') == 'ok':
            self._positions.add(base, {
                'symbol': sym, 'side': side, 'entry': entry,
                'sl': sl, 'target': target,
                'instrument_key': instrument_key,
                'qty': lot_size, 'order_id': result.get('order_id', ''),
                'date': str(date.today()),
                'time': datetime.now().strftime('%H:%M:%S'),
            })
            sl_str = f'{sl:,.0f}' if sl > 1000 else f'{sl:.2f}'
            entry_str = f'{entry:,.0f}' if entry > 1000 else f'{entry:.2f}'
            send_tg(
                f'🔔 <b>MCX {side}</b>: {sym}\n'
                f'Entry: {entry_str}\n'
                f'SL: {sl_str} ({sl_pct:.2f}%)\n\n'
                f'<a href="https://www.indmoney.com/commodities/futures">Open INDmoney → Commodities</a>'
            )
            log.info(f'ENTER: {side} {sym} entry={entry} sl={sl} ({sl_pct:.2f}%) '
                     f'target={target} qty={lot_size}')

    def _exit(self, base: str, reason: str):
        pos = self._positions.get(base)
        if not pos:
            return

        exit_side = 'SELL' if pos['side'] == 'BUY' else 'BUY'
        result = self._broker.place_order(pos['instrument_key'], pos['qty'], exit_side)

        if result.get('status') == 'ok':
            # Get exit price (LTP or use entry as fallback for paper)
            exit_price = self._broker.get_ltp(pos['instrument_key'])
            if exit_price <= 0:
                exit_price = pos.get('entry', 0)  # paper mode fallback

            entry = pos.get('entry', 0)
            side = pos.get('side', 'BUY')
            if entry > 0 and exit_price > 0:
                if side == 'BUY':
                    pnl_pct = (exit_price - entry) / entry * 100
                else:
                    pnl_pct = (entry - exit_price) / entry * 100
            else:
                pnl_pct = 0

            trade = {
                'symbol': pos['symbol'], 'side': side,
                'entry': entry, 'exit': exit_price,
                'pnl_pct': round(pnl_pct, 2), 'pnl': round(pnl_pct * entry / 100, 0),
                'reason': reason,
                'entry_time': pos.get('time', ''),
                'exit_time': datetime.now().strftime('%H:%M:%S'),
                'date': pos.get('date', str(date.today())),
            }
            self._positions.record_trade(trade)
            self._positions.remove(base)
            emoji = '✅' if pnl_pct > 0 else '❌'
            send_tg(
                f'{emoji} <b>EXIT {base}</b> ({reason})\n'
                f'P&L: {pnl_pct:+.2f}%'
            )
            log.info(f'EXIT: {base} ({reason}) pnl={pnl_pct:+.2f}%')

    def _check_sl(self):
        for base, pos in list(self._positions.all().items()):
            sl = pos.get('sl', 0)
            if sl <= 0:
                continue

            ltp = self._broker.get_ltp(pos.get('instrument_key', ''))
            if ltp <= 0:
                continue

            side = pos.get('side', 'BUY')
            if (side == 'BUY' and ltp <= sl) or (side == 'SELL' and ltp >= sl):
                log.info(f'SL HIT: {base} ltp={ltp} sl={sl}')
                self._exit(base, f'SL_HIT ltp={ltp}')


if __name__ == '__main__':
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    bot = MCXBot()
    bot.run()
