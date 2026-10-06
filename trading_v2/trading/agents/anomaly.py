"""Anomaly Agent — finds stocks displaced from their sector ground, shorts them.

Flow:
  9:20  — Scan all stocks via Market Lens. Compute sector stats, residuals, anomaly scores.
           Take top 5 SHORT anomalies. Enter with MARKET order.
  10:12 — Delivery updates. Rescan. Enter NEW anomalies with freed capital.
  Monitor handles trail (activate 1%, trail 0.5%) and SL (2%).

Anomaly score (0-8, all relative to sector):
  +1 if residual > 3% (stock moved more than beta * sector explains)
  +1 if residual > 5% (extreme)
  +1 if volatility > 1.3x sector avg (amplified move)
  +1 if delivery < sector - 10pts (no real buying support)
  +1 if volume < 0.5x sector avg (no conviction)
  +1 if weekly return > sector + 5% (already outperformed)
  +1 if gap rank >= 90th percentile in sector (extreme outlier)
  +1 if unprofitable AND negative cash flow (no rocket)
"""
import time
import json
from datetime import datetime
from collections import defaultdict

from trading.base_agent import BaseAgent
from trading.types import TradeRequest, Side
from trading.logger import audit, get_logger

log = get_logger('anomaly')

# Heartbeat scans — when to scan for anomalies
ENTRY_TIMES = [
    (9, 20),   # after ORB, first scan
    (10, 14),  # after first delivery update (~10:12)
]

MAX_TRADES = 5
SL_PCT = 2.0
TRAIL_ACTIVATE = 1.0
TRAIL_PCT = 0.5
MIN_ANOMALY_SCORE = 5
MIN_PRICE = 50


