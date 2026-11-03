"""Market Lens Agent — SHORT freshest gap-down stocks with zero buying resistance.

Entry at 10:30 AM (scan 75):
  1. Gap down >1%
  2. lower_lows_pct >50%
  3. below_vwap_pct >70%
  4. bull_bars <4
  5. move_since_open 1-3%
  6. delivery_change >0
  7. volume >= 5000
  Rank by smallest move (freshest), take TOP 5

Exit: SL 1.5%, trail +0.3%/0.3%, square-off 3:15 PM.
Validated: 37W 0L = 100% WR over 18 days.
"""
import time
from datetime import datetime
from dataclasses import dataclass, field

from trading.base_agent import BaseAgent
from trading.types import TradeRequest, Side
from trading.logger import get_logger, tg_alert


# Config
GAP_THRESHOLD = -1.0
MIN_LOWER_LOWS = 50
MIN_BELOW_VWAP = 70
MAX_BULL_BARS = 4
MIN_MOVE = 1.0
MAX_MOVE = 3.0
MIN_VOLUME = 5000
ENTRY_HOUR, ENTRY_MIN = 10, 30
MAX_POSITIONS = 5
SL_PCT = 1.5
TRAIL_ACTIVATE = 0.3
TRAIL_DISTANCE = 0.3


@dataclass
class StockState:
    ticker: str
    open_price: float = 0
    prev_close: float = 0
    gap_pct: float = 0
    below_vwap_count: int = 0
    total_scans: int = 0
    bounced_above_open: bool = False
    bar_buf: list = field(default_factory=list)
    bars: list = field(default_factory=list)
    dlv_first: float = 0
    dlv_last: float = 0
    volume: int = 0
    last_ltp: float = 0

    def update(self, ltp, vwap, volume=0, dlv=0):
        if ltp <= 0:
            return
        self.total_scans += 1
        self.last_ltp = ltp
        if ltp > self.open_price * 1.002:
            self.bounced_above_open = True
        if vwap > 0 and ltp < vwap:
            self.below_vwap_count += 1
        if volume > 0:
            self.volume = volume
        if dlv > 0:
            if self.dlv_first == 0 or self.dlv_last == 0:
                self.dlv_first = dlv
            self.dlv_last = dlv
        self.bar_buf.append(ltp)
        if len(self.bar_buf) >= 5:
            bp = self.bar_buf
            self.bars.append({'o': bp[0], 'h': max(bp), 'l': min(bp), 'c': bp[-1]})
            self.bar_buf = []

    @property
    def below_vwap_pct(self):
        return (self.below_vwap_count / self.total_scans * 100) if self.total_scans > 0 else 0

    @property
    def bull_bars(self):
        return sum(1 for b in self.bars if b['c'] > b['o'])

    @property
    def lower_lows_pct(self):
        if len(self.bars) < 2:
            return 0
        ll = sum(1 for i in range(1, len(self.bars)) if self.bars[i]['l'] < self.bars[i-1]['l'])
        return ll / (len(self.bars) - 1) * 100

    @property
    def move_since_open(self):
        if self.open_price <= 0 or self.last_ltp <= 0:
            return 0
        return abs((self.last_ltp - self.open_price) / self.open_price * 100)

    @property
    def delivery_change(self):
        if self.dlv_first == 0 or self.dlv_last == 0:
            return 0
        return self.dlv_last - self.dlv_first

    def qualifies(self):
        if self.gap_pct > GAP_THRESHOLD:
            return False
        if self.bounced_above_open:
            return False
        if self.below_vwap_pct < MIN_BELOW_VWAP:
            return False
        if self.lower_lows_pct < MIN_LOWER_LOWS:
            return False
        if self.bull_bars >= MAX_BULL_BARS:
            return False
        if len(self.bars) < 5:
            return False
        if self.move_since_open < MIN_MOVE or self.move_since_open > MAX_MOVE:
            return False
        if self.delivery_change <= 0:
            return False
        if self.volume < MIN_VOLUME:
            return False
        return True


