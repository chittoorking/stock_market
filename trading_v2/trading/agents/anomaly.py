"""Anomaly Agent — SHORT overextended small-cap gaps.

Thesis: small-cap stocks that gap up without institutional support revert intraday.

Entry 1 — 9:20 scan (fake gap detection):
  Filters: score >= 5, residual 2-13, gain > 4%, price >= 50,
           mcap < 1000 Cr, 1M < 35%, circuit > entry + 2.5%
  Skip: openPrice = 0 (stock not yet opened)
  Sort: highest score, then highest residual

Entry 2 — 10:12 / 11:12 / 12:12 scans (confirmed fade):
  Filters: gain 5-10%, pullback > 1%, below VWAP > 0.5%,
           delivery < 40%, OCF < 100, bounce < 2.5%,
           fr200 < 20%, 6M < 50%, circuit > entry + 2.5%
  Sort: highest pullback, then highest below_vwap

Entry 3 — 13:12 / 14:12 (PAPER mode, log only)

Execution: LIMIT DAY at LTP * 0.998 (0.2% thesis confirmation), 5-min fill window, cancel unfilled.
Exit: SL 2%, trail activate 1% / trail 0.5%, EOD 3:10 PM.

Anomaly score (0-8, all sector-relative):
  +1 residual > 3%    +1 residual > 5%    +1 volatility > 1.3x
  +1 delivery < -10pp +1 volume < 0.5x    +1 weekly > +5pp
  +1 gap rank >= 90th +1 unprofitable AND negative OCF

Backtest: 25W 0L = 100% WR across 7 days (Sep 30 - Oct 9, 2026).
"""
import time
import json
from datetime import datetime
from collections import defaultdict

from trading.base_agent import BaseAgent
from trading.types import TradeRequest, Side
from trading.logger import audit, get_logger
from trading.services import price_tracker

log = get_logger('anomaly')

# Heartbeat scans - when to scan for anomalies
ENTRY_TIMES = [
    (9, 20),   # after ORB, first scan
    (10, 12),  # delivery update - second scan
    (11, 12),  # late pullback - third scan
    (12, 12),  # fourth scan
    (13, 12),  # afternoon - paper mode
    (14, 12),  # afternoon - paper mode
]

MAX_TRADES = 10
SL_PCT = 2.0
TRAIL_ACTIVATE = 1.5
TRAIL_PCT = 0.5
MIN_ANOMALY_SCORE = 5
MIN_PRICE = 50


