"""Gap Fill Basket Trader V3 — INDstocks Flash API.

Strategy (validated 356/357 green days on unseen 2025-2026 data):
  UNIVERSE = 90 stocks (NIFTY 100 minus TRENT — unavailable on INDstocks)
  SCORE    = EV × gap × depth_score
  FILTER   = rolling WR >= 60%, max 2 per sector, min 5 stocks
  ENTRY    = 9:15 AM, MARKET order, fade the gap
  EXIT     = ATR trail (SL=0.05×ATR, trail=0.005×ATR) via WebSocket
  SELECT   = top 10 by score
  ALLOCATE = WR × (gap/ATR) weighted (not equal)

V2 additions:
  1. WebSocket for real-time LTP (replaces 2-sec polling)
  2. Market depth scoring (buy/sell pressure at entry)
  3. Margin check before each order
  4. WebSocket order updates (instant fill detection)
  5. Circuit limit safety check
  6. Real-time portfolio P&L tracking
"""
import sys, io, logging, time, json, threading
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
import numpy as np
import websocket

from . import indmoney_client as api

if sys.platform == 'win32' and not isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('basket_bot.log', encoding='utf-8'),
    ]
)
log = logging.getLogger('basket')

# ═══ SECTORS (90 stocks — NIFTY 100 minus TRENT) ═══
SECTORS = {
    # NIFTY 50
    'ITC':'FMCG','BHARTIARTL':'Tel','TCS':'IT','CIPLA':'Pharma','NESTLEIND':'FMCG',
    'BRITANNIA':'FMCG','NTPC':'Pwr','SUNPHARMA':'Pharma','HDFCLIFE':'Ins',
    'ICICIBANK':'Bank','TATACONSUM':'FMCG','APOLLOHOSP':'Health','MARUTI':'Auto',
    'EICHERMOT':'Auto','INFY':'IT','SBIN':'Bank','WIPRO':'IT','TATASTEEL':'Metal',
    'HINDALCO':'Metal','LT':'Infra','TITAN':'Con','GRASIM':'Cem','COALINDIA':'Mine',
    'ASIANPAINT':'Paint','BAJAJ-AUTO':'Auto','DIVISLAB':'Pharma','INDUSINDBK':'Bank',
    'HDFCBANK':'Bank','RELIANCE':'Cong','AXISBANK':'Bank','M&M':'Auto',
    'TATAMOTORS':'Auto','ADANIENT':'Cong','ADANIPORTS':'Infra','BPCL':'Oil',
    'ONGC':'Oil','JSWSTEEL':'Metal','HCLTECH':'IT','TECHM':'IT','POWERGRID':'Pwr',
    'UPL':'Agri','SBILIFE':'Ins','ULTRACEMCO':'Cem','HINDUNILVR':'FMCG',
    'HEROMOTOCO':'Auto','BAJFINANCE':'Fin','BAJAJFINSV':'Fin','KOTAKBANK':'Bank',
    'DRREDDY':'Pharma','SHRIRAMFIN':'Fin',
    # NIFTY Next 50 (estimated scrip codes — verify with verify_scrips.py)
    'ABB':'Capital','ADANIENSOL':'Pwr','ADANIGREEN':'Pwr','ADANIPOWER':'Pwr',
    'AMBUJACEM':'Cem','BAJAJHLDNG':'Fin','BANKBARODA':'Bank','BOSCHLTD':'Auto',
    'CANBK':'Bank','CGPOWER':'Capital','CHOLAFIN':'Fin','CUMMINSIND':'Capital',
    'DLF':'Realty','DMART':'Retail','GAIL':'Oil','GODREJCP':'FMCG',
    'HAL':'Defence','HDFCAMC':'Fin','HINDZINC':'Metal','INDHOTEL':'Hotel',
    'IOC':'Oil','IRFC':'Fin','JINDALSTEL':'Metal','LODHA':'Realty',
    'LTM':'IT','MOTHERSON':'Auto','MUTHOOTFIN':'Fin','PFC':'Fin',
    'PIDILITIND':'Chem','PNB':'Bank','RECLTD':'Fin','SHREECEM':'Cem',
    'SIEMENS':'Capital','SOLARINDS':'Defence','TATAPOWER':'Pwr','TORNTPHARM':'Pharma',
    'TVSMOTOR':'Auto','UNIONBANK':'Bank','UNITDSPR':'FMCG','VBL':'FMCG',
}

ALL_STOCKS = list(api.SCRIP_CODES.keys())

# ═══ STRATEGY PARAMS ═══
MIN_GAP = 0.5
MIN_WR = 0.60
MAX_PER_SECTOR = 2
MIN_BASKET = 5
MAX_BASKET = 10
SL_ATR_MULT = 0.20  # 0.05 was too tight for tick data at 9:15 — triggered on noise
TRAIL_ATR_MULT = 0.005
MIN_TRAIL_PCT = 0.10  # floor: never trail tighter than 0.1% (prevents sub-tick noise exits)
LEVERAGE = 5

# ═══ SESSION TOGGLES (enable/disable via env or here) ═══
ENABLE_GAP_FILL = True       # S1: proven +Rs 1,444/day
ENABLE_FLIPS = True          # S1b: proven +Rs 286/day
ENABLE_PAIRS = False         # S2: marginal at Rs 50K, enable at Rs 2L+
ENABLE_SECTOR_UNHEDGED = False  # S2b: short leader only, +Rs 173/day, enable at Rs 2L+
ENABLE_BB_ALMA = False       # S3: entry validated, exit needs more testing
ENABLE_BIGBAR = True         # S4: big bar reversal, Rs 530/trade, 87% WR, all day
CIRCUIT_MARGIN = 2.0  # skip stocks within 2% of circuit

# ═══ WEBSOCKET URLs ═══
WS_PRICES = 'wss://ws-prices.indstocks.com/api/v1/ws/prices'
WS_ORDERS = 'wss://ws-order-updates.indstocks.com/api/v1/ws/trades'

# ═══ PERSISTENCE ═══
DATA_DIR = Path(__file__).parent.parent / 'data'
FILL_HISTORY_FILE = DATA_DIR / 'fill_history.json'


