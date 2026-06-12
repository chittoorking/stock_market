"""Live trader — orchestrates scanning, entry, monitoring, exit."""
import sys
import io
import logging
import time
import json
import requests
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

        # Fetch real available margin from Upstox (not internal tracking)
        if not self.paper_mode:
            real_margin = self._fetch_available_margin()
            if real_margin and real_margin < self.available:
                log.info(f'Upstox margin Rs {real_margin:,.0f} < internal Rs {self.available:,.0f} — using Upstox')
                self.available = float(real_margin)

        # Position sizing — use 95% to leave room for Upstox margin overhead
        alloc = self.available * config.SIZING * 0.95
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

            # Place TARGET limit order only if target is reachable (not runner mode)
            tgt_pct = abs(signal['entry'] - signal['target']) / signal['entry'] * 100
            tgt_oid = None
            if tgt_pct < 5.0:  # Fixed target — place on Upstox
                tgt_oid = api.place_order(sym, qty, sl_side, signal['target'], order_type='LIMIT')
                if tgt_oid:
                    log.info(f'TARGET order placed: {sl_side} {qty} {sym} @ {signal["target"]} -> {tgt_oid}')
                else:
                    log.warning(f'TARGET order failed for {sym} — will use polling fallback')
            else:
                log.info(f'RUNNER MODE — no target order, trail will manage exit')

            self.positions[sym] = {
                'signal': signal, 'qty': qty, 'margin': margin,
                'entry_order': entry_oid, 'stop_order': sl_oid,
                'target_order': tgt_oid,
                'mfe': 0, 'trail_active': False, 'target_hit': False,
            }
            self.available -= margin
            return True

    def monitor_positions(self):
        """Check all open positions and manage exits.
        Target and SL are on Upstox — check if either filled, cancel the other.
        Also poll LTP for trail lock adjustments.
        """
        if not self.positions:
            return

        # Check order statuses to detect target/SL fills
        try:
            token = api.get_token()
            r = requests.get(f'{config.UPSTOX_BASE}/order/retrieve-all',
                           headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                           timeout=10)
            order_statuses = {}
            if r.status_code == 200:
                for o in r.json().get('data', []):
                    order_statuses[o.get('order_id')] = o.get('status')
        except Exception:
            order_statuses = {}

        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            signal = pos['signal']

            # Check if target order filled on Upstox
            tgt_oid = pos.get('target_order')
            sl_oid = pos.get('stop_order')
            if tgt_oid and order_statuses.get(tgt_oid) == 'complete':
                log.info(f'TARGET FILLED on Upstox for {sym}')
                self.exit_trade(sym, 'target_hit', signal['target'], already_closed=True)
                continue

            # Check if SL order filled on Upstox
            if sl_oid and order_statuses.get(sl_oid) == 'complete':
                log.info(f'STOP FILLED on Upstox for {sym}')
                self.exit_trade(sym, 'stop_loss', signal['stop'], already_closed=True)
                continue

            # Poll LTP for trail lock management
            inst = config.INSTRUMENTS.get(sym)
            if not inst:
                continue
            ltp_data = api.get_ltp([inst])
            if not ltp_data:
                continue

            current_price = None
            for key, val in ltp_data.items():
                if 'last_price' in val:
                    current_price = val['last_price']
                    break
            if not current_price:
                continue

            # Check exit conditions (trail lock, runner)
            action, exit_price, new_mfe, new_trail, new_target = strategy.check_exit(
                signal, current_price,
                pos['mfe'], pos['trail_active'], pos['target_hit']
            )

            # Update state
            pos['mfe'] = new_mfe
            pos['trail_active'] = new_trail
            pos['target_hit'] = new_target

            if action and action == 'trail_stop':
                # Trail lock triggered — cancel both Upstox orders and exit
                if sl_oid and sl_oid not in ('PAPER', 'synced', None):
                    try: api.cancel_order(sl_oid)
                    except: pass
                if tgt_oid:
                    try: api.cancel_order(tgt_oid)
                    except: pass
                self.exit_trade(sym, action, exit_price)
            elif action and action not in ('target_hit', 'stop_loss'):
                # Runner or other exit — cancel both and exit
                if sl_oid and sl_oid not in ('PAPER', 'synced', None):
                    try: api.cancel_order(sl_oid)
                    except: pass
                if tgt_oid:
                    try: api.cancel_order(tgt_oid)
                    except: pass
                self.exit_trade(sym, action, exit_price)

    def exit_trade(self, sym, reason, exit_price, already_closed=False):
        """Exit a position.
        already_closed=True when target/SL filled on Upstox (position already flat).
        In that case, DON'T place another MARKET order — just clean up.
        """
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
            # Cancel remaining SL and target orders
            for oid_key in ('stop_order', 'target_order'):
                oid = pos.get(oid_key)
                if oid and oid not in ('PAPER', 'synced', None):
                    try:
                        api.cancel_order(oid)
                    except Exception as e:
                        log.warning(f'{oid_key} cancel failed for {sym}: {e}')

            if already_closed:
                # Position already closed by Upstox (target/SL fill) — no MARKET order needed
                log.info(f'EXIT {direction} {sym}: {reason} @ {exit_price:.2f} '
                         f'PnL={pnl_pct:+.3f}% Rs {net_pnl:+,.0f} (Upstox filled)')
            else:
                # Position still open — place MARKET exit order
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

    def scan_gap_ltp(self):
        """15-second LTP scan — no bar close needed.
        Get LTP for all stocks, check gap + reversal direction."""
        log.info('Scanning via LTP (15-sec approach)...')
        signals = []

        # Get LTP for ALL instruments in one batch call
        all_insts = [config.INSTRUMENTS[sym] for sym in config.INSTRUMENTS
                     if sym in self.daily_closes and sym not in self.positions]
        inst_to_sym = {v: k for k, v in config.INSTRUMENTS.items()}

        # Upstox LTP API accepts comma-separated keys
        # Split into batches of 10 to avoid URL length limits
        all_ltps = {}
        for i in range(0, len(all_insts), 10):
            batch = all_insts[i:i+10]
            ltp_data = api.get_ltp(batch)
            if ltp_data:
                all_ltps.update(ltp_data)
            time.sleep(0.1)

        log.info(f'Got LTP for {len(all_ltps)} stocks')

        for key, val in all_ltps.items():
            ltp = val.get('last_price')
            if not ltp:
                continue

            # Extract symbol from key (format: NSE_EQ:SYMBOL)
            sym = key.split(':')[-1] if ':' in key else key
            inst = val.get('instrument_token', '')
            sym = inst_to_sym.get(inst, sym)

            if sym not in self.daily_closes:
                continue

            closes = self.daily_closes[sym]
            if len(closes) < 2:
                continue
            prev_close = closes[-1]

            prev_stats = self.prev_stats.get(sym)
            prev_high = prev_stats['high'] if prev_stats else None
            prev_low = prev_stats['low'] if prev_stats else None

            # The LTP IS the current price (~15 sec after open)
            # Use it as both "open" and "close" of a virtual bar
            today_open = ltp  # Best approximation of open at this point

            gap = (today_open - prev_close) / prev_close * 100
            is_gap = abs(gap) >= 1.0
            is_outside_high = prev_high and today_open > prev_high
            is_outside_low = prev_low and today_open < prev_low

            if not is_gap and not is_outside_high and not is_outside_low:
                continue

            # Reversal check: is LTP moving AGAINST the gap?
            # At 15 sec, we check if LTP < open (for gap up) = already reversing
            # We use the fact that LTP at 9:15:15 is already slightly different from open
            # If gap up and LTP is below where it opened = sellers stepping in
            # For now, we trust the gap + outside range as sufficient signal
            # The slippage guard will catch if it doesn't reverse

            if (is_gap and gap > 0) or is_outside_high:
                direction = 'SHORT'
                entry = round(today_open, 2)
                signals.append({
                    'sym': sym, 'direction': direction, 'strategy': 'RANGE_FILL',
                    'entry': entry,
                    'stop': round(entry * (1 + 1.0/100), 2),
                    'target': round(entry * (1 - 10.0/100), 2),  # No cap — runner
                    'runner_step': 0.10,
                    'trail_trigger': 0.10,
                    'trail_lock': 0.10,
                    'level': round(prev_close, 2),
                    'gap': round(gap, 2),
                })
                log.info(f'RANGE SIGNAL: SHORT {sym} gap={gap:+.2f}% ltp={ltp:.2f}')

            elif (is_gap and gap < 0) or is_outside_low:
                direction = 'LONG'
                entry = round(today_open, 2)
                signals.append({
                    'sym': sym, 'direction': direction, 'strategy': 'RANGE_FILL',
                    'entry': entry,
                    'stop': round(entry * (1 - 1.0/100), 2),
                    'target': round(entry * (1 + 10.0/100), 2),  # No cap — runner
                    'runner_step': 0.10,
                    'trail_trigger': 0.10,
                    'trail_lock': 0.10,
                    'level': round(prev_close, 2),
                    'gap': round(gap, 2),
                })
                log.info(f'RANGE SIGNAL: LONG {sym} gap={gap:+.2f}% ltp={ltp:.2f}')

        log.info(f'Found {len(signals)} signals')
        return signals

    def scan_gap_signals(self):
        """Fallback: 1-min bar scan (used if LTP scan fails)."""
        log.info('Scanning via 1-min bars (fallback)...')
        signals = []

        for sym in config.INSTRUMENTS:
            if sym in self.positions or sym not in self.daily_closes:
                continue
            closes = self.daily_closes[sym]
            if len(closes) < 2: continue
            prev_close = closes[-1]
            prev_stats = self.prev_stats.get(sym)
            prev_high = prev_stats['high'] if prev_stats else None
            prev_low = prev_stats['low'] if prev_stats else None

            inst = config.INSTRUMENTS[sym]
            candles = api.get_intraday_candles(inst, '1minute')
            if not candles: continue
            sorted_candles = sorted(candles, key=lambda x: x[0])
            if not sorted_candles: continue
            bars_1min = [{'open': float(c[1]), 'high': float(c[2]), 'low': float(c[3]),
                          'close': float(c[4]), 'volume': int(c[5])} for c in sorted_candles]

            signal = strategy.check_gap_signal(sym, bars_1min, prev_close, prev_high, prev_low)
            if signal:
                signals.append(signal)
                log.info(f'RANGE SIGNAL: {signal["direction"]} {sym} gap={signal["gap"]}%')
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
        log.info(f'BASKET GAP FILL | Enter ALL 1%+ gaps | 4-12 stocks')
        log.info(f'Stop 0.30% Trail 0.05% | 85% green days')
        log.info('=' * 60)

        # Step 0: Check for existing positions (crash recovery)
        self._sync_existing_positions()
        if self.positions:
            log.info(f'Found {len(self.positions)} existing positions — skipping signal scans, going to monitor')
            self._monitor_until_close()
            return

        # Step 1: Load historical data
        self.load_historical()

        # Step 2: BASKET GAP FILL — enter ALL 1%+ gap stocks
        now = datetime.now()
        gap_scan_time = now.replace(hour=9, minute=15, second=15, microsecond=0)
        gap_deadline = now.replace(hour=9, minute=18, second=0, microsecond=0)
        if now < gap_scan_time:
            wait = (gap_scan_time - now).total_seconds()
            log.info(f'Waiting {wait:.0f}s until 9:15:15 AM...')
            time.sleep(wait)

        if datetime.now() <= gap_deadline:
            log.info('Scanning ALL stocks for 1%+ gaps...')

            # Batch LTP for all stocks
            all_insts = [config.INSTRUMENTS[sym] for sym in config.INSTRUMENTS
                         if sym in self.daily_closes]
            inst_to_sym = {v: k for k, v in config.INSTRUMENTS.items()}
            all_ltps = {}
            for i in range(0, len(all_insts), 10):
                batch = all_insts[i:i+10]
                ltp_data = api.get_ltp(batch)
                if ltp_data:
                    for key, val in ltp_data.items():
                        ltp = val.get('last_price')
                        inst = val.get('instrument_token', '')
                        sym = inst_to_sym.get(inst, key.split(':')[-1] if ':' in key else key)
                        if ltp and sym in self.daily_closes:
                            all_ltps[sym] = ltp
                time.sleep(0.1)
            log.info(f'Got LTP for {len(all_ltps)} stocks')

            # Find ALL 1%+ gap stocks
            gap_stocks = []
            for sym, ltp in all_ltps.items():
                closes = self.daily_closes.get(sym, [])
                if not closes:
                    continue
                prev_close = closes[-1]
                if prev_close <= 0:
                    continue
                gap = (ltp - prev_close) / prev_close * 100
                if abs(gap) < 1.0:
                    continue
                direction = 'SHORT' if gap > 0 else 'LONG'
                entry = round(ltp, 2)
                stop = round(entry * (1 - 0.30/100), 2) if direction == 'LONG' else round(entry * (1 + 0.30/100), 2)
                gap_stocks.append({
                    'sym': sym, 'direction': direction, 'strategy': 'BASKET_GAP',
                    'entry': entry, 'stop': stop,
                    'target': round(entry * (1 + 10.0/100), 2) if direction == 'LONG' else round(entry * (1 - 10.0/100), 2),
                    'runner_step': 0.05, 'trail_trigger': 0.05, 'trail_lock': 0.05,
                    'gap': round(gap, 2), 'level': round(prev_close, 2),
                })
                log.info(f'GAP: {direction} {sym} gap={gap:+.2f}% ltp={ltp:.2f}')

            n_gaps = len(gap_stocks)
            longs = sum(1 for s in gap_stocks if s['direction'] == 'LONG')
            shorts = sum(1 for s in gap_stocks if s['direction'] == 'SHORT')
            log.info(f'Found {n_gaps} gap stocks: {longs} LONG, {shorts} SHORT')

            # BASKET RULES: 4-12 stocks only
            if n_gaps < 4:
                log.info(f'SKIP — only {n_gaps} gaps, need 4+ for basket')
            elif n_gaps > 12:
                log.info(f'SKIP — {n_gaps} gaps, market-wide event')
            else:
                # Fetch real margin
                if not self.paper_mode:
                    real_margin = self._fetch_available_margin()
                    if real_margin:
                        self.capital = real_margin
                        self.available = float(real_margin)

                # Enter ALL gap stocks with equal capital
                alloc_per = self.available * 0.95 / n_gaps
                entered = 0
                for s in gap_stocks:
                    qty = max(1, int(alloc_per * config.LEVERAGE / s['entry']))
                    margin = qty * s['entry'] / config.LEVERAGE
                    if self.paper_mode:
                        log.info(f'[PAPER] {s["direction"]} {qty} {s["sym"]} @ {s["entry"]}')
                        self.positions[s['sym']] = {
                            'signal': s, 'qty': qty, 'margin': margin,
                            'entry_order': 'PAPER', 'stop_order': 'PAPER', 'target_order': None,
                            'mfe': 0, 'trail_active': False, 'target_hit': False,
                        }
                        entered += 1
                    else:
                        side = 'SELL' if s['direction'] == 'SHORT' else 'BUY'
                        entry_oid = api.place_order(s['sym'], qty, side, 0, order_type='MARKET')
                        if not entry_oid:
                            log.error(f'Entry FAILED {s["sym"]}')
                            continue
                        sl_side = 'BUY' if s['direction'] == 'SHORT' else 'SELL'
                        sl_oid = api.place_order(s['sym'], qty, sl_side, 0,
                                                 order_type='SL-M', trigger_price=s['stop'])
                        if not sl_oid:
                            log.error(f'SL FAILED {s["sym"]} — exiting')
                            api.place_order(s['sym'], qty, 'BUY' if side == 'SELL' else 'SELL', 0, order_type='MARKET')
                            continue
                        log.info(f'BASKET: {s["direction"]} {qty} {s["sym"]} gap={s["gap"]}%')
                        self.positions[s['sym']] = {
                            'signal': s, 'qty': qty, 'margin': margin,
                            'entry_order': entry_oid, 'stop_order': sl_oid, 'target_order': None,
                            'mfe': 0, 'trail_active': False, 'target_hit': False,
                        }
                        entered += 1
                        self.available -= margin
                    time.sleep(0.3)
                log.info(f'BASKET: entered {entered}/{n_gaps} stocks')
        else:
            log.info(f'SKIPPED — past 9:18 AM')

        # Step 3: Monitor all basket positions until close
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
