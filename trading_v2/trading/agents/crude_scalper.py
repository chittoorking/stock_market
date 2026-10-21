"""Crude Oil Mini Scalper — MCX commodity via INDmoney API.

Strategy: 5-min window, first 1-min candle direction, pullback entry, trail exit.
Backtest: 25 days, 499 trades, 87% WR, Rs 5,796/day net, zero losing days.

Candle: if range >= Rs 150 and body shows direction, place limit order at
opposite end of candle. Trail at 25% activate / 10% step. SL = full range.
"""
import time
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path

from trading.logger import get_logger, audit, tg_alert

log = get_logger('crude_scalper')

# ── Config ──────────────────────────────────────────────────────
SECURITY_ID = '573423'          # CRUDEOILM Nov 2026 FUTCOM
SYMBOL = 'CRUDEOILM'
LOT_SIZE = 40                    # 40 barrels per lot
MIN_RANGE = 8                 # Rs per barrel minimum candle range
TRAIL_ACTIVATE_PCT = 0.25       # 25% of candle range
TRAIL_STEP_PCT = 0.10           # 10% of candle range
WINDOW_MINUTES = 5              # 5-min window
CANDLE_MINUTES = 1              # 1-min first candle
MAX_WAIT_BARS = 4               # remaining bars to fill + exit
POLL_INTERVAL = 1               # seconds between LTP polls
START_HOUR = 9                  # MCX Silver starts 9:00 AM
END_HOUR = 23                   # MCX Silver ends 11:30 PM
END_MINUTE = 25
PAPER_MODE = True               # Start in paper mode

# ── State ───────────────────────────────────────────────────────
class Candle:
    def __init__(self):
        self.open = 0
        self.high = 0
        self.low = 999999
        self.close = 0
        self.volume = 0
        self.start_time = None

    def update(self, price, vol=0):
        if self.open == 0:
            self.open = price
            self.start_time = datetime.now()
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += vol

    @property
    def range(self):
        return self.high - self.low

    @property
    def direction(self):
        return 'UP' if self.close > self.open else 'DOWN'

    @property
    def body_pct(self):
        return abs(self.close - self.open) / self.range * 100 if self.range > 0 else 0


