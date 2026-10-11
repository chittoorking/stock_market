"""Pre-compute swing points, max bounce and fr200 for every stock.
Updated every minute from ML cache. Anomaly pullback scans pull from here.
"""
import threading
import time
from collections import defaultdict
from trading.logger import get_logger

log = get_logger('price_tracker')

_stocks = {}   # ticker -> StockTracker
_lock = threading.Lock()
_running = False


class StockTracker:
    """Track price action for a single stock since open."""
    __slots__ = ['ticker', 'prices', 'peak', 'peak_idx', 'swings', 'max_bounce',
                 'local_low', 'local_low_idx', 'direction']

    def __init__(self, ticker):
        self.ticker = ticker
        self.prices = []           # all LTPs since open
        self.peak = 0              # highest price
        self.peak_idx = 0          # index of peak in prices
        self.swings = []           # [(low, high, bounce%), ...] archived swing points
        self.max_bounce = 0        # max bounce from any local low after peak
        self.local_low = 0         # current local low after peak
        self.local_low_idx = 0     # index of current local low
        self.direction = None      # 'UP' or 'DOWN' after peak

    def update(self, ltp):
        idx = len(self.prices)
        self.prices.append(ltp)

        # New peak
        if ltp > self.peak:
            self.peak = ltp
            self.peak_idx = idx
            self.local_low = ltp
            self.local_low_idx = idx
            self.direction = None
            return

        # After peak: track swings
        if idx <= self.peak_idx:
            return

        if ltp <= self.local_low:
            # Making new low — archive previous swing if we were going UP
            if self.direction == 'UP' and self.local_low > 0:
                # We were bouncing up, now reversing down again
                # The previous up-move is a completed swing
                prev_high = max(self.prices[self.local_low_idx:idx])
                bounce = (prev_high - self.local_low) / self.local_low * 100
                if bounce > 0.1:  # ignore tiny noise
                    self.swings.append({
                        'low': self.local_low,
                        'low_idx': self.local_low_idx,
                        'high': prev_high,
                        'bounce': round(bounce, 2),
                    })
                    if bounce > self.max_bounce:
                        self.max_bounce = bounce

            self.local_low = ltp
            self.local_low_idx = idx
            self.direction = 'DOWN'
        elif ltp > self.local_low:
            self.direction = 'UP'
            # Track live bounce from current local low
            bounce = (ltp - self.local_low) / self.local_low * 100 if self.local_low > 0 else 0
            if bounce > self.max_bounce:
                self.max_bounce = bounce


def start():
    """Start background tracking thread."""
    global _running
    if _running:
        return
    _running = True
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    log.info('Price tracker started')


def _poll_loop():
    """Poll ML cache every 60s, update tracking for all stocks."""
    while True:
        try:
            _update()
        except Exception as e:
            log.error(f'Price tracker error: {e}')
        time.sleep(60)


def _update():
    """Snapshot all LTPs from ML cache, update trackers."""
    try:
        import trading.services.ml_enricher as mle
        cache = mle._cache
    except Exception:
        return

    with _lock:
        for ticker, data in cache.items():
            ltp = data.get('lastTradedPrice', 0)
            if not ltp or ltp <= 0:
                continue

            if ticker not in _stocks:
                _stocks[ticker] = StockTracker(ticker)

            _stocks[ticker].update(ltp)


def get_max_bounce(ticker):
    """Get max bounce % for a ticker. Returns 0 if not tracked."""
    with _lock:
        st = _stocks.get(ticker)
        return st.max_bounce if st else 0


def get_swings(ticker):
    """Get all archived swing points for a ticker."""
    with _lock:
        st = _stocks.get(ticker)
        return list(st.swings) if st else []


def get_peak(ticker):
    """Get day's peak price for a ticker."""
    with _lock:
        st = _stocks.get(ticker)
        return st.peak if st else 0


def get_price_count(ticker):
    """Get number of price ticks tracked."""
    with _lock:
        st = _stocks.get(ticker)
        return len(st.prices) if st else 0


def reset():
    """Reset all tracking. Call at start of each trading day."""
    with _lock:
        _stocks.clear()
    log.info('Price tracker reset')
