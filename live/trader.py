"""Live trader — orchestrates scanning, entry, monitoring, exit."""
import sys
import io
import logging
import time
import json
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from . import config
from . import upstox_client as api
from . import strategy

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('live_bot.log', encoding='utf-8'),
    ]
)
log = logging.getLogger('trader')


class LiveTrader:
    def __init__(self, paper_mode=True):
        self.paper_mode = paper_mode
        self.capital = config.CAPITAL
        self.available = float(self.capital)
        self.positions = {}
        self.daily_pnl = 0
        self.today = datetime.now().strftime('%Y-%m-%d')

        # Auto-fetch capital from Upstox if not set
        if self.capital == 0 and not paper_mode:
            self.capital = self._fetch_available_margin()
            self.available = float(self.capital)

        self.daily_closes = {}
        self.daily_volumes = {}
        self.prev_day_bars = {}
        self.prev_stats = {}

    def _fetch_available_margin(self):
        """Fetch available margin from Upstox account."""
        try:
            import requests
            token = api.get_token()
            r = requests.get(f'{config.UPSTOX_BASE}/user/get-funds-and-margin',
                           headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                           timeout=10)
            if r.status_code == 200:
                equity = r.json().get('data', {}).get('equity', {})
                margin = equity.get('available_margin', 0)
                log.info(f'Fetched capital from Upstox: Rs {margin:,.0f}')
                return int(margin)
        except Exception as e:
            log.error(f'Failed to fetch margin: {e}')
        log.warning('Using default capital Rs 50,000')
        return 50000

    def load_historical(self):
        """Load last 30 days of data per stock for trends + MA convergence."""
        log.info('Loading historical data for all stocks...')
        for sym, inst in config.INSTRUMENTS.items():
            bars = api.load_previous_days(sym, num_days=30)
            if not bars:
                log.warning(f'No historical data for {sym}')
                continue

            # Group by date
            by_date = defaultdict(list)
            for b in bars:
                d = b['timestamp'][:10]
                by_date[d] = by_date.get(d, [])
                by_date[d].append(b)

            dates = sorted(by_date.keys())
            if len(dates) < 2:
                continue

            # Daily closes for trend — exclude today (incomplete)
            completed_dates = [d for d in dates if d < self.today]
            self.daily_closes[sym] = [by_date[d][-1]['close'] for d in completed_dates]

            # Daily volumes for MA convergence — exclude today
            self.daily_volumes[sym] = [sum(b['volume'] for b in by_date[d]) for d in completed_dates]

            # Previous day bars
            prev_date = dates[-1] if dates[-1] < self.today else dates[-2] if len(dates) > 1 else None
            if prev_date:
                self.prev_day_bars[sym] = by_date[prev_date]
                self.prev_stats[sym] = strategy.compute_prev_day_stats(by_date[prev_date])

            time.sleep(0.35)  # Rate limit

        log.info(f'Loaded data for {len(self.daily_closes)} stocks')

    def scan_signals(self):
        """Scan all stocks for signals at bar 10 (10:15 AM)."""
        log.info('Scanning for signals...')
        signals = []

        for sym in config.INSTRUMENTS:
            if sym in self.positions:  # Skip if already in a trade from GAP/MA
                continue
            if sym not in self.daily_closes or sym not in self.prev_stats:
                continue

            trend = strategy.compute_daily_trend(self.daily_closes[sym])
            if trend == 'SIDE':
                continue

            # Get today's bars (1-min candles, aggregate to 5-min)
            inst = config.INSTRUMENTS[sym]
            candles_1min = api.get_intraday_candles(inst, '1minute')
            if not candles_1min:
                continue

            # Aggregate 1-min to 5-min bars
            today_bars = api.aggregate_1min_to_5min(candles_1min)
            if len(today_bars) < config.SCAN_BAR + 1:
                continue

            signal = strategy.check_signal(
                sym, today_bars, self.prev_stats[sym],
                trend, self.daily_closes[sym]
            )
            if signal:
                signals.append(signal)
                log.info(f'SIGNAL: {signal["direction"]} {sym} @ {signal["entry"]} '
                         f'stop={signal["stop"]} target={signal["target"]}')

            time.sleep(0.35)

        log.info(f'Found {len(signals)} signals')
        return signals

    def enter_trade(self, signal):
        """Enter a trade (place order + stop loss)."""
        sym = signal['sym']
        direction = signal['direction']
        entry = signal['entry']

        # Position sizing: 20% of available capital
        alloc = self.available * config.SIZING
        qty = max(1, int(alloc * config.LEVERAGE / entry))
        margin = qty * entry / config.LEVERAGE

        if margin > self.available:
            log.warning(f'Insufficient capital for {sym}: need Rs {margin:,.0f}, have Rs {self.available:,.0f}')
            return False

        if self.paper_mode:
            log.info(f'[PAPER] {direction} {qty} {sym} @ {entry}')
            self.positions[sym] = {
                'signal': signal, 'qty': qty, 'margin': margin,
                'entry_order': 'PAPER', 'stop_order': 'PAPER',
                'mfe': 0, 'trail_active': False, 'target_hit': False,
            }
            self.available -= margin
            return True
        else:
            # Live: place MARKET entry order (fills immediately at best price)
            side = 'SELL' if direction == 'SHORT' else 'BUY'
            entry_oid = api.place_order(sym, qty, side, 0, order_type='MARKET')
            if not entry_oid:
                return False

            # Place SL stop loss order
            sl_side = 'BUY' if direction == 'SHORT' else 'SELL'
            sl_oid = api.place_order(sym, qty, sl_side, 0,
                                     order_type='SL-M', trigger_price=signal['stop'])

            if not sl_oid:
                log.error(f'SL order FAILED for {sym} — exiting position immediately')
                api.place_order(sym, qty, 'BUY' if side == 'SELL' else 'SELL', 0, order_type='MARKET')
                return False

            self.positions[sym] = {
                'signal': signal, 'qty': qty, 'margin': margin,
                'entry_order': entry_oid, 'stop_order': sl_oid,
                'mfe': 0, 'trail_active': False, 'target_hit': False,
            }
            self.available -= margin
            return True

    def monitor_positions(self):
        """Check all open positions and manage exits."""
        if not self.positions:
            return

        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            signal = pos['signal']

            # Get current price
            inst = config.INSTRUMENTS.get(sym)
            ltp_data = api.get_ltp([inst])
            if not ltp_data:
                continue

            # Extract LTP
            current_price = None
            for key, val in ltp_data.items():
                if 'last_price' in val:
                    current_price = val['last_price']
                    break
            if not current_price:
                continue

            # Check exit conditions
            action, exit_price, new_mfe, new_trail, new_target = strategy.check_exit(
                signal, current_price,
                pos['mfe'], pos['trail_active'], pos['target_hit']
            )

            # Update state
            pos['mfe'] = new_mfe
            pos['trail_active'] = new_trail
            pos['target_hit'] = new_target

            if action:
                self.exit_trade(sym, action, exit_price)

    def exit_trade(self, sym, reason, exit_price):
        """Exit a position."""
        pos = self.positions.get(sym)
        if not pos:
            return

        signal = pos['signal']
        entry = signal['entry']
        direction = signal['direction']
        qty = pos['qty']

        if direction == 'SHORT':
            pnl_pct = (entry - exit_price) / entry * 100
        else:
            pnl_pct = (exit_price - entry) / entry * 100

        pnl_rs = pnl_pct / 100 * qty * entry
        charges = 386 * (qty * entry / 1000000)  # Scale charges with position
        net_pnl = pnl_rs - charges

        if self.paper_mode:
            log.info(f'[PAPER] EXIT {direction} {sym}: {reason} @ {exit_price:.2f} '
                     f'PnL={pnl_pct:+.3f}% Rs {net_pnl:+,.0f}')
        else:
            # Cancel stop loss order first (skip if synced/paper/None)
            sl_oid = pos.get('stop_order')
            if sl_oid and sl_oid not in ('PAPER', 'synced', None):
                try:
                    api.cancel_order(sl_oid)
                except Exception as e:
                    log.warning(f'SL cancel failed for {sym}: {e}')

            # Place MARKET exit order
            side = 'BUY' if direction == 'SHORT' else 'SELL'
            exit_oid = api.place_order(sym, qty, side, 0, order_type='MARKET')
            if not exit_oid:
                log.error(f'EXIT ORDER FAILED for {sym} — position still open!')
                return  # Don't delete from self.positions
            log.info(f'EXIT {direction} {sym}: {reason} @ {exit_price:.2f} '
                     f'PnL={pnl_pct:+.3f}% Rs {net_pnl:+,.0f}')

        self.available += pos['margin'] + net_pnl
        self.daily_pnl += net_pnl
        del self.positions[sym]

    def close_all(self):
        """Close all positions at EOD."""
        for sym in list(self.positions.keys()):
            # Get current price
            inst = config.INSTRUMENTS.get(sym)
            ltp_data = api.get_ltp([inst])
            current_price = None
            if ltp_data:
                for key, val in ltp_data.items():
                    if 'last_price' in val:
                        current_price = val['last_price']
                        break
            if current_price:
                self.exit_trade(sym, 'eod', current_price)
            else:
                log.warning(f'Could not get price for {sym} at EOD')

    def write_journal(self):
        """Write today's journal."""
        config.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        jf = config.JOURNAL_DIR / f'{self.today}.json'
        journal = {
            'date': self.today,
            'mode': 'PAPER' if self.paper_mode else 'LIVE',
            'capital': self.capital,
            'daily_pnl': round(self.daily_pnl, 2),
            'final_capital': round(self.available, 2),
        }
        jf.write_text(json.dumps(journal, indent=2))
        log.info(f'Journal: {jf}')

    def scan_gap_signals(self):
        """Scan for gap fill signals at 9:20 AM (after first bar)."""
        log.info('Scanning for GAP FILL signals...')
        signals = []

        for sym in config.INSTRUMENTS:
            if sym in self.positions:  # Skip if already in a trade
                continue
            if sym not in self.daily_closes:
                continue

            # Get prev close
            closes = self.daily_closes[sym]
            if len(closes) < 2:
                continue
            prev_close = closes[-1]  # Last completed day's close

            # Get today's first bar
            inst = config.INSTRUMENTS[sym]
            candles = api.get_intraday_candles(inst, '1minute')
            if not candles:
                continue

            # Aggregate to 5-min (need at least 1 bar)
            bars_5min = api.aggregate_1min_to_5min(candles)
            if not bars_5min:
                continue

            signal = strategy.check_gap_signal(sym, bars_5min, prev_close)
            if signal:
                signals.append(signal)
                log.info(f'GAP SIGNAL: {signal["direction"]} {sym} gap={signal["gap"]}% '
                         f'entry={signal["entry"]} target={signal["target"]}')

            time.sleep(0.35)

        log.info(f'Found {len(signals)} gap signals')
        return signals

    def scan_ma_convergence(self, bar_start, bar_end):
        """Scan for MA convergence signals."""
        log.info(f'Scanning for MA CONVERGENCE signals (bar {bar_start}-{bar_end})...')
        signals = []

        for sym in config.INSTRUMENTS:
            if sym in self.positions:  # Skip if already in a trade
                continue
            if sym not in self.daily_closes:
                continue

            trend = strategy.compute_daily_trend(self.daily_closes[sym])
            if trend == 'SIDE':
                continue

            # Get daily volumes
            daily_volumes = self.daily_volumes.get(sym, [])

            # Get today's bars
            inst = config.INSTRUMENTS[sym]
            candles = api.get_intraday_candles(inst, '1minute')
            if not candles:
                continue
            today_bars = api.aggregate_1min_to_5min(candles)

            signal = strategy.check_ma_convergence(
                sym, self.daily_closes[sym], daily_volumes,
                today_bars, trend, bar_start, bar_end
            )
            if signal:
                signals.append(signal)
                log.info(f'MA CONV SIGNAL: {signal["direction"]} {sym} '
                         f'strategy={signal["strategy"]} entry={signal["entry"]}')

            time.sleep(0.35)

        log.info(f'Found {len(signals)} MA convergence signals')
        return signals

    def _sync_existing_positions(self):
        """Check Upstox for open positions on startup. Populate self.positions so we monitor them."""
        if self.paper_mode:
            return
        try:
            positions = api.get_positions()
            if not positions:
                return
            for p in positions:
                qty = p.get('quantity', 0)
                sym_raw = p.get('trading_symbol', '')
                sym = sym_raw.replace('-EQ', '')
                if qty == 0 or sym not in config.INSTRUMENTS:
                    continue
                direction = 'SHORT' if qty < 0 else 'LONG'
                # average_price is often 0 from Upstox — use sell_price for SHORT, buy_price for LONG
                if direction == 'SHORT':
                    entry = abs(p.get('sell_price', 0) or p.get('average_price', 0))
                else:
                    entry = abs(p.get('buy_price', 0) or p.get('average_price', 0))
                if entry == 0:
                    entry = p.get('last_price', 0)
                # Build a minimal signal for monitoring
                if direction == 'SHORT':
                    stop = entry * (1 + 1.5 / 100)
                    target = entry * (1 - 1.75 / 100)
                else:
                    stop = entry * (1 - 1.5 / 100)
                    target = entry * (1 + 1.75 / 100)
                self.positions[sym] = {
                    'signal': {'sym': sym, 'direction': direction, 'entry': entry,
                               'stop': stop, 'target': target},
                    'qty': abs(qty), 'margin': abs(qty) * entry / config.LEVERAGE,
                    'entry_order': 'synced', 'stop_order': 'synced',
                    'mfe': 0, 'trail_active': False, 'target_hit': False,
                }
                log.info(f'SYNCED existing position: {direction} {abs(qty)} {sym} @ {entry:.2f}')
        except Exception as e:
            log.error(f'Position sync error: {e}')

    def run(self):
        """Main trading loop."""
        mode = 'PAPER' if self.paper_mode else 'LIVE'
        log.info('=' * 60)
        log.info(f'CAM BOT v5 | {mode} | Capital: Rs {self.capital:,}')
        log.info(f'Strategy 1: GAP FILL (100% WR, gap>=1%, runner=0.10%) at 9:15 AM')
        log.info(f'Strategy 2: MA DOUBLE CONVERGENCE (65% WR) at 9:45 AM')
        log.info(f'CAM/PIVOT disabled — no proven edge without lookahead')
        log.info('=' * 60)

        # Step 0: Check for existing positions (crash recovery)
        self._sync_existing_positions()
        if self.positions:
            log.info(f'Found {len(self.positions)} existing positions — skipping signal scans, going to monitor')
            self._monitor_until_close()
            return

        # Step 1: Load historical data
        self.load_historical()

        # Step 2: GAP FILL scan at 9:15 AM (enter immediately at open, not 9:20)
        # The gap fills in the first 1-5 minutes — waiting till 9:20 misses the move
        gap_signals = []
        now = datetime.now()
        gap_scan_time = now.replace(hour=9, minute=15, second=30, microsecond=0)
        gap_deadline = now.replace(hour=9, minute=18, second=0, microsecond=0)
        if now < gap_scan_time:
            wait = (gap_scan_time - now).total_seconds()
            log.info(f'Waiting {wait:.0f}s until 9:15:30 AM (gap scan)...')
            time.sleep(wait)

        if datetime.now() <= gap_deadline:
            gap_signals = self.scan_gap_signals()
            for s in gap_signals:
                # Check if gap already filled before entering
                inst = config.INSTRUMENTS.get(s['sym'])
                ltp_data = api.get_ltp([inst])
                ltp = None
                for key, val in ltp_data.items():
                    if 'last_price' in val:
                        ltp = val['last_price']
                        break
                if ltp:
                    if s['direction'] == 'SHORT' and ltp <= s['target']:
                        log.info(f'SKIP {s["sym"]} — gap already filled (ltp={ltp:.2f} <= target={s["target"]})')
                        continue
                    if s['direction'] == 'LONG' and ltp >= s['target']:
                        log.info(f'SKIP {s["sym"]} — gap already filled (ltp={ltp:.2f} >= target={s["target"]})')
                        continue
                    # Check if price moved too far past entry (slippage guard)
                    slippage = abs(ltp - s['entry']) / s['entry'] * 100
                    if slippage > 0.5:
                        log.info(f'SKIP {s["sym"]} — too much slippage ({slippage:.2f}% from entry {s["entry"]})')
                        continue
                self.enter_trade(s)
        else:
            log.info(f'SKIPPED gap scan — past 9:18 AM, signals are stale')

        # Step 3: MA CONVERGENCE scan at 9:45 AM (skip if past 10:00)
        ma_signals = []
        now = datetime.now()
        ma_scan_time = now.replace(hour=9, minute=45, second=0, microsecond=0)
        ma_deadline = now.replace(hour=10, minute=0, second=0, microsecond=0)
        while datetime.now() < ma_scan_time:
            try:
                if self.positions:
                    self.monitor_positions()
            except Exception as e:
                log.error(f'Monitor error (pre-MA): {e}')
            time.sleep(30)

        if datetime.now() <= ma_deadline:
            ma_signals = self.scan_ma_convergence(6, 15)
            for s in ma_signals:
                self.enter_trade(s)
        else:
            log.info(f'SKIPPED MA scan — past 10:00 AM, signals are stale')

        # CAM/PIVOT disabled — no proven edge without lookahead trend
        cam_signals = []

        total_signals = len(gap_signals) + len(ma_signals)
        if total_signals == 0 and not self.positions:
            log.info('No signals from any strategy. Done for today.')
            self.write_journal()
            return

        # Step 6: Monitor until close
        self._monitor_until_close()

    def _monitor_until_close(self):
        """Monitor positions until 3 PM, then close all. Never crashes."""
        close_time = datetime.now().replace(hour=15, minute=0, second=0)
        log.info(f'Monitoring {len(self.positions)} positions until 3:00 PM...')
        while datetime.now() < close_time:
            try:
                if self.positions:
                    self.monitor_positions()
                    log.info(f'  [{datetime.now().strftime("%H:%M")}] {len(self.positions)} open | '
                             f'Daily PnL: Rs {self.daily_pnl:+,.0f}')
                else:
                    log.info(f'  [{datetime.now().strftime("%H:%M")}] All positions closed')
                    break
            except Exception as e:
                log.error(f'Monitor error (continuing): {e}')
            time.sleep(60)

        # Close remaining at EOD
        if self.positions:
            self.close_all()

        log.info(f'\nDAILY RESULT: Rs {self.daily_pnl:+,.0f}')
        log.info(f'Capital: Rs {self.capital:,} -> Rs {self.available:,.0f}')
        self.write_journal()
