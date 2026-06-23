"""Big Bar Reversal Strategy — runs all day (10:00-14:30).

Signal: 5-min bar with body > 0.7%, isolated (prev bar opposite), ALMA confirms.
Entry: MARKET order at bar close (detected via WebSocket).
Exit: Next bar close (hold 1 bar = 5 min).
GTT SL for protection.

Backtest: Rs 530/trade, 87% WR, ~6 trades/day.
"""
import sys, io, time, math, logging, threading
from datetime import datetime
from collections import defaultdict

from . import indmoney_client as api

log = logging.getLogger('bigbar')

# Strategy params
MIN_BODY_PCT = 0.7     # minimum body size as % of price
ALMA_WINDOW = 9
ALMA_OFFSET = 0.85
ALMA_SIGMA = 6
BB_PERIOD = 20
BB_MULT = 2
MAX_POSITIONS = 3
HOLD_BARS = 1          # hold 1 bar (5 min), exit at next bar close
EMERGENCY_SL_PCT = 0.5 # emergency SL if price moves 0.5% against


def alma_calc(data, window=ALMA_WINDOW, offset=ALMA_OFFSET, sigma=ALMA_SIGMA):
    m = offset * (window - 1); s = window / sigma
    weights = [math.exp(-((i - m) ** 2) / (2 * s * s)) for i in range(window)]
    ws = sum(weights); weights = [w / ws for w in weights]
    result = []
    for i in range(len(data)):
        if i < window - 1: result.append(data[i])
        else: result.append(sum(data[i - window + 1 + j] * weights[j] for j in range(window)))
    return result


def bb_mid(data, period=BB_PERIOD):
    result = []
    for i in range(len(data)):
        if i < period - 1: result.append(data[i])
        else: result.append(sum(data[i - period + 1:i + 1]) / period)
    return result


MIN_CAPITAL_PER_TRADE = 20000  # Rs 20K minimum per trade (with leverage)