class MarketLensAgent(BaseAgent):
    name = 'market_lens'

    def __init__(self, broker, positions, fund_manager, monitor):
        super().__init__(broker, positions, fund_manager, monitor)
        self._stocks = {}
        self._entered = set()
        self._scan_count = 0

    def run(self):
        while True:
            try:
                self._run_day()
            except Exception as e:
                self._log.error(f'Day failed: {e}')
                import traceback; traceback.print_exc()
            self._sleep_until_next_day()

    def _sleep_until_next_day(self):
        from datetime import timedelta
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=18, second=0)
        while tomorrow.weekday() >= 5:
            tomorrow += timedelta(days=1)
        wait = (tomorrow - datetime.now()).total_seconds()
        if wait > 0:
            self._log.info(f'Sleeping {wait/3600:.1f}h until {tomorrow.strftime("%Y-%m-%d %H:%M")}')
            time.sleep(wait)

    def _run_day(self):
        now = datetime.now()
        if now.hour >= 15 or now.weekday() >= 5:
            self._log.info('Past 3 PM or weekend, skipping')
            return

        self._stocks.clear()
        self._entered.clear()
        self._scan_count = 0

        self._log.info('=== MARKET LENS AGENT START ===')

        while datetime.now().hour < 9 or (datetime.now().hour == 9 and datetime.now().minute < 16):
            time.sleep(30)

        entry_done = False
        while True:
            now = datetime.now()
            if now.hour > 15 or (now.hour == 15 and now.minute >= 15):
                break

            stocks = self._fetch_market_lens()
            if stocks:
                self._scan_count += 1
                self._update_states(stocks)

                if not entry_done and now.hour == ENTRY_HOUR and now.minute >= ENTRY_MIN:
                    self._check_entries()
                    entry_done = True

            # Stop polling after entry — monitor handles trail/SL/EOD
            if entry_done:
                self._log.info(f"Entry done. {len(self._entered)} positions. Monitor takes over.")
                break

            time.sleep(60)

        self._log.info('=== MARKET LENS AGENT END ===')

    def _fetch_market_lens(self):
        try:
            from trading.services.ml_enricher import _poll, _cache
            import trading.services.ml_enricher as mle
            mle._cache_time = 0
            _poll()

            stocks = []
            for ticker, data in mle._cache.items():
                ltp = data.get('lastTradedPrice', 0)
                prev = data.get('previousClose', 0)
                op = data.get('openPrice', 0) or 0
                if not ltp or ltp <= 0 or not prev or prev <= 0 or op <= 0:
                    continue
                stocks.append({
                    'ticker': ticker,
                    'ltp': ltp,
                    'vwap': data.get('avgPrice', 0) or 0,
                    'open': op,
                    'prev_close': prev,
                    'volume': data.get('volume', 0) or 0,
                    'dlv': data.get('deliveryPercentage', 0) or 0,
                })
            return stocks
        except Exception as e:
            self._log.error(f'ML fetch failed: {e}')
            return []

    def _update_states(self, stocks):
        for s in stocks:
            tk = s['ticker']
            if tk not in self._stocks:
                op = s['open']
                prev = s['prev_close']
                gap = (op - prev) / prev * 100 if prev > 0 else 0
                self._stocks[tk] = StockState(
                    ticker=tk, open_price=op, prev_close=prev, gap_pct=gap,
                )
            self._stocks[tk].update(
                ltp=s['ltp'], vwap=s['vwap'],
                volume=s['volume'], dlv=s['dlv'],
            )

    def _check_entries(self):
        candidates = []
        for tk, state in self._stocks.items():
            if tk in self._entered:
                continue
            if state.qualifies():
                candidates.append({
                    'ticker': tk,
                    'price': state.last_ltp,
                    'move': state.move_since_open,
                    'gap': state.gap_pct,
                    'll': state.lower_lows_pct,
                    'vwap': state.below_vwap_pct,
                    'bull': state.bull_bars,
                    'dlv_chg': state.delivery_change,
                    'vol': state.volume,
                })

        # Freshness: smallest move = freshest = safest
        candidates.sort(key=lambda x: x['move'])
        max_new = MAX_POSITIONS - len(self._entered)

        if candidates:
            self._log.info(f'ML CANDIDATES: {len(candidates)} qualify')
            for c in candidates:
                self._log.info(f'  {c["ticker"]:15s} gap={c["gap"]:+.1f}% '
                               f'move={c["move"]:.1f}% ll={c["ll"]:.0f}% '
                               f'vwap={c["vwap"]:.0f}% bull={c["bull"]} '
                               f'dlv_chg={c["dlv_chg"]:+.1f}')

        for c in candidates[:max_new]:
            self._enter_short(c)

    def _enter_short(self, c):
        tk = c['ticker']
        price = c['price']
        sl = price * (1 + SL_PCT / 100)

        reason = (f'ML: gap={c["gap"]:+.1f}% move={c["move"]:.1f}% '
                  f'll={c["ll"]:.0f}% vwap={c["vwap"]:.0f}% '
                  f'bull={c["bull"]} dlv={c["dlv_chg"]:+.1f}')

        req = TradeRequest(
            symbol=tk,
            side=Side.SELL,
            entry=price,
            sl=sl,
            trail_activate_pct=TRAIL_ACTIVATE,
            trail_pct=TRAIL_DISTANCE,
            reason=reason,
        )

        pos_id = self.submit(req)
        if pos_id:
            self._entered.add(tk)
            tg_alert(f'ML SHORT: {tk} @ {price:.1f} | SL={sl:.1f} | {reason}')

            pos = self._positions.find_by_symbol(tk)
            if pos and pos.qty > 0:
                try:
                    gtt_id = self._broker.place_gtt(
                        tk, pos.qty, 'BUY',
                        round(sl, 2), round(sl * 1.001, 2),
                    )
                    if gtt_id:
                        pos.gtt_sl_id = gtt_id
                        pos._last_gtt_sl = sl
                        self._log.info(f'GTT placed: {tk} trigger={sl:.1f} id={gtt_id}')
                except Exception as e:
                    self._log.error(f'GTT failed: {tk}: {e}')
        else:
            self._log.info(f'ML REJECTED by FM: {tk}')