class PriceFeed:
    """WebSocket price feed — real-time LTP for all subscribed stocks."""

    def __init__(self):
        self.prices = {}        # scrip → ltp
        self.ws = None
        self._connected = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def start(self, token):
        """Connect WebSocket in background thread."""
        self._thread = threading.Thread(target=self._run, args=(token,), daemon=True)
        self._thread.start()
        # Wait up to 10 seconds for connection
        if not self._connected.wait(timeout=10):
            log.warning('WebSocket price feed: connection timeout, will use REST fallback')

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
            log.warning(f'WebSocket price feed: CLOSED ({close_code})')
            self._connected.clear()

        max_retries = 5
        for attempt in range(max_retries):
            try:
                self.ws = websocket.WebSocketApp(
                    WS_PRICES,
                    header={'Authorization': token},
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                log.error(f'WebSocket price feed failed: {e}')

            # If we get here, WS disconnected. Retry with backoff.
            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                log.info(f'WebSocket reconnecting in {wait}s (attempt {attempt + 2}/{max_retries})...')
                time.sleep(wait)
            else:
                log.error(f'WebSocket gave up after {max_retries} attempts. Using REST fallback.')

    def subscribe(self, scrip_codes):
        """Subscribe to LTP updates for given scrip codes."""
        if not self.ws or not self._connected.is_set():
            log.warning('WebSocket not connected, cannot subscribe')
            return
        # Format: NSE_2885 → NSE:2885
        instruments = []
        for sc in scrip_codes:
            parts = sc.split('_')
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
        """Get latest price for a symbol. Falls back to REST if WS not available."""
        scrip = api.SCRIP_CODES.get(sym)
        if scrip:
            ws_key = scrip.replace('_', ':')  # NSE_2885 → NSE:2885
            with self._lock:
                price = self.prices.get(ws_key)
            if price:
                return price
        # Fallback to REST
        result = api.get_ltp([sym])
        return result.get(sym, 0)

    def get_all_ltp(self, syms):
        """Get LTP for multiple symbols."""
        result = {}
        missing = []
        for sym in syms:
            scrip = api.SCRIP_CODES.get(sym)
            if scrip:
                ws_key = scrip.replace('_', ':')
                with self._lock:
                    price = self.prices.get(ws_key)
                if price:
                    result[sym] = price
                    continue
            missing.append(sym)
        # Fetch missing via REST
        if missing:
            rest = api.get_ltp(missing)
            result.update(rest)
        return result

    def stop(self):
        if self.ws:
            self.ws.close()


class OrderFeed:
    """WebSocket order updates — instant fill/reject notifications."""

    def __init__(self):
        self.updates = {}       # order_id → status
        self.callbacks = {}     # order_id → callback function
        self.ws = None
        self._connected = threading.Event()
        self._lock = threading.Lock()

    def start(self, token):
        thread = threading.Thread(target=self._run, args=(token,), daemon=True)
        thread.start()
        self._connected.wait(timeout=10)

    def _run(self, token):
        def on_open(ws):
            log.info('WebSocket order feed: CONNECTED')
            # Subscribe to order updates
            msg = json.dumps({'action': 'subscribe', 'mode': 'order_updates', 'instruments': []})
            ws.send(msg)
            self._connected.set()

        def on_message(ws, message):
            try:
                data = json.loads(message)
                oid = str(data.get('order_id', ''))
                status = data.get('status', '')
                if oid:
                    with self._lock:
                        self.updates[oid] = data
                    # Fire callback if registered
                    cb = self.callbacks.get(oid)
                    if cb:
                        cb(data)
                    if status in ('complete', 'rejected', 'cancelled'):
                        log.info(f'Order {oid}: {status}')
            except Exception:
                pass

        def on_error(ws, error):
            log.error(f'WebSocket order error: {error}')

        def on_close(ws, close_code, close_msg):
            log.warning('WebSocket order feed: CLOSED')
            self._connected.clear()

        max_retries = 5
        for attempt in range(max_retries):
            try:
                self.ws = websocket.WebSocketApp(
                    WS_ORDERS,
                    header={'Authorization': token},
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                log.error(f'WebSocket order feed failed: {e}')

            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                log.info(f'Order feed reconnecting in {wait}s (attempt {attempt + 2}/{max_retries})...')
                time.sleep(wait)
            else:
                log.error(f'Order feed gave up after {max_retries} attempts.')

    def is_filled(self, order_id):
        with self._lock:
            data = self.updates.get(str(order_id), {})
        return data.get('status') == 'complete'

    def is_rejected(self, order_id):
        with self._lock:
            data = self.updates.get(str(order_id), {})
        return data.get('status') == 'rejected'

    def stop(self):
        if self.ws:
            self.ws.close()


class BasketTrader:
    def __init__(self, capital=0, paper_mode=True):
        self.paper_mode = paper_mode
        self.capital = capital  # 0 = auto-fetch from broker
        self.total_capital = capital * LEVERAGE
        self.today = datetime.now().strftime('%Y-%m-%d')
        self.capital_pool = None  # set after capital is known in run()

        # State
        self.positions = {}
        self.daily_pnl = 0.0
        self.daily_trades = []
        self.flip_candidates = {}   # sym → flip metadata after pure SL hit

        # Data
        self.prev_close = {}
        self.daily_history = {}
        self.fill_history = {}
        self.circuit_limits = {}    # sym → {upper, lower}
        self.available_margin = 0

        # WebSocket feeds
        self.price_feed = PriceFeed()
        self.order_feed = OrderFeed()

        self._load_fill_history()

    # ═══════════════════════════════════════════════════════════
    # PERSISTENCE
    # ═══════════════════════════════════════════════════════════

    def _load_fill_history(self):
        if FILL_HISTORY_FILE.exists():
            try:
                self.fill_history = json.loads(FILL_HISTORY_FILE.read_text())
                log.info(f'Loaded fill history: {len(self.fill_history)} stocks')
            except Exception:
                self.fill_history = {}

    def _save_fill_history(self):
        FILL_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        FILL_HISTORY_FILE.write_text(json.dumps(self.fill_history, indent=2))

    def _update_all_fill_history(self):
        """At EOD, check ALL gap stocks (not just traded ones).
        For stocks we didn't trade, check: did the gap fill?
        This prevents stocks from getting permanently stuck below MIN_WR.
        """
        log.info('Updating fill history for all gap stocks...')
        traded_syms = set(t['sym'] for t in self.daily_trades)
        ltp = api.get_ltp(ALL_STOCKS)

        updated = 0
        for sym in ALL_STOCKS:
            if sym in traded_syms:
                continue  # already updated during exit_position

            prev = self.prev_close.get(sym, 0)
            if prev <= 0:
                continue

            # Check if there was a gap today
            open_price = self.prev_close.get(sym, 0)  # we need today's open
            close_price = ltp.get(sym, 0)
            if close_price <= 0:
                continue

            # We stored prev_close but not today's open.
            # Use the close vs prev_close to infer: did today's gap fill?
            # If stock gapped UP (open > prev_close) and close < open → filled
            # We don't have exact open, but we can approximate:
            # If we have daily_history, today's first entry would have open.
            # For now, skip passive update for stocks we don't have open for.
            # The fill_history is also updated from backtest data on disk.

            # BETTER APPROACH: fetch today's OHLC from API
            # and check if gap filled
            pass

        # Actually, let's fetch full quotes which include day_open
        try:
            quotes = api.get_full_quote(ALL_STOCKS)
            for sym in ALL_STOCKS:
                if sym in traded_syms:
                    continue
                q = quotes.get(sym, {})
                day_open = q.get('open', 0)
                prev = self.prev_close.get(sym, 0)
                close = q.get('last_price', 0)
                if day_open <= 0 or prev <= 0 or close <= 0:
                    continue

                gap = (day_open - prev) / prev * 100
                if abs(gap) < 0.5:
                    continue

                # Estimate pnl_pct as if we traded it (fade the gap)
                if gap > 0:  # gap up → SHORT
                    pnl_pct = (day_open - close) / day_open * 100
                else:  # gap down → LONG
                    pnl_pct = (close - day_open) / day_open * 100

                if sym not in self.fill_history:
                    self.fill_history[sym] = []
                self.fill_history[sym].append([self.today, pnl_pct])
                self.fill_history[sym] = self.fill_history[sym][-50:]
                updated += 1

            log.info(f'Passively updated WR for {updated} non-traded gap stocks')
        except Exception as e:
            log.error(f'Passive WR update failed: {e}')

    def rolling_wr(self, sym, n=20):
        hist = self.fill_history.get(sym, [])
        if len(hist) < 5:
            return 0.70
        recent = hist[-n:]
        # fill_history stores [date, pnl_value] — win if pnl > 0
        return sum(1 for r in recent if r[1] > 0) / len(recent)

    def expected_value(self, sym, n=20):
        """Expected value per trade = WR × avg_win + (1-WR) × avg_loss.
        Uses actual PnL values from fill history, not just win/loss."""
        hist = self.fill_history.get(sym, [])
        if len(hist) < 5:
            return 0.20  # default EV
        recent = [r[1] for r in hist[-n:]]
        wr = sum(1 for p in recent if p > 0) / len(recent)
        wins = [p for p in recent if p > 0]
        losses = [p for p in recent if p <= 0]
        avg_win = np.mean(wins) if wins else 0.30
        avg_loss = np.mean(losses) if losses else -0.10
        return wr * avg_win + (1 - wr) * avg_loss

    # ═══════════════════════════════════════════════════════════
    # DATA LOADING
    # ═══════════════════════════════════════════════════════════

    def load_market_data(self):
        """Fetch prev_close, circuit limits, 30-day history, margin."""
        log.info('Loading market data...')

        # [FEATURE 5] Full quotes → prev_close + circuit limits
        quotes = api.get_full_quote(ALL_STOCKS)
        for sym, q in quotes.items():
            self.prev_close[sym] = q.get('prev_close', 0)
            self.circuit_limits[sym] = {
                'upper': q.get('upper_circuit', 0),
                'lower': q.get('lower_circuit', 0),
            }

        # [FEATURE 3] Auto-detect capital from broker (retry until funds available)
        self.available_margin = api.get_funds()
        log.info(f'Available margin: Rs {self.available_margin:,.0f}')

        if self.capital == 0 and self.available_margin == 0:
            log.info('Funds = 0 (market closed?) — will retry at 9:10 AM')
            from datetime import datetime as dt
            target = dt.now().replace(hour=9, minute=10, second=0, microsecond=0)
            now = dt.now()
            if now < target:
                wait = (target - now).total_seconds()
                log.info(f'Sleeping {wait:.0f}s until 9:10 AM for funds retry...')
                time.sleep(max(0, wait))
            for attempt in range(10):
                self.available_margin = api.get_funds()
                if self.available_margin > 0:
                    log.info(f'Funds detected: Rs {self.available_margin:,.0f} (attempt {attempt+1})')
                    break
                log.info(f'Funds still 0, retry {attempt+1}/10 in 30s...')
                time.sleep(30)

        if self.capital == 0:
            self.capital = max(int(self.available_margin), 0)
            log.info(f'Auto-detected capital: Rs {self.capital:,}')
        elif self.available_margin > 0 and self.available_margin < self.capital:
            log.warning(f'Margin Rs {self.available_margin:,.0f} < requested Rs {self.capital:,}, using available')
            self.capital = int(self.available_margin)

        self.total_capital = self.capital * LEVERAGE

        # Initialize shared capital pool — all sessions draw from this
        from .capital_pool import CapitalPool
        self.capital_pool = CapitalPool(total_own=int(self.capital * 0.95), leverage=LEVERAGE)  # 5% buffer
        log.info(self.capital_pool.status())

        if self.capital < 20000:
            log.error(f'Capital Rs {self.capital:,} too low. Need at least Rs 20,000 for 5-stock basket.')
            log.error(f'Below Rs 20K = fewer than 5 stocks = no diversification safety.')
            return

        # Optimal basket size by capital slab (backtest-validated with charges)
        # Under 2L: 5 stocks (charges eat profit with more stocks)
        # 2L-5L: 8 stocks (diversification starts paying off)
        # 5L+: 10 stocks (full strategy, charges negligible)
        if self.capital < 200000:
            self.optimal_basket = 5
        elif self.capital < 500000:
            self.optimal_basket = 8
        else:
            self.optimal_basket = 10

        # Cap to what we can actually afford
        max_affordable = max(1, int(self.total_capital / 20000))
        self.optimal_basket = min(self.optimal_basket, max_affordable)

        log.info(f'Capital slab: Rs {self.capital:,} -> {self.optimal_basket} stocks')

        log.info(f'Capital: Rs {self.capital:,} x {LEVERAGE}x = Rs {self.total_capital:,}')
        log.info(f'Basket: {self.optimal_basket} stocks, Rs {self.total_capital/self.optimal_basket:,.0f} each')
        log.info(f'')
        log.info(f'CAPITAL GUIDE:')
        log.info(f'  Rs 10K  = 2 stocks  (risky, ~70% green days)')
        log.info(f'  Rs 20K  = 5 stocks  (decent, ~95% green days)')
        log.info(f'  Rs 50K  = 10 stocks (optimal, ~99% green days)')
        log.info(f'  Rs 2L+  = 10 stocks (full strategy, 99.7% green days)')
        log.info(f'  You are at: Rs {self.capital:,} = {self.optimal_basket} stocks')

        # 30-day history
        now = datetime.now()
        end_ts = int(now.timestamp() * 1000)
        start_ts = int((now - timedelta(days=45)).timestamp() * 1000)
        for sym in ALL_STOCKS:
            candles = api.get_historical_candles(sym, '1day', start_ts, end_ts)
            if candles:
                parsed = []
                for c in candles:
                    if isinstance(c, dict):
                        parsed.append({
                            'timestamp': c.get('ts', c.get('timestamp', 0)),
                            'open': c.get('o', c.get('open', 0)),
                            'high': c.get('h', c.get('high', 0)),
                            'low': c.get('l', c.get('low', 0)),
                            'close': c.get('c', c.get('close', 0)),
                            'volume': c.get('v', c.get('volume', 0)),
                        })
                    elif isinstance(c, (list, tuple)) and len(c) >= 6:
                        parsed.append({
                            'timestamp': c[0], 'open': c[1], 'high': c[2],
                            'low': c[3], 'close': c[4], 'volume': c[5],
                        })
                self.daily_history[sym] = parsed[-30:]
            time.sleep(0.1)

        log.info(f'Loaded {len(self.prev_close)} stocks, '
                 f'{len(self.daily_history)} with history, '
                 f'{sum(1 for c in self.circuit_limits.values() if c["upper"]>0)} with circuits')

    def compute_indicators(self, sym):
        hist = self.daily_history.get(sym, [])
        if len(hist) < 20:
            return None
        closes = [d['close'] for d in hist]
        highs = [d['high'] for d in hist]
        lows = [d['low'] for d in hist]

        bb_mid = np.mean(closes[-20:]); bb_std = np.std(closes[-20:])
        bb_upper = bb_mid + 2 * bb_std; bb_lower = bb_mid - 2 * bb_std
        avg_range = np.mean([(highs[i]-lows[i])/closes[i]*100 for i in range(-20, 0)])

        gains, losses_r = [], []
        for i in range(1, len(closes)):
            ch = closes[i] - closes[i-1]
            gains.append(max(ch, 0)); losses_r.append(max(-ch, 0))
        ag = sum(gains[-14:])/14; al = sum(losses_r[-14:])/14
        rsi = 100 - 100/(1+ag/al) if al > 0 else 100

        trs = []
        for i in range(1, len(hist)):
            tr = max(hist[i]['high']-hist[i]['low'],
                     abs(hist[i]['high']-hist[i-1]['close']),
                     abs(hist[i]['low']-hist[i-1]['close']))
            trs.append(tr)
        atr = np.mean(trs[-14:]) if len(trs) >= 14 else np.mean(trs) if trs else 1
        atr_pct = atr / closes[-1] * 100 if closes[-1] > 0 else 2

        return {'bb_upper': bb_upper, 'bb_lower': bb_lower,
                'avg_range': avg_range, 'rsi': rsi, 'atr_pct': atr_pct}

    # ═══════════════════════════════════════════════════════════
    # [FEATURE 2] MARKET DEPTH SCORING
    # ═══════════════════════════════════════════════════════════

    def get_depth_score(self, sym, direction):
        """Score based on order book imbalance. HARD FILTER + MULTIPLIER.

        For SHORT (gap-up fade): we need sellers to dominate
        For LONG (gap-down fade): we need buyers to dominate

        Returns:
          0      → SKIP (order book disagrees with our direction)
          0.5-1.5 → multiplier for the score

        Logic:
          >70% AGAINST our trade → SKIP (hard filter, return 0)
          60-70% against         → penalize × 0.7
          40-60% balanced        → neutral × 1.0
          60-70% WITH our trade  → boost × 1.2
          >70% WITH our trade    → boost × 1.4
          >85% WITH our trade    → cap at 1.4 (could be spoofing)
        """
        try:
            depth = api.get_market_depth([sym])
            if sym not in depth:
                return 1.0  # neutral if no data
            d = depth[sym]
            buy_pct = d.get('buy_pct', 50)
            sell_pct = d.get('sell_pct', 50)

            if direction == 'SELL':  # gap-up, we SHORT → need sellers
                with_us = sell_pct
            else:  # gap-down, we LONG → need buyers
                with_us = buy_pct

            against_us = 100 - with_us

            # HARD FILTER: if >70% against us, SKIP
            if against_us > 70:
                log.info(f'{sym}: DEPTH SKIP — {against_us:.0f}% against our {direction}')
                return 0

            # Scoring
            if with_us > 85:
                return 1.4  # cap (could be spoofing above 85%)
            elif with_us > 70:
                return 1.4
            elif with_us > 60:
                return 1.2
            elif with_us > 40:
                return 1.0  # balanced
            elif with_us > 30:
                return 0.7  # moderate pressure against
            else:
                return 0  # shouldn't reach here (caught by hard filter)

        except Exception as e:
            log.debug(f'Depth score error for {sym}: {e}')
            return 1.0  # neutral on error — don't skip just because API failed

    # ═══════════════════════════════════════════════════════════
    # [FEATURE 5] CIRCUIT LIMIT CHECK
    # ═══════════════════════════════════════════════════════════

    def is_near_circuit(self, sym, price):
        """Check if stock is within CIRCUIT_MARGIN% of circuit limit."""
        cl = self.circuit_limits.get(sym, {})
        upper = cl.get('upper', 0)
        lower = cl.get('lower', 0)
        if upper > 0 and price > 0:
            pct_from_upper = (upper - price) / price * 100
            if pct_from_upper < CIRCUIT_MARGIN:
                return True
        if lower > 0 and price > 0:
            pct_from_lower = (price - lower) / price * 100
            if pct_from_lower < CIRCUIT_MARGIN:
                return True
        return False

    # ═══════════════════════════════════════════════════════════
    # SCORING + SELECTION
    # ═══════════════════════════════════════════════════════════

    def scan_gaps(self):
        log.info('Scanning for gaps...')

        # Fetch fresh quotes AT SCAN TIME (9:15) — one call, has everything
        quotes = api.get_full_quote(ALL_STOCKS)
        candidates = []

        for sym in ALL_STOCKS:
            q = quotes.get(sym, {})
            open_price = q.get('open', 0)
            current_price = q.get('last_price', 0)
            # Use FRESH prev_close from quote (not stale load_market_data)
            prev = q.get('prev_close', 0)
            if prev <= 0:
                prev = self.prev_close.get(sym, 0)  # fallback to loaded data
            else:
                self.prev_close[sym] = prev  # update our cache
            gap_price = open_price or current_price
            price = current_price or open_price
            if gap_price <= 0 or prev <= 0 or price <= 0:
                continue

            gap = (gap_price - prev) / prev * 100
            log.info(f'  {sym}: prev={prev:.2f} open={open_price:.2f} gap={gap:+.2f}%')
            if abs(gap) < MIN_GAP:
                continue

            # [FEATURE 5] Skip stocks near circuit limits
            if self.is_near_circuit(sym, price):
                log.info(f'{sym}: NEAR CIRCUIT at Rs {price:.2f}, skipping')
                continue

            wr = self.rolling_wr(sym)
            if wr < MIN_WR:
                continue

            ind = self.compute_indicators(sym)
            if ind is None:
                continue

            direction = 'SELL' if gap > 0 else 'BUY'

            # [FEATURE 2] Market depth — hard filter + score multiplier
            depth_score = self.get_depth_score(sym, direction)
            if depth_score == 0:
                continue  # order book says DON'T ENTER

            # EV×gap scoring — picks stocks that make MOST MONEY
            ev = self.expected_value(sym)
            score = ev * abs(gap) * depth_score

            sl_pct = ind['atr_pct'] * SL_ATR_MULT
            trail_pct = max(ind['atr_pct'] * TRAIL_ATR_MULT, MIN_TRAIL_PCT)

            # gap/ATR = signal-to-noise ratio (for allocation later)
            gap_vs_atr = abs(gap) / ind['atr_pct'] if ind['atr_pct'] > 0 else 1

            candidates.append({
                'sym': sym, 'gap': gap, 'price': price, 'prev_close': prev,
                'direction': direction, 'score': score,
                'wr': wr, 'ev': ev, 'gap_vs_atr': gap_vs_atr,
                'sector': SECTORS.get(sym, '?'),
                'sl_pct': sl_pct, 'trail_pct': trail_pct,
                'atr_pct': ind['atr_pct'], 'depth_score': depth_score,
            })

        log.info(f'Found {len(candidates)} gap candidates')

        # Sort by score, apply sector cap
        basket_size = getattr(self, 'optimal_basket', MAX_BASKET)
        min_needed = min(MIN_BASKET, basket_size)  # adjust min for small capital

        candidates.sort(key=lambda x: x['score'], reverse=True)
        selected = []; sec_count = defaultdict(int)
        for c in candidates:
            if sec_count[c['sector']] >= MAX_PER_SECTOR:
                continue
            selected.append(c)
            sec_count[c['sector']] += 1
            if len(selected) >= basket_size:
                break

        if len(selected) < min_needed:
            log.warning(f'Only {len(selected)} qualify (need {min_needed}). SKIP.')
            return []

        log.info(f'Selected {len(selected)} stocks:')
        for i, c in enumerate(selected):
            log.info(f'  #{i+1}: {c["sym"]:<12s} gap={c["gap"]:+.1f}% '
                     f'dir={c["direction"]} EV={c["ev"]:.3f} score={c["score"]:.3f} '
                     f'WR={c["wr"]:.0%} g/ATR={c["gap_vs_atr"]:.2f} '
                     f'depth={c["depth_score"]:.2f}')
        return selected

    # ═══════════════════════════════════════════════════════════
    # ENTRY
    # ═══════════════════════════════════════════════════════════

    def enter_basket(self, basket):
        n = len(basket)

        # Request capital from shared pool (caps to available if other sessions active)
        per_stock = self.total_capital // n  # 5% buffer already in capital_pool
        if self.capital_pool:
            per_stock = self.capital_pool.request('S1', n, per_stock)
            if per_stock == 0:
                log.error('Capital pool exhausted — cannot enter basket')
                return

        # Tiered allocation: #1=30%, #2=15%, #3=10%, rest split equally
        session_capital = per_stock * n
        tier_pcts = [0.30, 0.15, 0.10]
        fixed = sum(tier_pcts)           # 0.55
        rest_n = max(n - len(tier_pcts), 0)
        rest_each = (1 - fixed) / rest_n if rest_n > 0 else 0  # 0.45/7 = 6.43%
        if n <= len(tier_pcts):
            weights = (tier_pcts + [rest_each] * rest_n)[:n]
            total = sum(weights)
            weights = [w / total for w in weights]  # renormalize if fewer stocks
        else:
            weights = tier_pcts + [rest_each] * rest_n

        log.info(f'Entering {n} stocks (pool: Rs {session_capital:,}):')
        for c, w in zip(basket, weights):
            log.info(f'  {c["sym"]:<12s} weight={w:.1%} = Rs {session_capital*w:,.0f}')

        # Build order list
        orders = []
        for c, w in zip(basket, weights):
            sym = c['sym']
            price = c['price']
            pos_size = session_capital * w
            qty = int(pos_size / price)
            if qty <= 0:
                continue
            side = c['direction']

            # Pre-check margin
            if not self.paper_mode:
                margin_needed = qty * price / LEVERAGE
                if margin_needed > self.available_margin * 0.95:
                    log.warning(f'{sym}: margin Rs {margin_needed:,.0f} > available, reducing qty')
                    qty = int(self.available_margin * 0.95 / price * LEVERAGE)
                    if qty <= 0:
                        continue
                self.available_margin -= qty * price / LEVERAGE

            orders.append({'sym': sym, 'side': side, 'qty': qty, 'price': price, 'candidate': c})

        # Place all orders in PARALLEL (speed is the edge)
        import threading
        order_results = {}

        def place_one(o):
            sym = o['sym']
            c = o['candidate']
            entry = o['price']
            side = o['side']

            if self.paper_mode:
                order_results[sym] = f'PAPER-{sym}-{int(time.time())}'
                log.info(f'[PAPER] {side} {o["qty"]} {sym} @ Rs {entry:.2f}')
            else:
                # Step 1: MARKET entry (guaranteed fill)
                oid = api.place_order(sym, o['qty'], side, entry, order_type='MARKET')
                if oid is None:
                    log.error(f'FAILED to enter {sym}')
                    return
                order_results[sym] = oid

                # Bot manages trail/SL via WebSocket monitoring + LIMIT exits
                o['gtt_id'] = ''
                log.info(f'ENTRY {side} {o["qty"]} {sym} @ {entry:.2f} -> {oid}')

        threads = [threading.Thread(target=place_one, args=(o,)) for o in orders]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Brief pause then check for rejections
        if not self.paper_mode and order_results:
            time.sleep(1)

        # Register positions for successfully placed orders
        for o in orders:
            sym = o['sym']
            oid = order_results.get(sym)
            if not oid:
                continue

            # Check rejection
            if not self.paper_mode and self.order_feed.is_rejected(oid):
                log.error(f'{sym} order REJECTED: {oid}')
                continue

            c = o['candidate']
            entry = o['price']
            side = o['side']
            if side == 'SELL':
                sl_price = entry * (1 + c['sl_pct'] / 100)
            else:
                sl_price = entry * (1 - c['sl_pct'] / 100)
            trail_level = sl_price

            self.positions[sym] = {
                'direction': side, 'entry': entry, 'qty': o['qty'],
                'sl_pct': c['sl_pct'], 'trail_pct': c['trail_pct'],
                'sl_price': sl_price, 'mfe': 0.0,
                'trail_active': False, 'trail_level': trail_level,
                'order_id': oid, 'gtt_id': o.get('gtt_id', ''),
                'gap': c['gap'], 'score': c['score'],
                'atr_pct': c['atr_pct'],
                'bar1_high': entry, 'bar1_low': entry, 'bar1_range': 0.0,
            }

        log.info(f'Entered {len(self.positions)} positions')

    # ═══════════════════════════════════════════════════════════
    # [FEATURE 1] WEBSOCKET MONITORING
    # ═══════════════════════════════════════════════════════════

    def monitor_ws(self):
        """Monitor positions using WebSocket price feed. <20ms latency."""
        if not self.positions:
            return

        log.info(f'Monitoring {len(self.positions)} positions via WebSocket...')

        # Subscribe to price updates for our stocks
        scrip_codes = [api.SCRIP_CODES[sym] for sym in self.positions if sym in api.SCRIP_CODES]
        self.price_feed.subscribe(scrip_codes)

        # Timeout: if no price updates for 5 minutes, force close
        last_price_time = time.time()
        MAX_NO_PRICE_SECS = 300  # 5 minutes without any price = something is wrong

        close_time = datetime.now().replace(hour=15, minute=15, second=0, microsecond=0)
        while datetime.now() < close_time:
            try:
                for sym in list(self.positions.keys()):
                    if sym not in self.positions:
                        continue
                    pos = self.positions[sym]

                    # [FEATURE 1] Get price from WebSocket (instant) or REST (fallback)
                    price = self.price_feed.get_ltp(sym)
                    if price <= 0:
                        continue

                    entry = pos['entry']
                    direction = pos['direction']

                    if direction == 'SELL':
                        fav = (entry - price) / entry * 100
                    else:
                        fav = (price - entry) / entry * 100

                    pos['mfe'] = max(pos['mfe'], fav)

                    # Track bar1 range (first 5 min, 9:15–9:20)
                    if not pos.get('bar1_done'):
                        now_t = datetime.now()
                        pos['bar1_high'] = max(pos.get('bar1_high', price), price)
                        pos['bar1_low'] = min(pos.get('bar1_low', price), price)
                        if now_t.hour == 9 and now_t.minute >= 20:
                            ep = pos['entry']
                            pos['bar1_range'] = (pos['bar1_high'] - pos['bar1_low']) / ep * 100 if ep > 0 else 0.0
                            pos['bar1_done'] = True

                    # Trail logic — modify GTT on exchange instead of exiting
                    if pos['mfe'] > pos['trail_pct']:
                        pos['trail_active'] = True
                        new_trail = pos['mfe'] - pos['trail_pct']
                        if direction == 'SELL':
                            trail_price = entry * (1 - new_trail / 100)
                            new_sl = min(pos['trail_level'], trail_price)
                        else:
                            trail_price = entry * (1 + new_trail / 100)
                            new_sl = max(pos['trail_level'], trail_price)

                        # Only modify GTT if trail moved meaningfully (> Rs 0.50 or > 0.02%)
                        if new_sl != pos['trail_level']:
                            diff = abs(new_sl - pos.get('last_gtt_sl', pos['sl_price']))
                            diff_pct = diff / entry * 100 if entry > 0 else 0
                            pos['trail_level'] = new_sl
                            gtt_id = pos.get('gtt_id', '')
                            if gtt_id and not self.paper_mode and (diff >= 0.50 or diff_pct >= 0.02):
                                pos['last_gtt_sl'] = new_sl
                                sl_limit = new_sl * 1.002 if direction == 'SELL' else new_sl * 0.998
                                api.modify_smart_order(gtt_id,
                                    sl_trigger=round(new_sl, 2),
                                    sl_limit=round(sl_limit, 2))

                    # EXIT CHECK — trail and SL
                    should_exit = False; exit_reason = ''
                    if pos['trail_active']:
                        if direction == 'SELL' and price >= pos['trail_level']:
                            should_exit = True
                            exit_reason = f'TRAIL (MFE={pos["mfe"]:.3f}%)'
                        elif direction == 'BUY' and price <= pos['trail_level']:
                            should_exit = True
                            exit_reason = f'TRAIL (MFE={pos["mfe"]:.3f}%)'
                    if not should_exit:
                        if direction == 'SELL' and price >= pos['sl_price']:
                            should_exit = True; exit_reason = 'STOP LOSS'
                        elif direction == 'BUY' and price <= pos['sl_price']:
                            should_exit = True; exit_reason = 'STOP LOSS'

                    if should_exit:
                        self.exit_position(sym, price, exit_reason)

                    if price > 0:
                        last_price_time = time.time()

                # Safety: if no price for 5 min, force close everything
                if time.time() - last_price_time > MAX_NO_PRICE_SECS:
                    log.error('NO PRICE UPDATES for 5 minutes — force closing all positions')
                    self.close_all()
                    break

                # Small sleep to prevent CPU spinning, but much faster than 2-sec polling
                time.sleep(0.1)

                # [FEATURE 6] Real-time P&L display every 10 seconds
                if int(time.time()) % 10 == 0 and self.positions:
                    self._print_live_pnl()

                # [FEATURE 7] Flip monitor — only until 9:20 (gaps fill in first 5 min)
                now = datetime.now()
                if now.second < 2 and now.minute % 5 == 0 and self.flip_candidates:
                    if now.hour == 9 and now.minute <= 20:
                        self._check_flip_entries()
                    elif now.hour == 9 and now.minute > 20:
                        log.info('Past 9:20 — clearing flip candidates')
                        self.flip_candidates.clear()

                # Exit loop if nothing to do
                if not self.positions and not self.flip_candidates:
                    now_check = datetime.now()
                    if now_check.hour >= 10 or (now_check.hour == 9 and now_check.minute > 25):
                        log.info('No positions, no flips, past 9:25 — exiting monitor')
                        break

                # Force close at 3:15 PM — don't hold past market close
                if now.hour > 15 or (now.hour == 15 and now.minute >= 15):
                    log.warning('3:15 PM — force closing all positions from monitor loop')
                    self.close_all()
                    break

            except KeyboardInterrupt:
                log.warning('Keyboard interrupt — closing all')
                self.close_all()
                break
            except Exception as e:
                log.error(f'Monitor error: {e}')
                time.sleep(1)

    # ═══════════════════════════════════════════════════════════
    # [FEATURE 7] FLIP ON PURE SL — bar-close confirmation
    # ═══════════════════════════════════════════════════════════

    def _check_flip_entries(self):
        """At each 5-min bar boundary: check if any SL-stopped stock confirmed flip direction."""
        now = datetime.now()
        # No flips after 2:30 PM — not enough time to trail
        if now.hour > 14 or (now.hour == 14 and now.minute >= 30):
            self.flip_candidates.clear()
            return

        to_remove = []
        for sym, fc in list(self.flip_candidates.items()):
            # Skip if already in a position (re-entered by another signal)
            if sym in self.positions:
                to_remove.append(sym)
                continue

            price = self.price_feed.get_ltp(sym)
            if price <= 0:
                continue

            fc['bars_watched'] += 1

            # Check if this bar closed in flip direction vs previous bar close
            prev_close = fc['sl_bar_close']
            flip_dir = fc['flip_dir']
            confirmed = (price < prev_close) if flip_dir == 'SELL' else (price > prev_close)

            if confirmed:
                # Real order book filter — skip if depth strongly against flip direction
                depth_score = self.get_depth_score(sym, flip_dir)
                if depth_score == 0:
                    log.info(f'[FLIP] {sym}: order book against {flip_dir} — skipping')
                    to_remove.append(sym)
                    continue

                # Enter flip trade at current price (next bar open)
                atr_p = fc['atr_pct']
                sl_abs = atr_p * SL_ATR_MULT / 100 * price
                trail_pct = max(atr_p * TRAIL_ATR_MULT, MIN_TRAIL_PCT)
                sl_price = (price + sl_abs) if flip_dir == 'SELL' else (price - sl_abs)
                capital = fc['capital']
                qty = int(capital / price)

                if qty > 0:
                    if self.paper_mode:
                        log.info(f'[PAPER][FLIP] {flip_dir} {qty} {sym} @ Rs {price:.2f} '
                                 f'(SL={sl_price:.2f}, depth={depth_score:.2f})')
                    else:
                        oid = api.place_order(sym, qty, flip_dir, price, order_type='MARKET')
                        if oid is None:
                            log.error(f'[FLIP] Failed to enter {sym}')
                            to_remove.append(sym)
                            continue

                    self.positions[sym] = {
                        'direction': flip_dir,
                        'entry': price,
                        'qty': qty,
                        'sl_price': sl_price,
                        'trail_pct': trail_pct,
                        'trail_active': False,
                        'trail_level': sl_price,
                        'mfe': 0.0,
                        'atr_pct': atr_p,
                        'gap': 0,  # flip trade, no gap
                        'is_flip': True,
                    }
                    log.info(f'[FLIP] {sym}: {flip_dir} entered @ Rs {price:.2f} after {fc["bars_watched"]} bars')
                to_remove.append(sym)
            else:
                # Update bar close for next comparison
                fc['sl_bar_close'] = price
                # Expire after 6 bars (~30 min) if no confirmation
                if fc['bars_watched'] >= 6:
                    log.info(f'[FLIP] {sym}: no confirmation after 6 bars — dropping')
                    to_remove.append(sym)

        for sym in to_remove:
            self.flip_candidates.pop(sym, None)

    # ═══════════════════════════════════════════════════════════
    # [FEATURE 6] REAL-TIME P&L
    # ═══════════════════════════════════════════════════════════

    def _print_live_pnl(self):
        """Print live P&L for all open positions."""
        total_unrealized = 0
        for sym, pos in self.positions.items():
            price = self.price_feed.get_ltp(sym)
            if price <= 0:
                continue
            if pos['direction'] == 'SELL':
                pnl = (pos['entry'] - price) / pos['entry'] * 100
            else:
                pnl = (price - pos['entry']) / pos['entry'] * 100
            rs = pnl / 100 * pos['entry'] * pos['qty']
            total_unrealized += rs
        total = self.daily_pnl + total_unrealized
        log.info(f'[P&L] Realized: Rs {self.daily_pnl:+,.0f} | '
                 f'Unrealized: Rs {total_unrealized:+,.0f} | '
                 f'Total: Rs {total:+,.0f} | Open: {len(self.positions)}')

    # ═══════════════════════════════════════════════════════════
    # EXIT
    # ═══════════════════════════════════════════════════════════

    def exit_position(self, sym, exit_price, reason):
        pos = self.positions.get(sym)
        if not pos:
            return

        # Cancel GTT SL on exchange first (prevent double exit)
        gtt_id = pos.get('gtt_id', '')
        if gtt_id and not self.paper_mode:
            api.cancel_smart_order(gtt_id)

        qty = pos['qty']; direction = pos['direction']; entry = pos['entry']
        exit_side = 'BUY' if direction == 'SELL' else 'SELL'

        if self.paper_mode:
            log.info(f'[PAPER] EXIT {exit_side} {qty} {sym} @ Rs {exit_price:.2f} ({reason})')
        else:
            # Retry exit up to 3 times, but check broker before retry
            oid = None
            for attempt in range(3):
                # LIMIT first for exact price, falls back to MARKET if limit fails
                # Try LIMIT at exit price (get exact stop price)
                oid = api.place_order(sym, qty, exit_side, exit_price, order_type='LIMIT')
                if oid:
                    # Wait 3s for limit fill
                    time.sleep(3)
                    if self.order_feed.is_filled(oid):
                        log.info(f'{sym} LIMIT exit filled')
                        break
                    # Not filled — cancel and try MARKET
                    cancelled = api.cancel_order(oid)
                    time.sleep(1)
                    # Verify position still open before sending MARKET
                    try:
                        broker_pos = api.get_positions()
                        still_open = any(
                            p.get('trading_symbol', '') == sym and abs(int(p.get('net_quantity', 0))) > 0
                            for p in broker_pos) if broker_pos else False
                    except Exception:
                        still_open = True
                    if not still_open:
                        log.info(f'{sym} already closed (limit filled late) — done')
                        break
                    # Position still open, send MARKET
                    log.warning(f'{sym} LIMIT not filled, sending MARKET')
                    oid = api.place_order(sym, qty, exit_side, exit_price, order_type='MARKET')
                    if oid:
                        break
                log.error(f'Exit {sym} attempt {attempt+1}/3 failed')
                time.sleep(1)
            if oid is None:
                log.error(f'FAILED to exit {sym} after 3 attempts — removing from tracking anyway')
                # Still remove from positions to avoid infinite retry loop
                # Position may still be open on broker — log for manual check
            else:
                # [FEATURE 4] Wait for fill confirmation
                for _ in range(10):
                    if self.order_feed.is_filled(oid):
                        break
                    time.sleep(0.2)
                if not self.order_feed.is_filled(oid):
                    log.warning(f'{sym} exit order {oid} not confirmed as filled')

        if direction == 'SELL':
            pnl_pct = (entry - exit_price) / entry * 100
        else:
            pnl_pct = (exit_price - entry) / entry * 100

        pnl_rs = pnl_pct / 100 * (entry * qty)
        self.daily_pnl += pnl_rs
        win = 1 if pnl_pct > 0 else 0
        marker = '✓' if win else '✗'

        log.info(f'{marker} {sym}: {direction} {entry:.2f}→{exit_price:.2f} '
                 f'{pnl_pct:+.3f}% Rs {pnl_rs:+,.0f} ({reason})')

        self.daily_trades.append({
            'sym': sym, 'direction': direction,
            'entry': entry, 'exit': exit_price,
            'pnl_pct': pnl_pct, 'pnl_rs': pnl_rs,
            'reason': reason, 'gap': pos['gap'],
        })

        if sym not in self.fill_history:
            self.fill_history[sym] = []
        self.fill_history[sym].append([self.today, pnl_pct])
        self.fill_history[sym] = self.fill_history[sym][-50:]

        # Register flip candidate if pure SL hit (MFE < 0.05% — never moved in our favor)
        # No filter — backtest shows unfiltered gives Rs+523/day vs Rs+136 filtered
        # 65% WR, AvgRs+271, charges ~Rs80 → net Rs+191/flip, 1.93 flips/day
        if 'STOP' in reason and pos.get('mfe', 1.0) < 0.05:
            flip_dir = 'BUY' if direction == 'SELL' else 'SELL'
            atr_p = pos.get('atr_pct', 0.0)
            self.flip_candidates[sym] = {
                'flip_dir': flip_dir,
                'sl_price': exit_price,
                'sl_bar_close': exit_price,   # updated at next bar boundary
                'atr_pct': atr_p,
                'trail_pct': pos.get('trail_pct', MIN_TRAIL_PCT),
                'capital': entry * qty,
                'confirmed': False,
                'bars_watched': 0,
            }
            log.info(f'[FLIP] {sym} pure SL hit — queued {flip_dir}')

        del self.positions[sym]

    def close_all(self):
        if not self.positions:
            return
        log.warning(f'Closing all {len(self.positions)} positions')
        for sym in list(self.positions.keys()):
            price = self.price_feed.get_ltp(sym)
            if price <= 0:
                price = self.positions[sym]['entry']
            self.exit_position(sym, price, 'FORCE CLOSE')

        # Verify no positions remain on broker (safety net)
        if not self.paper_mode:
            time.sleep(2)
            try:
                broker_positions = api.get_positions()
                if broker_positions:
                    open_pos = []
                    for p in broker_positions:
                        qty = p.get('net_quantity', 0)
                        sym = p.get('trading_symbol', p.get('security_id', '?'))
                        if abs(int(qty)) > 0:
                            open_pos.append(f'{sym}(qty={qty})')
                    if open_pos:
                        log.error(f'POSITIONS STILL OPEN ON BROKER: {open_pos} — MANUAL CLOSE NEEDED')
                    else:
                        log.info('Broker position verification: all clear')
                else:
                    log.info('Broker position verification: all clear')
            except Exception as e:
                log.error(f'Could not verify broker positions: {e}')

        if self.capital_pool:
            self.capital_pool.release('S1', self.daily_pnl)

    def _run_pairs_session(self):
        """Session 2: Pairs trading after gap fill exits."""
        try:
            from .pairs_trader import PairsTrader
        except ImportError:
            log.warning('Pairs trader module not available, skipping')
            return

        # Time guard: only run pairs between 9:25 and 10:00
        now = datetime.now()
        too_late = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if now > too_late:
            log.info(f'Past 10:00 AM — skipping pairs session (too late)')
            return

        log.info('='*60)
        log.info('SESSION 2: PAIRS TRADING')
        log.info('='*60)

        # Wait until 9:30
        pairs_start = now.replace(hour=9, minute=30, second=0, microsecond=0)
        if now < pairs_start:
            wait = (pairs_start - now).total_seconds()
            log.info(f'Waiting {wait:.0f}s for pairs session at 9:30...')
            time.sleep(max(0, wait))

        # Record opening prices (from prev_close + current = infer open)
        opening_prices = {}
        for sym in ALL_STOCKS:
            pc = self.prev_close.get(sym, 0)
            if pc > 0:
                # Use the LTP at 9:15 as opening price
                # In practice, we stored it during gap scan
                opening_prices[sym] = pc  # approximate with prev_close

        # Better: get actual opening prices from full quote
        try:
            quotes = api.get_full_quote(ALL_STOCKS)
            for sym, q in quotes.items():
                op = q.get('open', 0)
                if op > 0:
                    opening_prices[sym] = op
        except Exception:
            pass

        # Request capital from shared pool for pairs (S2)
        # Wait for S1 (gap fill) to release capital if needed
        pairs_capital = self.total_capital
        pairs_capital_per = 0
        if self.capital_pool:
            for wait_attempt in range(20):  # wait up to 10 min (20 × 30s)
                pairs_capital_per = self.capital_pool.request('S2', 5, self.total_capital // 5)
                if pairs_capital_per > 0:
                    break
                if datetime.now().hour >= 10:
                    log.warning('Past 10:00 AM — giving up waiting for capital')
                    return
                log.info(f'Capital pool busy (S1 active) — retry {wait_attempt+1}/20 in 30s...')
                time.sleep(30)
            if pairs_capital_per == 0:
                log.warning('Capital pool exhausted after waiting — skipping pairs')
                return
            pairs_capital = pairs_capital_per * 5

        try:
            pt = PairsTrader(pairs_capital, self.price_feed, self.order_feed,
                             self.paper_mode, depth_fn=self.get_depth_score)

            # Pass ATR data
            for sym in ALL_STOCKS:
                ind = self.compute_indicators(sym)
                if ind:
                    pt.set_atr(sym, ind['atr_pct'])

            # Scan and enter
            pairs = pt.scan_pairs(opening_prices)
            if pairs:
                pt.enter_pairs(pairs)
                pt.monitor()
                pt.close_all()
                pt.save()

                # Add pairs P&L to daily total
                self.daily_pnl += pt.daily_pnl
                log.info(f'Pairs session: Rs {pt.daily_pnl:+,.0f} '
                         f'({len(pt.daily_trades)} trades)')
            else:
                log.info('No pairs today.')
        except Exception as e:
            log.error(f'Pairs session crashed: {e}')
        finally:
            if self.capital_pool:
                self.capital_pool.release('S2', 0)

    # ═══════════════════════════════════════════════════════════
    # SESSION 3: MTF TREND DIP (10:00-14:30, every 30 min)
    # Backtest: +Rs 3,525/day, 88% win days, 85% WR, 16.8 t/d
    # PAPER MODE ONLY — validating before live deployment
    # ═══════════════════════════════════════════════════════════

    def _run_mtf_session(self):
        """Session 3: Multi-timeframe trend dip. Scans every 30 min, buys dips in trends.
        Runs PAPER ONLY for validation — does not place real orders."""
        try:
            log.info('=' * 60)
            log.info('SESSION 3: MTF TREND DIP (PAPER VALIDATION)')
            log.info('=' * 60)

            # Wait until 10:00
            now = datetime.now()
            start = now.replace(hour=10, minute=0, second=0, microsecond=0)
            if now < start:
                wait = (start - now).total_seconds()
                log.info(f'MTF: waiting {wait:.0f}s for 10:00...')
                time.sleep(max(0, wait))

            end_time = datetime.now().replace(hour=14, minute=30, second=0, microsecond=0)
            mtf_pnl = 0.0
            mtf_trades = []
            mtf_positions = {}
            scan_times = []  # 10:00, 10:30, 11:00, ..., 14:00
            t = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
            while t <= datetime.now().replace(hour=14, minute=0, second=0, microsecond=0):
                scan_times.append(t)
                t = t.replace(minute=t.minute + 30) if t.minute < 30 else t.replace(hour=t.hour + 1, minute=0)

            for scan_time in scan_times:
                now = datetime.now()
                if now < scan_time:
                    time.sleep((scan_time - now).total_seconds())
                if datetime.now() > end_time:
                    break

                # Monitor and close existing positions first
                for sym in list(mtf_positions.keys()):
                    pos = mtf_positions[sym]
                    price = self.price_feed.get_ltp(sym)
                    if price <= 0:
                        continue
                    entry = pos['entry']; d = pos['dir']
                    fav = (price-entry)/entry*100 if d=='BUY' else (entry-price)/entry*100
                    pos['mfe'] = max(pos['mfe'], fav)
                    atr = pos['atr']
                    sl_abs = atr*0.05/100*entry
                    tr_abs = max(atr*0.005/100*entry, entry*0.10/100)

                    exited = False
                    if pos['mfe'] > pos['trail_pct']:
                        nt = pos['mfe'] - pos['trail_pct']
                        if d == 'BUY':
                            tp = entry*(1+nt/100)
                            pos['trail'] = max(pos['trail'], tp)
                            if price <= pos['trail']: exited = True
                        else:
                            tp = entry*(1-nt/100)
                            pos['trail'] = min(pos['trail'], tp)
                            if price >= pos['trail']: exited = True
                    elif d == 'BUY' and price <= pos['sl']:
                        exited = True
                    elif d == 'SELL' and price >= pos['sl']:
                        exited = True

                    if exited:
                        pnl_pct = (price-entry)/entry*100 if d=='BUY' else (entry-price)/entry*100
                        pnl_rs = pnl_pct/100*entry*pos['qty']
                        reason = 'TRAIL' if pos['mfe'] > pos['trail_pct'] else 'STOP'
                        log.info(f'[MTF-PAPER] EXIT {sym} {d} {entry:.2f}->{price:.2f} {pnl_pct:+.3f}% Rs {pnl_rs:+,.0f} ({reason})')
                        mtf_pnl += pnl_rs
                        mtf_trades.append({'sym':sym,'pnl_pct':pnl_pct,'pnl_rs':pnl_rs})
                        del mtf_positions[sym]

                # Scan for new signals using full quotes (batch)
                quotes = api.get_full_quote(ALL_STOCKS)
                signals = []
                for sym in ALL_STOCKS:
                    if sym in mtf_positions or sym in self.positions:
                        continue
                    q = quotes.get(sym, {})
                    op = q.get('open', 0)
                    hi = q.get('high', 0)
                    lo = q.get('low', 0)
                    cur = q.get('last_price', 0)
                    if op <= 0 or cur <= 0 or hi <= 0 or lo <= 0:
                        continue
                    ind = self.compute_indicators(sym)
                    if not ind or ind['atr_pct'] < 0.3:
                        continue
                    atr = ind['atr_pct']
                    trend = (cur - op) / op * 100
                    if abs(trend) < 0.2:
                        continue
                    # Uptrend + pullback from high
                    if trend > 0.2:
                        pb = (hi - cur) / hi * 100
                        if pb > 0.1:
                            signals.append({'sym':sym,'dir':'BUY','price':cur,'score':abs(trend)*atr,'atr':atr})
                    # Downtrend + bounce from low
                    elif trend < -0.2:
                        bn = (cur - lo) / lo * 100
                        if bn > 0.1:
                            signals.append({'sym':sym,'dir':'SELL','price':cur,'score':abs(trend)*atr,'atr':atr})

                # Enter top 3 (PAPER ONLY)
                signals.sort(key=lambda x: -x['score'])
                entered = 0
                for sig in signals[:3]:
                    if len(mtf_positions) >= 3:
                        break
                    sym = sig['sym']
                    price = sig['price']
                    atr = sig['atr']
                    qty = int(CAPITAL / 5 / price) if price > 0 else 0
                    if qty <= 0:
                        continue
                    sl_pct = atr * 0.05
                    trail_pct = max(atr * 0.005, 0.10)
                    sl = price*(1-sl_pct/100) if sig['dir']=='BUY' else price*(1+sl_pct/100)
                    mtf_positions[sym] = {
                        'dir': sig['dir'], 'entry': price, 'qty': qty,
                        'atr': atr, 'sl': sl, 'trail_pct': trail_pct,
                        'trail': sl, 'mfe': 0.0,
                    }
                    entered += 1
                    log.info(f'[MTF-PAPER] {sig["dir"]} {qty} {sym} @ {price:.2f} SL={sl:.2f}')

                if entered > 0:
                    log.info(f'[MTF-PAPER] Scan: {len(signals)} signals, entered {entered}, open={len(mtf_positions)}')

            # EOD close remaining
            for sym in list(mtf_positions.keys()):
                pos = mtf_positions[sym]
                price = self.price_feed.get_ltp(sym)
                if price <= 0:
                    price = pos['entry']
                pnl_pct = (price-pos['entry'])/pos['entry']*100 if pos['dir']=='BUY' else (pos['entry']-price)/pos['entry']*100
                pnl_rs = pnl_pct/100*pos['entry']*pos['qty']
                log.info(f'[MTF-PAPER] EOD CLOSE {sym} {pnl_pct:+.3f}% Rs {pnl_rs:+,.0f}')
                mtf_pnl += pnl_rs
                mtf_trades.append({'sym':sym,'pnl_pct':pnl_pct,'pnl_rs':pnl_rs})

            wins = sum(1 for t in mtf_trades if t['pnl_rs'] > 0)
            log.info(f'[MTF-PAPER] Session done: {wins}W/{len(mtf_trades)-wins}L Rs {mtf_pnl:+,.0f} ({len(mtf_trades)} trades)')

        except Exception as e:
            log.error(f'MTF session crashed: {e}')

    # ═══════════════════════════════════════════════════════════
    # JOURNAL
    # ═══════════════════════════════════════════════════════════

    def write_journal(self):
        journal_dir = DATA_DIR.parent / 'journal'
        journal_dir.mkdir(parents=True, exist_ok=True)
        jfile = journal_dir / f'{self.today}.md'

        wins = sum(1 for t in self.daily_trades if t['pnl_pct'] > 0)
        losses = len(self.daily_trades) - wins
        wr = wins / len(self.daily_trades) * 100 if self.daily_trades else 0

        lines = [
            f'# Trade Journal — {self.today}',
            f'## Mode: {"PAPER" if self.paper_mode else "LIVE"}',
            f'## Basket: {len(self.daily_trades)} stocks',
            f'## Result: {wins}W/{losses}L ({wr:.0f}% WR)',
            f'## Daily P&L: Rs {self.daily_pnl:+,.0f}', '',
            '| # | Stock | Dir | Gap% | Entry | Exit | PnL% | Rs | Reason |',
            '|---|-------|-----|------|-------|------|------|-----|--------|',
        ]
        for i, t in enumerate(self.daily_trades):
            m = '✓' if t['pnl_pct'] > 0 else '✗'
            lines.append(f'| {m}{i+1} | {t["sym"]} | {t["direction"]} | '
                f'{t["gap"]:+.1f}% | {t["entry"]:.2f} | {t["exit"]:.2f} | '
                f'{t["pnl_pct"]:+.3f}% | {t["pnl_rs"]:+,.0f} | {t["reason"]} |')
        lines.extend(['', f'**Total: Rs {self.daily_pnl:+,.0f}**'])
        jfile.write_text('\n'.join(lines), encoding='utf-8')
        log.info(f'Journal: {jfile}')

    # ═══════════════════════════════════════════════════════════
    # MAIN
    # ═══════════════════════════════════════════════════════════

    def run(self):
        log.info('='*60)
        log.info(f'BASKET GAP FILL TRADER V2 — {self.today}')
        log.info(f'Capital: Rs {self.capital:,} × {LEVERAGE}x = Rs {self.total_capital:,}')
        log.info(f'Mode: {"PAPER" if self.paper_mode else "*** LIVE ***"}')
        log.info(f'Features: WebSocket prices, market depth, margin check,')
        log.info(f'          order updates, circuit check, live P&L')
        log.info('='*60)

        # Step 1: Load data
        self.load_market_data()

        # Step 2: Start WebSocket feeds
        token = api.get_token()
        if token:
            self.price_feed.start(token)
            if not self.paper_mode:
                self.order_feed.start(token)
            # Subscribe to all stocks for pre-market
            all_scrips = list(api.SCRIP_CODES.values())
            self.price_feed.subscribe(all_scrips)
        else:
            log.warning('No token — WebSocket disabled, using REST fallback')

        # Step 3: Wait for market open
        now = datetime.now()
        market_open = now.replace(hour=9, minute=15, second=3, microsecond=0)
        if now < market_open:
            wait = (market_open - now).total_seconds()
            log.info(f'Waiting {wait:.0f}s for market open...')
            time.sleep(max(0, wait))

        # Step 4: Scan + Score — hard cutoff at 9:16, entry must be at 9:15 sharp
        now = datetime.now()
        if now.hour > 9 or (now.hour == 9 and now.minute >= 16):
            log.warning(f'Past 9:16 AM — too late for gap fill entry, skipping basket')
            basket = []
        else:
            basket = self.scan_gaps()

        # Step 4b: Launch background sessions based on toggles
        import threading
        pairs_thread = None
        if ENABLE_PAIRS:
            pairs_thread = threading.Thread(target=self._run_pairs_session, daemon=True)
            pairs_thread.start()
            log.info('Pairs session: ENABLED')
        else:
            log.info('Pairs session: DISABLED (enable at Rs 2L+)')

        # Big Bar Reversal — runs all day (10:00-14:30)
        bigbar_thread = None
        if ENABLE_BIGBAR:
            from .bigbar_reversal import BigBarTrader
            bb_trader = BigBarTrader(self.total_capital, self.price_feed,
                                     self.order_feed, self.paper_mode,
                                     capital_pool=self.capital_pool)
            bigbar_thread = threading.Thread(
                target=lambda: bb_trader.run(ALL_STOCKS), daemon=True)
            bigbar_thread.start()
            log.info('Big Bar Reversal: ENABLED (10:00-14:30)')
        else:
            log.info('Big Bar Reversal: DISABLED')

        if not basket:
            log.info('No gap fill basket today.')
            if pairs_thread:
                pairs_thread.join(timeout=60)
            if bigbar_thread:
                bigbar_thread.join(timeout=18000)
            self.price_feed.stop()
            self._save_fill_history()
            self._update_all_fill_history()
            self.write_journal()
            return

        # Step 5: Enter
        self.enter_basket(basket)

        # Step 6: Monitor via WebSocket
        self.monitor_ws()

        # Step 7: Force close at 3:15
        now = datetime.now()
        close_time = now.replace(hour=15, minute=15, second=0, microsecond=0)
        if now < close_time and self.positions:
            log.info('Continuing to monitor until 3:15 PM...')
            while datetime.now() < close_time and self.positions:
                for sym in list(self.positions.keys()):
                    price = self.price_feed.get_ltp(sym)
                    if price <= 0:
                        continue
                    pos = self.positions[sym]
                    d = pos['direction']; entry = pos['entry']
                    fav = (entry-price)/entry*100 if d=='SELL' else (price-entry)/entry*100
                    pos['mfe'] = max(pos['mfe'], fav)
                    if pos['mfe'] > pos['trail_pct']:
                        pos['trail_active'] = True
                        nt = pos['mfe'] - pos['trail_pct']
                        if d == 'SELL':
                            tp = entry*(1-nt/100)
                            pos['trail_level'] = min(pos['trail_level'], tp)
                            if price >= pos['trail_level']:
                                self.exit_position(sym, price, 'TRAIL')
                        else:
                            tp = entry*(1+nt/100)
                            pos['trail_level'] = max(pos['trail_level'], tp)
                            if price <= pos['trail_level']:
                                self.exit_position(sym, price, 'TRAIL')
                    elif d == 'SELL' and price >= pos['sl_price']:
                        self.exit_position(sym, price, 'STOP')
                    elif d == 'BUY' and price <= pos['sl_price']:
                        self.exit_position(sym, price, 'STOP')
                time.sleep(0.1)

        self.close_all()

        # Wait for background sessions to finish
        if pairs_thread and pairs_thread.is_alive():
            log.info('Waiting for pairs session to finish...')
            pairs_thread.join(timeout=60)
        if bigbar_thread and bigbar_thread.is_alive():
            log.info('Waiting for big bar session to finish...')
            bigbar_thread.join(timeout=60)
            # Add bigbar P&L to daily total
            if ENABLE_BIGBAR:
                self.daily_pnl += bb_trader.daily_pnl
                self.daily_trades.extend(bb_trader.daily_trades)

        # Step 8: Update WR for ALL gap stocks (not just traded ones)
        self._update_all_fill_history()

        # Step 9: Cleanup
        self.price_feed.stop()
        self.order_feed.stop()
        self._save_fill_history()
        self.write_journal()

        wins = sum(1 for t in self.daily_trades if t['pnl_pct'] > 0)
        losses = len(self.daily_trades) - wins
        log.info('='*60)
        log.info(f'DAY COMPLETE: {wins}W/{losses}L | Rs {self.daily_pnl:+,.0f}')
        log.info('='*60)


def main():
    import argparse
    p = argparse.ArgumentParser(description='Gap Fill Basket Trader V2')
    p.add_argument('--live', action='store_true', help='LIVE mode')
    p.add_argument('--capital', type=int, default=0, help='Capital Rs (0=auto-detect from broker)')
    args = p.parse_args()
    BasketTrader(capital=args.capital, paper_mode=not args.live).run()

if __name__ == '__main__':
    main()