class AnomalyAgent(BaseAgent):
    name = 'anomaly'

    def __init__(self, broker, positions, fund_manager, monitor):
        super().__init__(broker, positions, fund_manager, monitor)
        self._entered = set()  # tickers already entered today
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
        self._log.info('=== ANOMALY AGENT START ===')

        for entry_h, entry_m in ENTRY_TIMES:
            # Wait for entry time
            target = now.replace(hour=entry_h, minute=entry_m, second=0)
            wait = (target - datetime.now()).total_seconds()
            if wait > 0:
                self._log.info(f'Waiting {wait:.0f}s for {entry_h}:{entry_m:02d}')
                time.sleep(wait)
            elif wait < -300:
                # More than 5 min past — skip this heartbeat
                continue

            self._log.info(f'=== HEARTBEAT {entry_h}:{entry_m:02d} ===')
            try:
                self._scan_and_trade()
            except Exception as e:
                self._log.error(f'Scan error: {e}')
                import traceback; traceback.print_exc()

        self._log.info('=== ANOMALY AGENT DONE ===')

    def _scan_and_trade(self):
        """Fetch all stocks, compute anomaly scores, trade top N."""
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

        # Rank and take top N (skip already entered)
        candidates = [s for s in stocks
                      if s['anomaly_score'] >= MIN_ANOMALY_SCORE
                      and s['residual'] > 2
                      and s['ticker'] not in self._entered
                      and s['ltp'] >= MIN_PRICE]
        candidates.sort(key=lambda x: (-x['anomaly_score'], -x['residual']))

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
            audit('anomaly', 'CANDIDATE', s['ticker'],
                  score=s['anomaly_score'], residual=round(s['residual'], 1),
                  gap=round(s['gap'], 2), sector=s['sector'],
                  dv_vs=round(s['dv_vs'], 2), dlv_vs=round(s['dlv_vs'], 1),
                  vol_vs=round(s['vol_vs'], 2), wk_vs=round(s['wk_vs'], 1),
                  rank=s['rank'], nm=round(s['nm'], 1), ltp=s['ltp'])

        # Enter trades
        for s in to_trade:
            self._enter_short(s)

    def _enter_short(self, s):
        """Enter SHORT via LIMIT IOC order.
        Fills instantly at LTP-0.1%. Unfilled auto-cancelled.
        SL + trail handled by monitor."""
        sym = s['ticker']
        ltp = s['ltp']

        if not self._broker.has_symbol(sym):
            self._log.warning(f'{sym}: not in broker scrips')
            return

        live_ltp = self._broker.ltp_safe(sym)
        if live_ltp <= 0:
            live_ltp = ltp

        # Position sizing
        available = self._fm.available
        per_trade = available / max(1, MAX_TRADES - len(self._entered))
        qty = int(per_trade * 5 / live_ltp)
        if qty < 1:
            self._log.info(f'{sym}: qty=0, insufficient funds')
            return

        margin = qty * live_ltp / 5
        ok, amount, trade_id = self._fm.request(self.name, sym, margin)
        if not ok:
            self._log.info(f'{sym}: FM rejected (need Rs {margin:,.0f})')
            return

        try:
            # LIMIT IOC at 0.1% below LTP — fills instantly for shorts
            # If doesn't fill = stock too strong = dodged a bullet
            entry_price = round(live_ltp * 0.999, 2)
            order_id = self._broker.sell(sym, qty, price=entry_price,
                                         order_type='LIMIT', product='INTRADAY',
                                         validity='IOC')

            # Check actual fill after 2 seconds
            import time; time.sleep(2)
            filled, fill_price = self._broker.order_filled(order_id)

            if not filled:
                self._log.info(f'{sym}: IOC no fill — too strong to short')
                self._fm.release(trade_id, pnl=0)
                return

            # Get actual filled qty from order book
            actual_qty = qty  # default
            actual_price = fill_price if fill_price > 0 else entry_price
            try:
                book = self._broker._api.get_order_book() or []
                for o in book:
                    if o.get('id') == order_id or o.get('order_id') == order_id:
                        actual_qty = int(o.get('traded_qty', qty) or qty)
                        tp = o.get('traded_price', 0)
                        if tp and float(tp) > 0:
                            actual_price = float(tp)
                        break
            except Exception:
                pass

            if actual_qty <= 0:
                self._log.info(f'{sym}: filled qty=0')
                self._fm.release(trade_id, pnl=0)
                return

            sl_price = round(actual_price * (1 + SL_PCT / 100), 2)

            self._positions.open(
                symbol=sym, side=Side.SELL, qty=actual_qty,
                entry_price=actual_price, order_id=order_id,
                strategy=self.name, sl=sl_price,
                trail_activate_pct=TRAIL_ACTIVATE, trail_pct=TRAIL_PCT,
                trade_id=trade_id,
            )
            self._entered.add(sym)

            audit('anomaly', 'ENTRY', sym,
                  side='SHORT', qty=actual_qty, entry=round(actual_price, 2),
                  sl=round(sl_price, 2), margin=round(margin, 0),
                  score=s['anomaly_score'], residual=round(s['residual'], 1),
                  sector=s['sector'], gap=round(s['gap'], 2),
                  requested_qty=qty, order_id=order_id)

            self._log.info(f'SHORT {sym} filled={actual_qty}/{qty} @ {actual_price:.1f} '
                          f'SL={sl_price:.1f} score={s["anomaly_score"]} '
                          f'residual={s["residual"]:+.1f}')
        except Exception as e:
            self._log.error(f'{sym}: order failed: {e}')
            self._fm.release(trade_id, pnl=0)

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
                if not ltp or ltp <= 0 or not prev or prev <= 0:
                    continue

                stocks.append({
                    'ticker': ticker,
                    'ltp': ltp,
                    'gap': (data.get('openPrice', ltp) - prev) / prev * 100,
                    'sector': data.get('sector', '') or '',
                    'beta': data.get('beta', 0) or 0,
                    'dv': data.get('dayVolatility', 0) or 0,
                    'nm': data.get('netMargin', 0) or 0,
                    'dlv': data.get('deliveryPercentage', 0) or 0,
                    'vol': data.get('volume', 0) or 0,
                    'wk': data.get('oneWeekReturn', 0) or 0,
                    'ocf': data.get('operatingCashFlow', 0) or 0,
                })
            return stocks
        except Exception as e:
            self._log.error(f'Market Lens fetch failed: {e}')
            import traceback; traceback.print_exc()
            return []