class BigBarTrader:
    def __init__(self, capital, price_feed, order_feed, paper_mode=True, capital_pool=None):
        self.capital = capital
        self.price_feed = price_feed
        self.order_feed = order_feed
        self.paper_mode = paper_mode
        self.capital_pool = capital_pool  # shared with gap fill
        self.positions = {}       # sym -> position dict
        self.prev_bars = {}       # sym -> list of recent bar closes (for ALMA/BB)
        self.current_bar = {}     # sym -> {'o','h','l','c'} building current bar
        self.bar_count = 0        # how many bars have elapsed today
        self.last_bar_time = 0    # timestamp of last bar close
        self.daily_pnl = 0.0
        self.daily_trades = []
        self.exited_cooldown = {} # sym -> time (don't re-enter same bar)

    def load_history(self, sym):
        """Load recent bar history from API for ALMA calculation."""
        try:
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - 8 * 3600 * 1000  # 8 hours
            candles = api.get_historical_candles(sym, "5minute", start_ms, now_ms)
            if candles:
                self.prev_bars[sym] = [c['c'] for c in candles]
                return True
        except Exception as e:
            log.error(f'History load error for {sym}: {e}')
        return False

    def on_bar_close(self, sym, bar):
        """Called when a 5-min bar completes. This is where signals are detected."""
        # Update history
        if sym not in self.prev_bars:
            self.prev_bars[sym] = []
        self.prev_bars[sym].append(bar['c'])
        # Keep last 50 bars
        self.prev_bars[sym] = self.prev_bars[sym][-50:]

        closes = self.prev_bars[sym]
        if len(closes) < 20:
            return None  # not enough history

        # Check existing positions — exit after HOLD_BARS
        if sym in self.positions:
            pos = self.positions[sym]
            # If GTT entry, check if it actually filled on broker
            if pos.get('gtt_entry') and pos.get('pending'):
                try:
                    broker_pos = api.get_positions()
                    filled = any(
                        (p.get('symbol', '') == sym or p.get('trading_symbol', '') == sym)
                        and abs(int(p.get('net_quantity', p.get('net_qty', 0)))) > 0
                        for p in broker_pos) if broker_pos else False
                    if filled:
                        pos['pending'] = False
                        log.info(f'[BIGBAR] GTT entry filled for {sym}')
                    else:
                        pos['bars_waited'] = pos.get('bars_waited', 0) + 1
                        if pos['bars_waited'] >= 2:
                            # GTT didn't fire in 2 bars — cancel and remove
                            oid = pos.get('order_id', '')
                            if oid and not self.paper_mode:
                                api.cancel_smart_order(oid)
                            log.info(f'[BIGBAR] GTT expired for {sym} — cancelled')
                            del self.positions[sym]
                        return None
                except Exception:
                    pass

            pos['bars_held'] += 1
            if pos['bars_held'] >= HOLD_BARS:
                return {'action': 'EXIT', 'sym': sym, 'price': bar['c'],
                        'reason': f'HOLD {HOLD_BARS} bars'}
            return None

        # Don't enter if we already have max positions
        if len(self.positions) >= MAX_POSITIONS:
            return None

        # Cooldown check
        if sym in self.exited_cooldown:
            if time.time() - self.exited_cooldown[sym] < 300:  # 5 min cooldown
                return None

        # === SIGNAL DETECTION ===
        body = bar['c'] - bar['o']
        body_pct = abs(body) / bar['c'] * 100 if bar['c'] > 0 else 0

        # Filter 1: body size
        if body_pct < MIN_BODY_PCT:
            return None

        # Filter 2: isolated (prev bar opposite direction)
        if len(closes) >= 2:
            prev_close = closes[-2]
            prev_prev = closes[-3] if len(closes) >= 3 else prev_close
            prev_body = prev_close - prev_prev
            if prev_body * body > 0:  # same direction = trend, skip
                return None

        # Filter 3: ALMA confirms overextension
        alma_v = alma_calc(closes)
        bb_m = bb_mid(closes)
        if body < 0:  # big red bar → want to BUY
            # ALMA should be below BB mid (confirming downside overextension)
            if alma_v[-1] >= bb_m[-1]:
                return None
            direction = 'BUY'
        else:  # big green bar → want to SELL
            if alma_v[-1] <= bb_m[-1]:
                return None
            direction = 'SELL'

        # Calculate GTT trigger price (5% of bar range above/below close)
        rng = bar['h'] - bar['l']
        trigger_dist = rng * 0.05
        if direction == 'BUY':
            trigger_price = bar['c'] + trigger_dist  # bounce UP to trigger
        else:
            trigger_price = bar['c'] - trigger_dist  # bounce DOWN to trigger

        return {'action': 'ENTER', 'sym': sym, 'direction': direction,
                'price': bar['c'], 'trigger_price': round(trigger_price, 2),
                'body_pct': body_pct, 'range': rng}

    def get_available_capital(self):
        """Get available capital directly from exchange, with 5% buffer."""
        try:
            funds = api.get_funds()
            return funds * 0.95 * 5  # 5% buffer + leverage
        except Exception:
            return 0

    def enter_position(self, sym, direction, price, qty, trigger_price=None):
        """Enter a position via GTT (bounce confirmation, zero slippage)."""
        # Check available capital
        needed = price * qty
        available = self.get_available_capital()
        locked_in_positions = sum(p['entry'] * p['qty'] for p in self.positions.values())
        free = available - locked_in_positions

        if free < MIN_CAPITAL_PER_TRADE:
            log.info(f'[BIGBAR] Skip {sym}: free capital Rs {free:,.0f} < Rs {MIN_CAPITAL_PER_TRADE:,}')
            return False

        if needed > free:
            qty = int(free / price)
            if qty <= 0:
                log.info(f'[BIGBAR] Skip {sym}: not enough capital')
                return False

        entry_price = trigger_price or price

        if self.paper_mode:
            oid = f'PAPER-BB-{sym}-{int(time.time())}'
            log.info(f'[PAPER][BIGBAR] {direction} {qty} {sym} trigger={entry_price:.2f}')
        else:
            # Place GTT: trigger at bounce price, SL for protection
            sl_price = price * (1 - EMERGENCY_SL_PCT/100) if direction == 'BUY' else price * (1 + EMERGENCY_SL_PCT/100)
            gtt = api.place_smart_order(
                sym, qty, direction, entry_price,
                sl_trigger=round(sl_price, 2),
                sl_limit=round(sl_price * (0.998 if direction == 'BUY' else 1.002), 2),
            )
            if not gtt:
                # Fallback to MARKET
                log.warning(f'[BIGBAR] GTT failed for {sym}, using MARKET')
                oid = api.place_order(sym, qty, direction, price, order_type='MARKET')
                if not oid:
                    log.error(f'[BIGBAR] Failed to enter {sym}')
                    return False
                entry_price = price
            else:
                oid = gtt.get('parent', '') if isinstance(gtt, dict) else gtt
            log.info(f'[BIGBAR] GTT {direction} {qty} {sym} trigger={entry_price:.2f} -> {oid}')

        self.positions[sym] = {
            'direction': direction,
            'entry': entry_price,
            'qty': qty,
            'order_id': oid,
            'bars_held': 0,
            'entry_time': time.time(),
            'gtt_entry': trigger_price is not None,
            'pending': trigger_price is not None,  # GTT not filled yet
            'bars_waited': 0,
        }
        return True

    def exit_position(self, sym, price, reason):
        """Exit a position."""
        pos = self.positions.get(sym)
        if not pos:
            return

        direction = pos['direction']
        entry = pos['entry']
        qty = pos['qty']
        exit_side = 'SELL' if direction == 'BUY' else 'BUY'

        if self.paper_mode:
            log.info(f'[PAPER][BIGBAR] EXIT {exit_side} {qty} {sym} @ {price:.2f} ({reason})')
        else:
            # Verify position exists on broker first
            try:
                broker_pos = api.get_positions()
                still_open = any(
                    (p.get('trading_symbol', '') == sym or p.get('symbol', '') == sym)
                    and abs(int(p.get('net_quantity', p.get('net_qty', 0)))) > 0
                    for p in broker_pos) if broker_pos else False
                if not still_open:
                    log.info(f'[BIGBAR] {sym} not on broker — removing')
                    del self.positions[sym]
                    return
            except Exception:
                pass

            oid = api.place_order(sym, qty, exit_side, price, order_type='LIMIT')
            if oid:
                time.sleep(3)
                if not self.order_feed.is_filled(oid):
                    api.cancel_order(oid)
                    oid = api.place_order(sym, qty, exit_side, price, order_type='MARKET')
            if not oid:
                log.error(f'[BIGBAR] Failed to exit {sym}')

        pnl_pct = (price - entry) / entry * 100 if direction == 'BUY' else (entry - price) / entry * 100
        pnl_rs = pnl_pct / 100 * entry * qty
        self.daily_pnl += pnl_rs
        m = '+' if pnl_rs > 0 else '-'
        log.info(f'[BIGBAR] {m} {sym}: {direction} {entry:.2f}->{price:.2f} '
                 f'{pnl_pct:+.3f}% Rs {pnl_rs:+,.0f} ({reason})')

        self.daily_trades.append({
            'sym': sym, 'direction': direction,
            'entry': entry, 'exit': price,
            'pnl_pct': pnl_pct, 'pnl_rs': pnl_rs,
            'reason': reason,
        })
        self.exited_cooldown[sym] = time.time()
        del self.positions[sym]

    def check_emergency_sl(self):
        """Check emergency SL on tick data (between bar closes)."""
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            price = self.price_feed.get_ltp(sym)
            if price <= 0:
                continue
            d = pos['direction']; ep = pos['entry']
            if d == 'BUY':
                loss_pct = (ep - price) / ep * 100
            else:
                loss_pct = (price - ep) / ep * 100
            if loss_pct > EMERGENCY_SL_PCT:
                self.exit_position(sym, price, f'EMERGENCY SL ({loss_pct:.2f}%)')

    def run(self, all_stocks):
        """Main loop — runs from 10:00 to 14:30."""
        log.info('=' * 60)
        log.info('BIG BAR REVERSAL — starting')
        log.info(f'Params: body>{MIN_BODY_PCT}%, isolated, ALMA confirms')
        log.info(f'Max {MAX_POSITIONS} positions, hold {HOLD_BARS} bar(s)')
        log.info('=' * 60)

        # Wait for 10:00
        now = datetime.now()
        start = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if now < start:
            wait = (start - now).total_seconds()
            log.info(f'Waiting {wait:.0f}s for 10:00...')
            time.sleep(max(0, wait))

        end_time = datetime.now().replace(hour=14, minute=30, second=0, microsecond=0)

        # Load history for all stocks
        log.info('Loading bar history...')
        loaded = 0
        for sym in all_stocks:
            if self.load_history(sym):
                loaded += 1
        log.info(f'Loaded history for {loaded} stocks')

        # Main loop — check every 5 min (on bar close)
        BAR_INTERVAL = 300  # 5 min
        last_bar = time.time()
        last_status = 0

        while datetime.now() < end_time:
            now_t = time.time()

            # Emergency SL check every 2 seconds
            if self.positions:
                self.check_emergency_sl()

            # Bar close check every 5 min
            if now_t - last_bar >= BAR_INTERVAL:
                last_bar = now_t
                self.bar_count += 1
                log.info(f'--- Bar {self.bar_count} close ---')

                # Get current prices and build bars
                for sym in all_stocks:
                    ltp = self.price_feed.get_ltp(sym)
                    if ltp <= 0:
                        continue

                    # Fetch last 5-min bar from API
                    try:
                        now_ms = int(time.time() * 1000)
                        candles = api.get_historical_candles(sym, "5minute",
                                                             now_ms - 600000, now_ms)
                        if not candles:
                            continue
                        bar = {'o': candles[-1]['o'], 'h': candles[-1]['h'],
                               'l': candles[-1]['l'], 'c': candles[-1]['c']}
                    except Exception:
                        continue

                    # Process signal
                    signal = self.on_bar_close(sym, bar)
                    if not signal:
                        continue

                    if signal['action'] == 'ENTER':
                        qty = int(self.capital / 5 / signal['price'])
                        if qty > 0:
                            self.enter_position(sym, signal['direction'],
                                                signal['price'], qty,
                                                trigger_price=signal.get('trigger_price'))

                    elif signal['action'] == 'EXIT':
                        self.exit_position(sym, signal['price'], signal['reason'])

                # Status
                if self.positions:
                    for sym, pos in self.positions.items():
                        ltp = self.price_feed.get_ltp(sym)
                        if ltp > 0:
                            d = pos['direction']; ep = pos['entry']
                            pnl = (ltp-ep)/ep*100 if d=='BUY' else (ep-ltp)/ep*100
                            log.info(f'  {sym}: {d} {ep:.2f} now={ltp:.2f} {pnl:+.2f}%')

                wins = sum(1 for t in self.daily_trades if t['pnl_rs'] > 0)
                total = len(self.daily_trades)
                log.info(f'  Trades: {wins}W/{total-wins}L Rs {self.daily_pnl:+,.0f}')

            # Print P&L every 30s
            if now_t - last_status > 30 and self.positions:
                last_status = now_t

            time.sleep(2)

        # EOD close remaining
        if self.positions:
            log.info('EOD closing remaining positions')
            for sym in list(self.positions.keys()):
                price = self.price_feed.get_ltp(sym)
                if price <= 0:
                    price = self.positions[sym]['entry']
                self.exit_position(sym, price, 'EOD CLOSE')

        wins = sum(1 for t in self.daily_trades if t['pnl_rs'] > 0)
        total = len(self.daily_trades)
        wr = wins / total * 100 if total else 0
        log.info(f'[BIGBAR] Day done: {wins}W/{total-wins}L WR={wr:.0f}% Rs {self.daily_pnl:+,.0f}')
        return self.daily_pnl, self.daily_trades
