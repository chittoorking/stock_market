"""WebSocket price feed — real-time LTP from INDmoney."""
import json, threading, logging, time
from live import indmoney_client as api

try:
    import websocket
except ImportError:
    websocket = None

log = logging.getLogger('price_feed')

WS_PRICES = 'wss://ws-prices.indstocks.com/api/v1/ws/prices'


class PriceFeed:
    """WebSocket price feed — real-time LTP for subscribed stocks."""

    def __init__(self):
        self.prices = {}
        self.ws = None
        self._connected = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._subscribed = []  # track symbols for re-subscribe on reconnect

    def start(self, token):
        if not websocket:
            log.warning('No websocket module, REST fallback only')
            return
        self._thread = threading.Thread(target=self._run, args=(token,), daemon=True)
        self._thread.start()
        if not self._connected.wait(timeout=10):
            log.warning('WebSocket: connection timeout, REST fallback')

    def _run(self, token):

        def on_open(ws):
            log.info('WebSocket price feed: CONNECTED')
            self._connected.set()

        def on_message(ws, message):
            try:
                if isinstance(message, bytes):
                    message = message.decode('utf-8')
                data = json.loads(message)
                instrument = data.get('instrument', '')
                msg_data = data.get('data', {})
                if isinstance(msg_data, dict):
                    ltp = msg_data.get('ltp')
                    if ltp is not None:
                        with self._lock:
                            self.prices[instrument] = float(ltp)
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass

        def on_error(ws, error):
            log.error(f'WebSocket price error: {error}')

        def on_close(ws, close_code, close_msg):
            log.warning(f'WebSocket: CLOSED ({close_code})')
            self._connected.clear()

        # Infinite reconnection — never give up until 3:15 PM
        attempt = 0
        while True:
            from datetime import datetime
            now = datetime.now()
            if now.hour >= 15 and now.minute >= 15:
                log.info('WebSocket: market closed, stopping reconnection')
                break

            fresh_token = api.get_token() or token
            try:
                self.ws = websocket.WebSocketApp(
                    WS_PRICES,
                    header={'Authorization': fresh_token},
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                log.error(f'WebSocket failed: {e}')

            attempt += 1
            wait = min(30, 5 * attempt)  # backoff capped at 30s
            log.info(f'WebSocket reconnecting in {wait}s (attempt {attempt})...')
            time.sleep(wait)

            # Re-subscribe after reconnect
            if self._connected.is_set() and self._subscribed:
                self.subscribe(self._subscribed)

    def subscribe(self, symbols):
        """Subscribe to LTP for given NSE symbols."""
        self._subscribed = symbols  # save for re-subscribe on reconnect
        if not self.ws or not self._connected.is_set():
            log.warning('WebSocket not connected, cannot subscribe')
            return
        instruments = []
        for sym in symbols:
            scrip = api.SCRIP_CODES.get(sym)
            if scrip:
                parts = scrip.split('_')
                if len(parts) == 2:
                    instruments.append(f'{parts[0]}:{parts[1]}')
        if instruments:
            msg = json.dumps({'action': 'subscribe', 'mode': 'ltp', 'instruments': instruments})
            try:
                self.ws.send(msg)
                log.info(f'Subscribed to {len(instruments)} instruments')
            except Exception as e:
                log.error(f'Subscribe error: {e}')

    def get_ltp(self, sym):
        """Get latest price. WebSocket first, REST fallback."""
        scrip = api.SCRIP_CODES.get(sym)
        if scrip:
            ws_key = scrip.replace('_', ':')
            with self._lock:
                price = self.prices.get(ws_key)
            if price:
                return price
        # REST fallback
        result = api.get_ltp([sym])
        return result.get(sym, 0)