class AnomalyAgent(BaseAgent):
    name = 'anomaly'

    def __init__(self, broker, positions, fund_manager, monitor):
        super().__init__(broker, positions, fund_manager, monitor)
        self._entered = set()  # tracks all tickers entered today (for re-entry logging)
        self._sl_hit = set()   # tickers that hit SL today — no re-entry
        self._ml_session = None
        self._ml_action = None

    def run(self):
        """Main loop: wait for each heartbeat, scan, trade."""
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
        if now.hour >= 14 or now.weekday() >= 5:
            self._log.info('Past 2 PM or weekend, skipping')
            return

        self._entered.clear()
        self._sl_hit.clear()
        self._fm.reset_daily()
        price_tracker.reset()
        price_tracker.start()
        self._log.info('=== ANOMALY AGENT START ===')

        for entry_h, entry_m in ENTRY_TIMES:
            # Wait for entry time
            target = now.replace(hour=entry_h, minute=entry_m, second=0)
            wait = (target - datetime.now()).total_seconds()
            if wait > 0:
                self._log.info(f'Waiting {wait:.0f}s for {entry_h}:{entry_m:02d}')
                time.sleep(wait)
            elif wait < -300:
                # More than 5 min past - skip this heartbeat
                self._log.info(f'SKIP {entry_h}:{entry_m:02d} (past by {-wait:.0f}s)')
                continue

            self._log.info(f'=== HEARTBEAT {entry_h}:{entry_m:02d} ===')
            try:
                self._scan_and_trade()
            except Exception as e:
                self._log.error(f'Scan error: {e}')
                import traceback; traceback.print_exc()

        self._log.info('=== ANOMALY AGENT DONE ===')

    def _scan_and_trade(self):
        """Fetch all stocks. 9:20 uses anomaly score. 10:12 uses pullback rule."""
        now = datetime.now()
        is_first_scan = now.hour == 9  # 9:20 scan
        is_second_scan = now.hour in (10, 11, 12, 13, 14)  # 10:12 and 11:12 scan
        # Fetch Market Lens data
        stocks = self._fetch_market_lens()
        if not stocks:
            self._log.warning('No Market Lens data')
            return

        self._log.info(f'Fetched {len(stocks)} stocks from Market Lens')

        # Compute sector stats
        sectors = defaultdict(list)
        for s in stocks:
            if s['sector']:
                sectors[s['sector']].append(s)

        sec_stats = {}
        for sec, ss in sectors.items():
            if len(ss) < 10:
                continue
            betas = [s['beta'] for s in ss if s['beta'] > 0]
            dlvs = [s['dlv'] for s in ss if s['dlv'] > 0]
            vols = [s['vol'] for s in ss if s['vol'] > 0]
            dvs = [s['dv'] for s in ss if s['dv'] > 0]
            sec_stats[sec] = {
                'gap': sum(s['gap'] for s in ss) / len(ss),
                'beta': sum(betas) / len(betas) if betas else 1,
                'dlv': sum(dlvs) / len(dlvs) if dlvs else 50,
                'vol': sum(vols) / len(vols) if vols else 1,
                'dv': sum(dvs) / len(dvs) if dvs else 200,
                'wk': sum(s['wk'] for s in ss) / len(ss),
            }

        mkt_gap = sum(s['gap'] for s in stocks) / len(stocks) if stocks else 0

        # Compute anomaly score for each stock
        for s in stocks:
            sec = s['sector']
            beta = s['beta'] if s['beta'] > 0 else 1
            st = sec_stats.get(sec)

            if st:
                avg_beta = st['beta'] if st['beta'] > 0 else 1
                s['residual'] = s['gap'] - st['gap'] * (beta / avg_beta)
                s['dlv_vs'] = s['dlv'] - st['dlv']
                s['vol_vs'] = s['vol'] / st['vol'] if st['vol'] > 0 else 1
                s['wk_vs'] = s['wk'] - st['wk']
                s['dv_vs'] = s['dv'] / st['dv'] if st['dv'] > 0 else 1
                sg = sorted([x['gap'] for x in sectors[sec]])
                s['rank'] = sum(1 for g in sg if g <= s['gap']) * 100 // len(sg)
            else:
                s['residual'] = s['gap'] - mkt_gap * beta
                s['dlv_vs'] = 0
                s['vol_vs'] = 1
                s['wk_vs'] = 0
                s['dv_vs'] = 1
                s['rank'] = 50

            # SHORT anomaly score
            score = 0
            if s['residual'] > 3: score += 1
            if s['residual'] > 5: score += 1
            if s['dv_vs'] > 1.3: score += 1
            if s['dlv_vs'] < -10: score += 1
            if s['vol_vs'] < 0.5: score += 1
            if s['wk_vs'] > 5: score += 1
            if s['rank'] >= 90: score += 1
            if s['nm'] < 0 and s['ocf'] <= 0: score += 1
            s['anomaly_score'] = score

        if is_first_scan:
            # 9:20: gain>4% + residual<=13 + anomaly_score>=5
            candidates = [s for s in stocks
                          if s['anomaly_score'] >= MIN_ANOMALY_SCORE
                          and s['residual'] > 2
                          and s['residual'] <= 13
                          and not self._positions.find_by_symbol(s['ticker'])
                          and s['ticker'] not in self._sl_hit
                          and s['ltp'] >= MIN_PRICE]
            # Filter: gain must be > 4% from prev close
            candidates = [s for s in candidates
                          if s.get('gain_from_prev', 0) > 4]
            # Filter: small caps only + not momentum continuation
            candidates = [s for s in candidates
                          if s.get('mcap', 9999) < 1000
                          and s.get('mo', 0) < 35]
            # Circuit filter: skip if upper circuit < entry + 2.5% (SL can't fill before circuit)
            candidates = [s for s in candidates
                          if s.get('upper_circuit', 9999) > s['ltp'] * 1.025]
            # Skip multi-day momentum: 10%+ gain in prior 4 days = circuit continuation
            candidates = [s for s in candidates
                          if (s.get('wk', 0) - s.get('gain_from_prev', 0)) < 10]
            candidates.sort(key=lambda x: (-x['anomaly_score'], -x['residual']))
        elif is_second_scan:
            # 10:12: gain 5-10% + pullback>1% + below VWAP>0.5% + delivery<40% + OCF<100
            candidates = []
            for s in stocks:
                if self._positions.find_by_symbol(s['ticker']): continue
                if s['ticker'] in self._sl_hit: continue
                if s['ltp'] < MIN_PRICE: continue
                # Only short weak fundamentals (OCF < 100)
                if s.get("ocf", 0) >= 100:
                    audit("anomaly", "SKIP_HIGH_OCF", s["ticker"],
                          gain=round(s.get("gain_from_prev", 0), 1), ocf=round(s.get("ocf", 0)))
                    continue
                gain = s.get('gain_from_prev', 0)
                if gain < 5 or gain > 10: continue
                # Pullback from day high
                dh = s.get('day_high', 0)
                if dh <= 0: continue
                pullback = (dh - s['ltp']) / dh * 100
                if pullback < 1: continue
                # Below VWAP
                vwap = s.get('vwap', 0)
                if vwap <= 0: continue
                below_vwap = (vwap - s['ltp']) / vwap * 100
                if below_vwap < 0.5: continue
                # Delivery < 40%
                dlv = s.get('dlv', 0)
                if dlv >= 40:
                    # Log but skip
                    audit('anomaly', 'SKIP_HIGH_DLV', s['ticker'],
                          gain=round(gain, 1), pullback=round(pullback, 1),
                          below_vwap=round(below_vwap, 2), dlv=round(dlv, 1))
                    continue
                s['pullback'] = pullback
                s['below_vwap'] = below_vwap
                # Max bounce < 2.5% (gradual fade, not choppy)
                mb = price_tracker.get_max_bounce(s['ticker'])
                if mb >= 2.5:
                    audit('anomaly', 'SKIP_CHOPPY', s['ticker'],
                          gain=round(gain, 1), max_bounce=round(mb, 1))
                    continue
                # fr200 < 20% (not in strong uptrend)
                dma200 = s.get('dma200', 0)
                if dma200 > 0:
                    fr200 = (s['ltp'] - dma200) / dma200 * 100
                    if fr200 >= 20:
                        audit('anomaly', 'SKIP_UPTREND', s['ticker'],
                              gain=round(gain, 1), fr200=round(fr200, 1))
                        continue
                # 6M return < 50% (not a stock that doubled recently)
                sixm = s.get('sixm', 0)
                if sixm >= 50:
                    audit('anomaly', 'SKIP_6M_MOMENTUM', s['ticker'],
                          gain=round(gain, 1), sixm=round(sixm, 1))
                    continue
                # Circuit filter: skip if upper circuit < entry + 2.5%
                uc = s.get('upper_circuit', 0)
                if uc > 0 and uc < s['ltp'] * 1.025:
                    audit('anomaly', 'SKIP_CIRCUIT', s['ticker'],
                          gain=round(gain, 1), ltp=s['ltp'], upper_circuit=uc)
                    continue
                # Skip multi-day momentum: 10%+ gain in prior 4 days = circuit continuation
                prior_4d = s.get('wk', 0) - gain
                if prior_4d > 10:
                    audit('anomaly', 'SKIP_MOMENTUM', s['ticker'],
                          gain=round(gain, 1), wk=round(s.get('wk', 0), 1), prior_4d=round(prior_4d, 1))
                    continue
                candidates.append(s)
            candidates.sort(key=lambda x: (-x['pullback'], -x['below_vwap']))
        else:
            candidates = []

        # How many slots available?
        current_positions = len([p for p in self._positions.active()
                                if p.strategy == self.name])
        slots = MAX_TRADES - current_positions
        if slots <= 0:
            self._log.info(f'No slots available ({current_positions} active)')
            return

        to_trade = candidates[:slots]
        self._log.info(f'Anomalies found: {len(candidates)} (score >= {MIN_ANOMALY_SCORE}), '
                       f'trading top {len(to_trade)}, {slots} slots available')

        # Log all candidates
        for s in candidates[:10]:
            extra = {}
            if is_second_scan:
                extra = {'pullback': round(s.get('pullback', 0), 1),
                          'below_vwap': round(s.get('below_vwap', 0), 2),
                          'gain': round(s.get('gain_from_prev', 0), 1)}
            audit('anomaly', 'CANDIDATE', s['ticker'],
                  score=s.get('anomaly_score', 0), residual=round(s.get('residual', 0), 1),
                  gap=round(s['gap'], 2), sector=s['sector'],
                  dlv=round(s.get('dlv', 0), 1), ltp=s['ltp'],
                  scan=f'{now.hour}:{now.minute:02d}', **extra)

        # Afternoon scans (1:12, 2:12) = paper mode only
        if now.hour >= 13:
            for s in to_trade:
                audit('anomaly', 'PAPER_AFTERNOON', s['ticker'],
                      score=s.get('anomaly_score', 0), residual=round(s.get('residual', 0), 1),
                      gap=round(s['gap'], 2), sector=s['sector'],
                      dlv=round(s.get('dlv', 0), 1), ltp=s['ltp'],
                      scan=f'{now.hour}:{now.minute:02d}',
                      pullback=round(s.get('pullback', 0), 1),
                      below_vwap=round(s.get('below_vwap', 0), 2),
                      gain=round(s.get('gain_from_prev', 0), 1))
                self._log.info(f'PAPER AFTERNOON: {s["ticker"]} gain={s.get("gain_from_prev",0):+.1f}%')
            return

        # Place all orders at once, then wait and check fills
        self._place_and_fill(to_trade)

        # Check for SL hits — block re-entry for those tickers
        for sym in list(self._entered):
            if not self._positions.find_by_symbol(sym):
                # Position closed. Check if it was SL by reading audit
                try:
                    from pathlib import Path
                    import json as _json
                    today = datetime.now().strftime('%Y%m%d')
                    audit_file = Path(__file__).parent.parent.parent.parent / 'live' / 'logs' / f'audit_{today}.jsonl'
                    if audit_file.exists():
                        for line in open(audit_file):
                            r = _json.loads(line)
                            if (r.get('symbol') == sym and
                                r.get('action') == 'EXIT' and
                                r.get('reason') == 'SL_HIT' and
                                r.get('component') == 'monitor'):
                                self._sl_hit.add(sym)
                                self._log.info(f'{sym}: SL hit — blocked from re-entry')
                                break
                except Exception:
                    pass

    def _place_and_fill(self, candidates):
        """Place all SHORT orders at once, wait up to 5 min, check fills."""
        import time

        # Phase 1: Place all orders
        pending = []
        for s in candidates:
            sym = s['ticker']
            ltp = s['ltp']

            if not self._broker.has_symbol(sym):
                self._log.warning(f'{sym}: not in broker scrips')
                continue

            live_ltp = self._broker.ltp_safe(sym)
            if live_ltp <= 0:
                live_ltp = ltp

            available = self._fm.available
            current_active = len([p for p in self._positions.active()
                                    if p.strategy == self.name])
            per_trade = available / max(1, MAX_TRADES - current_active)
            qty = int(per_trade * 5 / live_ltp)
            if qty < 1:
                self._log.info(f'{sym}: qty=0, insufficient funds')
                continue

            margin = qty * live_ltp / 5
            ok, amount, trade_id = self._fm.request(self.name, sym, margin)
            if not ok:
                self._log.info(f'{sym}: FM rejected')
                continue

            try:
                entry_price = round(live_ltp * 0.998, 2)  # 0.2% below = thesis confirmation
                order_id = self._broker.sell(sym, qty, price=entry_price,
                                             order_type='LIMIT', product='INTRADAY')
                pending.append({
                    'sym': sym, 'qty': qty, 'entry_price': entry_price,
                    'order_id': order_id, 'trade_id': trade_id,
                    'margin': margin, 'signal': s,
                })
                self._log.info(f'ORDER PLACED: SHORT {sym} qty={qty} @ {entry_price:.1f}')
            except Exception as e:
                self._log.error(f'{sym}: order failed: {e}')
                self._fm.release(trade_id, pnl=0)

        if not pending:
            self._log.info('No orders to place')
            return

        self._log.info(f'Placed {len(pending)} orders, waiting up to 5 min...')

        # Phase 2: Check every 30s for up to 5 min
        for wait_round in range(10):
            time.sleep(30)
            all_done = True
            for p in pending:
                if p.get('done'):
                    continue
                filled, fill_price = self._broker.order_filled(p['order_id'])
                if filled:
                    p['done'] = True
                    p['filled'] = True
                    p['fill_price'] = fill_price
                    self._log.info(f'{p["sym"]}: filled after {(wait_round+1)*30}s')
                else:
                    all_done = False
            if all_done:
                break

        # Phase 3: Process results
        for p in pending:
            sym = p['sym']
            s = p['signal']

            if not p.get('filled'):
                try:
                    self._broker.cancel(p['order_id'])
                    self._log.info(f'{sym}: order cancelled (no fill)')
                except Exception as e:
                    self._log.warning(f'{sym}: cancel failed: {e}')

                # Second chance: wait until 30 min from scan, retry if stock flat/lower
                scan_ltp = p['signal']['ltp']  # LTP at scan time
                p['_second_chance'] = True
                p['_scan_ltp'] = scan_ltp
                audit('anomaly', 'NO_FILL', sym, order_id=p['order_id'])
                continue

            # Cancel any unfilled remainder
            try:
                self._broker.cancel(p['order_id'])
            except Exception:
                pass

            # Get actual filled qty
            actual_qty = p['qty']
            actual_price = p['fill_price'] if p['fill_price'] > 0 else p['entry_price']
            try:
                book = self._broker._api.get_order_book() or []
                for o in book:
                    if o.get('id') == p['order_id'] or o.get('order_id') == p['order_id']:
                        tq = int(o.get('traded_qty', 0) or 0)
                        if tq > 0:
                            actual_qty = tq
                        tp = o.get('traded_price', 0)
                        if tp and float(tp) > 0:
                            actual_price = float(tp)
                        break
            except Exception:
                pass

            if actual_qty <= 0:
                self._log.info(f'{sym}: filled qty=0')
                self._fm.release(p['trade_id'], pnl=0)
                continue

            # Handle partial fill margin
            trade_id = p['trade_id']
            margin = p['margin']
            actual_margin = actual_qty * actual_price / 5
            if actual_margin < margin * 0.9:
                self._fm.release(trade_id, pnl=0)
                ok2, _, trade_id = self._fm.request(self.name, sym, actual_margin)
                if not ok2:
                    self._log.error(f'{sym}: FM re-request failed')
                    try:
                        self._broker.buy(sym, actual_qty, order_type='MARKET', product='INTRADAY')
                        self._log.info(f'{sym}: closed orphaned partial fill {actual_qty} qty')
                    except Exception:
                        self._log.error(f'{sym}: ORPHANED {actual_qty} qty SHORT - close manually!')
                    continue
                self._log.info(f'{sym}: partial fill {actual_qty}/{p["qty"]}')

            sl_price = round(actual_price * (1 + SL_PCT / 100), 2)

            self._positions.open(
                symbol=sym, side=Side.SELL, qty=actual_qty,
                entry_price=actual_price, order_id=p['order_id'],
                strategy=self.name, sl=sl_price,
                trail_activate_pct=TRAIL_ACTIVATE, trail_pct=TRAIL_PCT,
                trade_id=trade_id,
            )
            is_reentry = sym in self._entered
            self._entered.add(sym)
            audit('anomaly', 'RE_ENTRY' if is_reentry else 'ENTRY', sym,
                  side='SHORT', qty=actual_qty, entry=round(actual_price, 2),
                  sl=round(sl_price, 2), margin=round(margin, 0),
                  score=s['anomaly_score'], residual=round(s['residual'], 1),
                  sector=s['sector'], gap=round(s['gap'], 2),
                  requested_qty=p['qty'], order_id=p['order_id'])

            self._log.info(f'SHORT {sym} filled={actual_qty}/{p["qty"]} @ {actual_price:.1f} '
                          f'SL={sl_price:.1f} score={s["anomaly_score"]} '
                          f'residual={s["residual"]:+.1f}')

        # Phase 4: Second chance for unfilled orders
        second_chance = [p for p in pending if p.get('_second_chance') and not p.get('filled')]
        if second_chance:
            # Wait remaining time until 30 min from scan
            # We already waited ~5 min in Phase 2, wait 25 more
            self._log.info(f'{len(second_chance)} unfilled — waiting for second chance at 30 min')
            time.sleep(25 * 60)  # 25 min more = 30 min total from scan

            for p in second_chance:
                sym = p['sym']
                scan_ltp = p['_scan_ltp']

                # Check current LTP
                current_ltp = self._broker.ltp_safe(sym)
                if current_ltp <= 0:
                    self._log.info(f'{sym}: no LTP for second chance')
                    self._fm.release(p['trade_id'], pnl=0)
                    continue

                # Only retry if stock is flat or lower (not rising)
                if current_ltp > scan_ltp:
                    self._log.info(f'{sym}: went UP {scan_ltp:.1f} -> {current_ltp:.1f} — skip second chance')
                    self._fm.release(p['trade_id'], pnl=0)
                    audit('anomaly', 'SECOND_CHANCE_SKIP', sym,
                          scan_ltp=round(scan_ltp, 2), current_ltp=round(current_ltp, 2))
                    continue

                # Stock flat or lower — re-place order
                new_entry = round(current_ltp * 0.998, 2)
                self._log.info(f'{sym}: second chance — stock flat/lower {scan_ltp:.1f} -> {current_ltp:.1f}, new entry {new_entry:.1f}')

                try:
                    new_order_id = self._broker.sell(sym, p['qty'], price=new_entry,
                                                     order_type='LIMIT', product='INTRADAY')
                    p['order_id'] = new_order_id
                    p['entry_price'] = new_entry
                    audit('anomaly', 'SECOND_CHANCE_ORDER', sym,
                          scan_ltp=round(scan_ltp, 2), current_ltp=round(current_ltp, 2),
                          new_entry=new_entry, order_id=new_order_id)
                except Exception as e:
                    self._log.error(f'{sym}: second chance order failed: {e}')
                    self._fm.release(p['trade_id'], pnl=0)
                    continue

            # Wait 5 min for second chance fills
            for wait_round in range(10):
                time.sleep(30)
                all_done = True
                for p in second_chance:
                    if p.get('filled') or not p.get('order_id'):
                        continue
                    filled, fill_price = self._broker.order_filled(p['order_id'])
                    if filled:
                        p['filled'] = True
                        p['fill_price'] = fill_price
                        self._log.info(f'{p["sym"]}: SECOND CHANCE filled after {(wait_round+1)*30}s')
                    else:
                        all_done = False
                if all_done:
                    break

            # Process second chance results
            for p in second_chance:
                sym = p['sym']
                s = p['signal']

                if not p.get('filled'):
                    try:
                        self._broker.cancel(p['order_id'])
                    except Exception:
                        pass
                    self._log.info(f'{sym}: second chance no fill — released')
                    self._fm.release(p['trade_id'], pnl=0)
                    audit('anomaly', 'SECOND_CHANCE_NO_FILL', sym)
                    continue

                # Get actual fill
                actual_qty = p['qty']
                actual_price = p['fill_price'] if p['fill_price'] > 0 else p['entry_price']
                try:
                    book = self._broker._api.get_order_book() or []
                    for o in book:
                        if o.get('id') == p['order_id'] or o.get('order_id') == p['order_id']:
                            tq = int(o.get('traded_qty', 0) or 0)
                            if tq > 0: actual_qty = tq
                            tp = o.get('traded_price', 0)
                            if tp and float(tp) > 0: actual_price = float(tp)
                            break
                except Exception:
                    pass

                if actual_qty <= 0:
                    self._fm.release(p['trade_id'], pnl=0)
                    continue

                sl_price = round(actual_price * (1 + SL_PCT / 100), 2)

                self._positions.open(
                    symbol=sym, side=Side.SELL, qty=actual_qty,
                    entry_price=actual_price, order_id=p['order_id'],
                    strategy=self.name, sl=sl_price,
                    trail_activate_pct=TRAIL_ACTIVATE, trail_pct=TRAIL_PCT,
                    trade_id=p['trade_id'],
                )

                is_reentry = sym in self._entered
                self._entered.add(sym)

                audit('anomaly', 'SECOND_CHANCE_ENTRY', sym,
                      side='SHORT', qty=actual_qty, entry=round(actual_price, 2),
                      sl=round(sl_price, 2), scan_ltp=round(p['_scan_ltp'], 2),
                      score=s['anomaly_score'], residual=round(s['residual'], 1))

                self._log.info(f'SECOND CHANCE SHORT {sym} filled={actual_qty} @ {actual_price:.1f} SL={sl_price:.1f}')

    def _fetch_market_lens(self):
        """Fetch all stocks from NSE Market Lens."""
        try:
            from trading.services.ml_enricher import _init, _poll, _cache, _cache_time
            import trading.services.ml_enricher as mle

            # Force fresh poll
            mle._cache_time = 0
            _poll()

            stocks = []
            for ticker, data in mle._cache.items():
                ltp = data.get('lastTradedPrice', 0)
                prev = data.get('previousClose', 0)
                op = data.get('openPrice', 0) or 0
                if not ltp or ltp <= 0 or not prev or prev <= 0:
                    continue
                if op <= 0:
                    continue  # skip stocks that haven't opened yet

                stocks.append({
                    'ticker': ticker,
                    'ltp': ltp,
                    'gain_from_prev': (ltp - prev) / prev * 100,
                    'day_high': data.get('dayHigh', 0) or 0,
                    'vwap': data.get('avgPrice', 0) or 0,
                    'gap': (op - prev) / prev * 100,
                    'sector': data.get('sector', '') or '',
                    'beta': data.get('beta', 0) or 0,
                    'dv': data.get('dayVolatility', 0) or 0,
                    'nm': data.get('netMargin', 0) or 0,
                    'dlv': data.get('deliveryPercentage', 0) or 0,
                    'vol': data.get('volume', 0) or 0,
                    'wk': data.get('oneWeekReturn', 0) or 0,
                    'ocf': data.get('operatingCashFlow', 0) or 0,
                    'dma200': data.get('dma200', 0) or 0,
                    'sixm': data.get('sixMonthReturn', 0) or 0,
                    'upper_circuit': data.get('priceBandHigher', 0) or 0,
                    'mcap': (data.get('marketCap', 0) or 0) / 1e7,
                    'mo': data.get('oneMonthReturn', 0) or 0,
                })
            return stocks
        except Exception as e:
            self._log.error(f'Market Lens fetch failed: {e}')
            import traceback; traceback.print_exc()
            return []
