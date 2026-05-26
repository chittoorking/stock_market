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
        self.positions = {}  # sym -> {signal, entry_order_id, stop_order_id, mfe, trail_active, target_hit, qty}
        self.daily_pnl = 0
        self.today = datetime.now().strftime('%Y-%m-%d')

        # Pre-load historical data for trends
        self.daily_closes = {}  # sym -> [list of daily closes]
        self.prev_day_bars = {}  # sym -> [bars]
        self.prev_stats = {}  # sym -> {high, low, close, range, ...}

    def load_historical(self):
        """Load last 10 days of data per stock for trend calculation."""
        log.info('Loading historical data for all stocks...')
        for sym, inst in config.INSTRUMENTS.items():
            bars = api.load_previous_days(sym, num_days=10)
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

    def run(self):
        """Main trading loop."""
        mode = 'PAPER' if self.paper_mode else 'LIVE'
        log.info('=' * 60)
        log.info(f'CAM BOT v3 LIVE | {mode} | Capital: Rs {self.capital:,}')
        log.info(f'Target: {config.TARGET}% | Stop: {config.STOP}%')
        log.info(f'Trail: {config.TRAIL_ACTIVATE}% -> {config.TRAIL_LOCK}% | Runner: {config.RUNNER_STEP}%')
        log.info('=' * 60)

        # Step 1: Load historical data
        self.load_historical()

        # Step 2: Wait for 10:15 AM
        now = datetime.now()
        scan_time = now.replace(hour=10, minute=15, second=0, microsecond=0)
        if now < scan_time:
            wait = (scan_time - now).total_seconds()
            log.info(f'Waiting {wait:.0f}s until 10:15 AM...')
            time.sleep(wait)

        # Step 3: Scan for signals
        signals = self.scan_signals()
        if not signals:
            log.info('No signals. Done for today.')
            self.write_journal()
            return

        # Step 4: Enter trades
        for s in signals[:config.MAX_TRADES]:
            self.enter_trade(s)

        # Step 5: Monitor until 3:00 PM
        close_time = datetime.now().replace(hour=15, minute=0, second=0)
        while datetime.now() < close_time and self.positions:
            self.monitor_positions()
            time.sleep(60)  # Check every minute

        # Step 6: Close remaining at EOD
        self.close_all()

        # Step 7: Journal
        log.info(f'\nDAILY RESULT: Rs {self.daily_pnl:+,.0f}')
        log.info(f'Capital: Rs {self.capital:,} -> Rs {self.available:,.0f}')
        self.write_journal()
