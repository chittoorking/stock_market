"""News Confirmation Agent — Gemini news scan + delivery confirmation entry.

Thesis: Gemini picks stocks with news catalysts. Enter only when BOTH
price direction AND delivery increase confirm institutional participation.

Timeline:
  9:05  — Scan RSS + NSE/BSE filings + Gemini analysis
  9:15  — Collect opening range (5 min)
  9:20  — Record baseline LTP + delivery for each candidate
  11:12 — Check confirmation: price moved > 0.5% in Gemini direction
          AND delivery increased > 2pp from baseline
  11:12 — Enter confirmed trades (currently PAPER_MODE = False)

Exit: SL 2%, trail activate 1% / trail 0.5%, EOD 3:10 PM.

Status: 5W 0L on paper. Need 20+ confirmed trades before going live.
"""
import math
import time
from datetime import datetime

from trading.base_agent import BaseAgent
from trading.types import Side
from trading.services import rss, gemini, orb
from trading.logger import audit


class NewsOrbAgent(BaseAgent):
    name = 'news_orb'
    PAPER_MODE = False  # Set False to go live

    def run(self):
        """Loop daily: wait for 9:05 -> scan -> analyze -> OR -> filter -> trade."""
        while True:
            try:
                self._run_day()
            except Exception as e:
                self._log.error(f'Day failed: {e}')

            # Sleep until next trading day 9:05 AM
            self._sleep_until_next_day()

    def _sleep_until_next_day(self):
        """Sleep until next weekday 9:05 AM."""
        from datetime import timedelta
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=5, second=0, microsecond=0)
        # Skip weekends
        while tomorrow.weekday() >= 5:
            tomorrow += timedelta(days=1)
        wait = (tomorrow - datetime.now()).total_seconds()
        if wait > 0:
            self._log.info(f'Sleeping {wait/3600:.1f}h until {tomorrow.strftime("%Y-%m-%d %H:%M")}')
            time.sleep(wait)

    def _run_day(self):
        """Single day flow: scan -> analyze -> OR -> filter -> limit -> fills."""
        now = datetime.now()

        # Skip if past 12 PM (missed the confirmation window)
        if now.hour >= 12:
            self._log.info('Past 12 PM, skipping today')
            return
        if now.weekday() >= 5:
            self._log.info('Weekend, skipping')
            return

        # Wait until 9:05
        scan_time = now.replace(hour=9, minute=5, second=0, microsecond=0)
        if now < scan_time:
            wait = (scan_time - now).total_seconds()
            self._log.info(f'Waiting {wait:.0f}s for 9:05 AM')
            time.sleep(wait)

        date_str = datetime.now().strftime('%Y-%m-%d')
        self._log.info(f'=== NEWS SCAN {date_str} ===')

        # Phase 1: Fetch + analyze
        trades = self._scan_and_analyze(date_str)
        if not trades:
            self._log.info('No trades found')
            audit("news_orb", "NO_TRADES")
            return

        # Phase 2: Wait for OR window (9:15-9:30)
        if not self._wait_for_or_window():
            self._log.error('Missed OR window')
            return

        # Phase 3: Collect opening range
        symbols = list(set(t['nse_symbol'] for t in trades))
        self._log.info(f'Collecting OR for {len(symbols)} stocks: {symbols}')
        or_data = orb.collect_range(self._broker, symbols)

        # Phase 4: Apply filters
        filtered = orb.apply_filters(trades, or_data)
        if not filtered:
            self._log.info('No trades passed ORB filters')
            return
        self._log.info(f'{len(filtered)} trades passed filters')

        # Phase 5: Record baseline at 9:20 for confirmation check
        baseline = {}
        for t in filtered:
            sym = t['nse_symbol']
            try:
                from trading.services.ml_enricher import get_ml_snapshot
                ml = get_ml_snapshot(sym)
                if ml:
                    baseline[sym] = {
                        'ltp': ml.get('lastTradedPrice', 0),
                        'dlv': ml.get('deliveryPercentage', 0),
                        'direction': t.get('call', t.get('direction', 'BUY')),
                        'trade': t,
                    }
                    self._log.info(f'BASELINE {sym}: LTP={baseline[sym]["ltp"]:.1f} Dlv={baseline[sym]["dlv"]:.0f}%')
            except Exception as e:
                self._log.error(f'Baseline error {sym}: {e}')

        if not baseline:
            self._log.info('No baseline data')
            return

        # Phase 6: Wait until 11:12 for confirmation
        now = datetime.now()
        target = now.replace(hour=11, minute=12, second=0, microsecond=0)
        wait = (target - now).total_seconds()
        if wait > 0:
            self._log.info(f'Waiting {wait:.0f}s for 11:12 confirmation check')
            time.sleep(wait)

        # Phase 7: Check confirmation — price + delivery both confirming
        confirmed = []
        for sym, bl in baseline.items():
            try:
                from trading.services.ml_enricher import get_ml_snapshot
                import trading.services.ml_enricher as mle
                mle._cache_time = 0
                mle._poll()
                ml = get_ml_snapshot(sym)
                if not ml:
                    continue

                ltp_now = ml.get('lastTradedPrice', 0)
                dlv_now = ml.get('deliveryPercentage', 0)
                ltp_base = bl['ltp']
                dlv_base = bl['dlv']
                direction = bl['direction']

                if ltp_base <= 0:
                    continue

                price_chg = (ltp_now - ltp_base) / ltp_base * 100
                dlv_chg = dlv_now - dlv_base

                # Check confirmation (minimum thresholds to avoid noise)
                if direction == 'BUY':
                    price_confirms = price_chg > 0.5  # at least 0.5% move
                else:
                    price_confirms = price_chg < -0.5
                dlv_confirms = dlv_chg > 2  # at least 2pp delivery increase

                audit('news_orb', 'CONFIRMATION_CHECK', sym,
                      direction=direction, price_chg=round(price_chg, 2),
                      dlv_chg=round(dlv_chg, 1), price_confirms=price_confirms,
                      dlv_confirms=dlv_confirms, both=price_confirms and dlv_confirms,
                      ltp_base=round(ltp_base, 2), ltp_now=round(ltp_now, 2),
                      dlv_base=round(dlv_base, 1), dlv_now=round(dlv_now, 1))

                if price_confirms and dlv_confirms:
                    self._log.info(f'CONFIRMED {sym} {direction}: price={price_chg:+.1f}% dlv={dlv_chg:+.0f}pp')
                    confirmed.append({
                        'sym': sym,
                        'direction': direction,
                        'entry_price': ltp_now,
                        'trade': bl['trade'],
                        'price_chg': price_chg,
                        'dlv_chg': dlv_chg,
                    })
                else:
                    reason = 'price' if not price_confirms else 'delivery'
                    self._log.info(f'SKIP {sym}: {reason} not confirmed (price={price_chg:+.1f}% dlv={dlv_chg:+.0f}pp)')
            except Exception as e:
                self._log.error(f'Confirmation error {sym}: {e}')

        if not confirmed:
            self._log.info('No trades confirmed at 11:12')
            return

        # Phase 8: Enter confirmed trades
        for conf in confirmed:
            sym = conf['sym']
            direction = conf['direction']

            # Paper mode: log and skip
            if self.PAPER_MODE:
                audit('news_orb', 'PAPER_CONFIRMED', sym,
                      direction=direction, entry=round(conf['entry_price'], 2),
                      price_chg=round(conf['price_chg'], 2),
                      dlv_chg=round(conf['dlv_chg'], 1))
                self._log.info(f'PAPER: {direction} {sym} @ {conf["entry_price"]:.1f} '
                              f'price={conf["price_chg"]:+.1f}% dlv={conf["dlv_chg"]:+.0f}pp')
                continue

            if not self._broker.has_symbol(sym):
                self._log.warning(f'{sym}: not in broker scrips')
                continue

            live_ltp = self._broker.ltp_safe(sym)
            if live_ltp <= 0:
                live_ltp = conf['entry_price']

            # Position sizing
            available = self._fm.available
            # Fixed position size: pool / 10 -- uniform risk, cap per trade
            per_trade = self._fm.pool / 10
            qty = int(per_trade * 5 / live_ltp)
            if qty < 1:
                continue

            margin = qty * live_ltp / 5
            ok, amount, trade_id = self._fm.request(self.name, sym, margin)
            if not ok:
                continue

            try:
                if direction == 'BUY':
                    order_id = self._broker.buy(sym, qty)
                else:
                    order_id = self._broker.sell(sym, qty, price=round(live_ltp * 0.999, 2),
                                                 order_type='LIMIT', product='INTRADAY')

                if not order_id:
                    self._fm.release(trade_id, pnl=0)
                    continue

                sl_pct = 2.0
                if direction == 'BUY':
                    sl = round(live_ltp * (1 - sl_pct / 100), 2)
                else:
                    sl = round(live_ltp * (1 + sl_pct / 100), 2)

                self._positions.open(
                    symbol=sym,
                    side=Side.BUY if direction == 'BUY' else Side.SELL,
                    qty=qty, entry_price=live_ltp, order_id=order_id,
                    strategy=self.name, sl=sl,
                    trail_activate_pct=1.0, trail_pct=0.5,
                    trade_id=trade_id,
                )

                audit('news_orb', 'CONFIRMED_ENTRY', sym,
                      side=direction, qty=qty, entry=round(live_ltp, 2),
                      sl=round(sl, 2), price_chg=round(conf['price_chg'], 2),
                      dlv_chg=round(conf['dlv_chg'], 1))

                self._log.info(f'ENTRY {direction} {sym} qty={qty} @ {live_ltp:.1f} SL={sl:.1f}')

            except Exception as e:
                self._log.error(f'{sym}: order failed: {e}')
                self._fm.release(trade_id, pnl=0)

        self._log.info('=== NEWS CONFIRMATION DONE ===')

    def _scan_and_analyze(self, date_str: str) -> list:
        """Fetch RSS, filter, analyze. No hard deadline — OR collection starts at 9:15 regardless."""
        # Fetch RSS
        articles = rss.fetch_all()
        self._log.info(f'RSS: {len(articles)} articles')
        audit('news_orb', 'RSS_FETCH', count=len(articles))
        if len(articles) < 3:
            return []

        # Filter headlines (cheap Gemini, no grounding)
        articles = gemini.filter_headlines(articles)
        self._log.info(f'After filter: {len(articles)} stock-relevant')
        audit('news_orb', 'HEADLINE_FILTER', count=len(articles))

        # Analyze in batches — take as long as needed
        all_trades = []
        batch_size = 20
        for i in range(0, len(articles), batch_size):
            batch = articles[i:i + batch_size]
            batch_num = i // batch_size + 1
            self._log.info(f'Batch {batch_num}: {len(batch)} headlines')

            trades = gemini.analyze_stocks(batch, date_str)
            if not trades:
                self._log.warning(f'Batch {batch_num} returned 0 trades, retrying...')
                time.sleep(3)
                trades = gemini.analyze_stocks(batch, date_str)

            all_trades.extend(trades)
            time.sleep(2)

        # Dedup
        trades = self._dedup(all_trades)
        self._log.info(f'Total: {len(trades)} trades after dedup')
        audit('news_orb', 'TRADES_FOUND', count=len(trades))
        return trades

    def _dedup(self, trades: list) -> list:
        """Dedup by nse_symbol. Log conflicts."""
        seen = {}
        result = []
        for t in trades:
            sym = t['nse_symbol']
            if sym in seen:
                prev = seen[sym]
                if prev['call'] != t['call']:
                    self._log.warning(f'CONFLICT: {sym} {prev["call"]} vs {t["call"]}')
                continue
            seen[sym] = t
            result.append(t)
        return result

    def _wait_for_or_window(self) -> bool:
        """Wait until 9:15. Return False if past 9:30."""
        now = datetime.now()
        open_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
        or_end = now.replace(hour=9, minute=30, second=0, microsecond=0)

        if now > or_end:
            return False

        if now < open_time:
            wait = (open_time - now).total_seconds()
            self._log.info(f'Waiting {wait:.0f}s for 9:15')
            time.sleep(wait)

        return True

