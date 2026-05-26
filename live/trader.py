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

            # Daily closes for trend
            self.daily_closes[sym] = [by_date[d][-1]['close'] for d in dates]

            # Daily volumes for MA convergence
            self.daily_volumes[sym] = [sum(b['volume'] for b in by_date[d]) for d in dates]

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

            # Place SL-M stop loss order (trigger price only, market exit)
            sl_side = 'BUY' if direction == 'SHORT' else 'SELL'
            sl_oid = api.place_order(sym, qty, sl_side, 0,
                                     order_type='SL-M', trigger_price=signal['stop'])

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
            # Cancel stop loss order first
            if pos.get('stop_order') and pos['stop_order'] != 'PAPER':
                api.cancel_order(pos['stop_order'])
            # Place MARKET exit order (price=0 for market orders)
            side = 'BUY' if direction == 'SHORT' else 'SELL'
            api.place_order(sym, qty, side, 0, order_type='MARKET')
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

    def run(self):
        """Main trading loop."""
        mode = 'PAPER' if self.paper_mode else 'LIVE'
        log.info('=' * 60)
        log.info(f'CAM BOT v5 | {mode} | Capital: Rs {self.capital:,}')
        log.info(f'Strategy 1: GAP FILL (99.8% WR) at 9:20 AM')
        log.info(f'Strategy 2: MA CONVERGENCE (91% WR) at 9:45 AM')
        log.info(f'Strategy 3: CAM R3/S3 + Pivot (89-96% WR) at 10:15 AM')
        log.info('=' * 60)

        # Step 1: Load historical data
        self.load_historical()

        # Step 2: GAP FILL scan at 9:20 AM
        now = datetime.now()
        gap_scan_time = now.replace(hour=9, minute=20, second=0, microsecond=0)
        if now < gap_scan_time:
            wait = (gap_scan_time - now).total_seconds()
            log.info(f'Waiting {wait:.0f}s until 9:20 AM (gap scan)...')
            time.sleep(wait)

        gap_signals = self.scan_gap_signals()
        for s in gap_signals:
            self.enter_trade(s)

        # Step 3: MA CONVERGENCE scan at 9:45 AM (bar 6-15)
        ma_scan_time = datetime.now().replace(hour=9, minute=45, second=0, microsecond=0)
        if datetime.now() < ma_scan_time:
            # Monitor gap trades while waiting
            while datetime.now() < ma_scan_time and self.positions:
                self.monitor_positions()
                time.sleep(30)
            if datetime.now() < ma_scan_time:
                time.sleep((ma_scan_time - datetime.now()).total_seconds())

        ma_signals = self.scan_ma_convergence(6, 15)
        for s in ma_signals:
            self.enter_trade(s)

        # Step 4: Monitor until 10:15 AM
        cam_scan_time = datetime.now().replace(hour=10, minute=15, second=0, microsecond=0)
        while datetime.now() < cam_scan_time and self.positions:
            self.monitor_positions()
            time.sleep(30)

        # Step 5: CAM + Pivot scan at 10:15 AM
        log.info('--- CAM + PIVOT SCAN at 10:15 AM ---')
        cam_signals = self.scan_signals()
        for s in cam_signals[:config.MAX_TRADES]:
            self.enter_trade(s)

        total_signals = len(gap_signals) + len(ma_signals) + len(cam_signals)
        if total_signals == 0:
            log.info('No signals from any strategy. Done for today.')
            self.write_journal()
            return

        # Step 6: Monitor until 3:00 PM
        close_time = datetime.now().replace(hour=15, minute=0, second=0)
        while datetime.now() < close_time and self.positions:
            self.monitor_positions()
            time.sleep(60)

        # Step 7: Close remaining at EOD
        self.close_all()

        # Step 7: Journal
        log.info(f'\nDAILY RESULT: Rs {self.daily_pnl:+,.0f}')
        log.info(f'Capital: Rs {self.capital:,} -> Rs {self.available:,.0f}')
        self.write_journal()