class CrudeScalper:
    def __init__(self):
        self._token = ''
        self._headers = {}
        self._position = None  # {side, entry, sl, trail_act, trail_step, peak, trail_on, order_id}
        self._pending_order = None  # {side, price, order_id, placed_at}
        self._current_candle = Candle()
        self._candle_count = 0  # candles within current 5-min window
        self._first_candle = None  # saved first candle of window
        self._daily_trades = 0
        self._daily_pnl = 0
        self._today = ''
        self._load_token()

    def _load_token(self):
        try:
            self._token = Path('/home/ai18developer/news-trading/data/indmoney_token.txt').read_text().strip()
            self._headers = {
                'Authorization': self._token,
                'Content-Type': 'application/json',
            }
        except Exception as e:
            log.error(f'Token load failed: {e}')

    def _get_ltp(self):
        """Get Silver Mini LTP from INDmoney."""
        try:
            r = requests.get(
                f'https://api.indstocks.com/market/quotes/ltp?scrip-codes=MCX_{SECURITY_ID}',
                headers=self._headers, timeout=10)
            if r.status_code == 200:
                data = r.json().get('data', {})
                return float(data.get('MCX_' + SECURITY_ID, {}).get('live_price', 0) or 0)
            if r.status_code == 401:
                self._load_token()
        except Exception as e:
            log.warning(f'LTP error: {e}')
        return 0

    def _place_order(self, side, price, qty=LOT_SIZE):
        """Place MCX LIMIT order."""
        order = {
            'txn_type': side,
            'exchange': 'MCX',
            'segment': 'COMMODITY',
            'product': 'MARGIN',
            'order_type': 'LIMIT',
            'validity': 'DAY',
            'security_id': SECURITY_ID,
            'qty': qty,
            'limit_price': round(price, 1),
            'algo_id': '99999',
        }

        if PAPER_MODE:
            oid = f'PAPER-{int(time.time())}'
            log.info(f'PAPER {side} {qty} @ {price}')
            audit('crude_scalper', 'PAPER_ORDER', SYMBOL, side=side, qty=qty, price=price, order_id=oid)
            return oid

        try:
            r = requests.post('https://api.indstocks.com/smart/order',
                            headers=self._headers, json=order, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if data.get('status') == 'success':
                    oid = data.get('data', {}).get('order_data', [{}])[0].get('order_id', '')
                    log.info(f'ORDER {side} {qty} @ {price} -> {oid}')
                    audit('crude_scalper', 'ORDER_OK', SYMBOL, side=side, qty=qty, price=price, order_id=oid)
                    return oid
            log.error(f'ORDER FAILED: {r.text[:200]}')
        except Exception as e:
            log.error(f'ORDER ERROR: {e}')
        return ''

    def _cancel_order(self, order_id):
        """Cancel pending order."""
        if PAPER_MODE or not order_id:
            return
        try:
            r = requests.post('https://api.indstocks.com/smart/order/cancel',
                            headers=self._headers,
                            json={'order_id': order_id, 'segment': 'COMMODITY'},
                            timeout=10)
            log.info(f'CANCEL {order_id}: {r.status_code}')
        except Exception as e:
            log.warning(f'CANCEL ERROR: {e}')

    def _check_fill(self, order_id):
        """Check if order filled. Returns (filled, fill_price)."""
        if PAPER_MODE:
            # In paper mode, check if LTP touched our limit price
            return False, 0

        try:
            r = requests.get('https://api.indstocks.com/order-book?segment=COMMODITY',
                           headers=self._headers, timeout=10)
            if r.status_code == 200:
                for o in r.json().get('data', []):
                    if str(o.get('id', '')) == str(order_id) or str(o.get('order_id', '')) == str(order_id):
                        status = str(o.get('status', '')).upper()
                        if status in ('SUCCESS', 'COMPLETE', 'FILLED'):
                            price = float(o.get('traded_price', 0) or 0)
                            return True, price
        except Exception as e:
            log.warning(f'FILL CHECK ERROR: {e}')
        return False, 0

    def _close_position(self, price, reason=''):
        """Close current position."""
        if not self._position:
            return

        pos = self._position
        side = pos['side']
        exit_side = 'SELL' if side == 'BUY' else 'BUY'

        if not PAPER_MODE:
            self._place_order(exit_side, price)

        if side == 'BUY':
            pnl = (price - pos['entry']) * LOT_SIZE
        else:
            pnl = (pos['entry'] - price) * LOT_SIZE

        net = pnl - 195  # charges
        self._daily_pnl += net
        self._daily_trades += 1

        result = 'WIN' if net > 0 else 'LOSS'
        log.info(f'EXIT {reason}: {side} entry={pos["entry"]} exit={price} pnl=Rs {pnl:.0f} net=Rs {net:.0f} {result}')
        audit('crude_scalper', 'EXIT', SYMBOL, side=side, entry=pos['entry'], exit=round(price, 1),
              pnl=round(pnl, 0), net=round(net, 0), reason=reason, result=result,
              daily_trades=self._daily_trades, daily_pnl=round(self._daily_pnl, 0))
        tg_alert(f'SILVER {reason}: {side} pnl=Rs {net:+.0f} ({result}) | Day: {self._daily_trades} trades Rs {self._daily_pnl:+.0f}')

        self._position = None

    # ── Main Loop ──────────────────────────────────────────────

    def run(self):
        log.info(f'Silver Scalper started ({"PAPER" if PAPER_MODE else "LIVE"} mode)')
        log.info(f'Config: range>={MIN_RANGE}, trail={TRAIL_ACTIVATE_PCT}/{TRAIL_STEP_PCT}, window={WINDOW_MINUTES}min')
        tg_alert(f'Silver Scalper started ({"PAPER" if PAPER_MODE else "LIVE"})')

        while True:
            try:
                self._run_session()
            except Exception as e:
                log.error(f'Session error: {e}')
                import traceback; traceback.print_exc()

            # Sleep until next trading day
            self._sleep_until_next_session()

    def _sleep_until_next_session(self):
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=START_HOUR, minute=0, second=0)
        while tomorrow.weekday() >= 5:  # skip weekends
            tomorrow += timedelta(days=1)
        wait = (tomorrow - datetime.now()).total_seconds()
        if wait > 0:
            log.info(f'Sleeping {wait/3600:.1f}h until {tomorrow.strftime("%Y-%m-%d %H:%M")}')
            time.sleep(wait)

    def _run_session(self):
        now = datetime.now()
        if now.weekday() >= 5:
            return

        # Reset daily stats
        today = now.strftime('%Y-%m-%d')
        if today != self._today:
            self._today = today
            self._daily_trades = 0
            self._daily_pnl = 0
            log.info(f'=== NEW DAY {today} ===')

        # Wait for market open
        market_open = now.replace(hour=START_HOUR, minute=0, second=0)
        if now < market_open:
            wait = (market_open - now).total_seconds()
            log.info(f'Waiting {wait:.0f}s for market open')
            time.sleep(wait)

        log.info('Session started')

        # Main loop: build candles, trade
        self._candle_count = 0
        self._first_candle = None
        self._current_candle = Candle()
        last_candle_minute = -1

        while True:
            now = datetime.now()

            # Session end
            if now.hour > END_HOUR or (now.hour == END_HOUR and now.minute >= END_MINUTE):
                # Close any open position
                if self._position:
                    ltp = self._get_ltp()
                    if ltp > 0:
                        self._close_position(ltp, 'EOD')
                # Cancel pending
                if self._pending_order:
                    self._cancel_order(self._pending_order.get('order_id', ''))
                    self._pending_order = None

                log.info(f'Session end: {self._daily_trades} trades, Rs {self._daily_pnl:+.0f}')
                tg_alert(f'Silver Scalper EOD: {self._daily_trades} trades, Rs {self._daily_pnl:+.0f}')
                return

            # Get LTP
            ltp = self._get_ltp()
            if ltp <= 0:
                time.sleep(POLL_INTERVAL)
                continue

            current_minute = now.minute

            # New minute = new candle bar
            if current_minute != last_candle_minute:
                # Save completed candle
                if self._current_candle.open > 0:
                    self._on_candle_close(self._current_candle, ltp)
                    self._candle_count += 1

                # Start new candle
                self._current_candle = Candle()
                last_candle_minute = current_minute

            # Update current candle
            self._current_candle.update(ltp)

            # Manage open position (trail/SL)
            if self._position:
                self._manage_position(ltp)

            # Check pending order fill
            if self._pending_order:
                self._check_pending(ltp)

            time.sleep(POLL_INTERVAL)

    def _on_candle_close(self, candle, current_ltp):
        """Called when a 1-min candle completes."""
        window_pos = self._candle_count % WINDOW_MINUTES

        if window_pos == 0:
            # First candle of new 5-min window
            self._first_candle = candle
            log.info(f'CANDLE: O={candle.open} H={candle.high} L={candle.low} C={candle.close} '
                    f'range={candle.range} dir={candle.direction} body={candle.body_pct:.0f}%')

            if candle.range >= MIN_RANGE and not self._position:
                self._setup_trade(candle)

        elif window_pos >= MAX_WAIT_BARS:
            # End of window — cancel unfilled order
            if self._pending_order and not self._position:
                self._cancel_order(self._pending_order.get('order_id', ''))
                log.info(f'TIMEOUT: order cancelled after {window_pos} bars')
                audit('crude_scalper', 'TIMEOUT', SYMBOL)
                self._pending_order = None

    def _setup_trade(self, candle):
        """Place limit order based on first candle direction."""
        if candle.direction == 'UP':
            entry_price = candle.low
            side = 'BUY'
        else:
            entry_price = candle.high
            side = 'SELL'

        sl_distance = candle.range
        trail_activate = candle.range * TRAIL_ACTIVATE_PCT
        trail_step = candle.range * TRAIL_STEP_PCT

        oid = self._place_order(side, entry_price)
        if oid:
            self._pending_order = {
                'side': side,
                'price': entry_price,
                'order_id': oid,
                'placed_at': datetime.now(),
                'sl_distance': sl_distance,
                'trail_activate': trail_activate,
                'trail_step': trail_step,
                'candle_range': candle.range,
            }
            log.info(f'SETUP: {side} limit @ {entry_price} range={candle.range} '
                    f'SL={sl_distance} trail_act={trail_activate:.0f} trail_step={trail_step:.0f}')
            audit('crude_scalper', 'SETUP', SYMBOL, side=side, price=entry_price,
                  range=candle.range, direction=candle.direction)

    def _check_pending(self, ltp):
        """Check if pending limit order filled."""
        po = self._pending_order
        if not po:
            return

        filled = False
        fill_price = po['price']

        if PAPER_MODE:
            # Simulate fill: did LTP touch our limit?
            if po['side'] == 'BUY' and ltp <= po['price']:
                filled = True
                fill_price = po['price']
            elif po['side'] == 'SELL' and ltp >= po['price']:
                filled = True
                fill_price = po['price']
        else:
            filled, fill_price = self._check_fill(po['order_id'])

        if filled:
            side = po['side']
            if side == 'BUY':
                sl = fill_price - po['sl_distance']
            else:
                sl = fill_price + po['sl_distance']

            self._position = {
                'side': side,
                'entry': fill_price,
                'sl': sl,
                'trail_activate': po['trail_activate'],
                'trail_step': po['trail_step'],
                'peak': fill_price,
                'trail_on': False,
                'candle_range': po['candle_range'],
            }
            self._pending_order = None

            log.info(f'FILLED: {side} @ {fill_price} SL={sl}')
            audit('crude_scalper', 'FILL', SYMBOL, side=side, entry=fill_price, sl=sl)
            tg_alert(f'SILVER ENTRY: {side} @ {fill_price}')

    def _manage_position(self, ltp):
        """Trail and SL management."""
        pos = self._position
        if not pos:
            return

        side = pos['side']

        # SL check
        if (side == 'BUY' and ltp <= pos['sl']) or (side == 'SELL' and ltp >= pos['sl']):
            self._close_position(ltp, 'SL_HIT')
            return

        # Update peak
        if side == 'BUY' and ltp > pos['peak']:
            pos['peak'] = ltp
        elif side == 'SELL' and ltp < pos['peak']:
            pos['peak'] = ltp

        # Trail
        move = (pos['peak'] - pos['entry']) if side == 'BUY' else (pos['entry'] - pos['peak'])

        if not pos['trail_on'] and move >= pos['trail_activate']:
            pos['trail_on'] = True
            log.info(f'TRAIL ON: move={move:.0f}')

        if pos['trail_on']:
            if side == 'BUY':
                trail_stop = pos['peak'] - pos['trail_step']
                if ltp <= trail_stop:
                    self._close_position(ltp, 'TRAIL_EXIT')
                    return
            else:
                trail_stop = pos['peak'] + pos['trail_step']
                if ltp >= trail_stop:
                    self._close_position(ltp, 'TRAIL_EXIT')
                    return


# ── Entry point ─────────────────────────────────────────────────
if __name__ == '__main__':
    bot = CrudeScalper()
    bot.run()
