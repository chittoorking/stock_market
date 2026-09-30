"""News ORB Agent — Gemini news scan + Opening Range Breakout entry.

Timeline:
  9:05  — Scan RSS + Gemini analysis (must finish by 9:14)
  9:15  — Market opens, collect OR (15 min)
  9:30  — Apply filters, place limit orders
  9:30-2:45 — Monitor fills via broker order book
  2:45  — Cancel unfilled orders

Safeguards:
  - Check time before wasting Gemini quota
  - Hard deadline: analysis must finish by 9:14
  - Retry failed Gemini calls once
  - NaN protection on ATR/SL
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

        # Skip if past 10 AM (missed the window)
        if now.hour >= 10:
            self._log.info('Past 10 AM, skipping today')
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

        # Phase 5: Watch for breakout, place market orders on confirm
        pending = self._place_limit_orders(filtered)
        if not pending:
            return

        # Phase 6: Create positions for filled breakout orders
        self._process_fills(pending)

        self._log.info('=== NEWS ORB DONE ===')

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

    def _place_limit_orders(self, filtered: list) -> list:
        """Watch LTP, place MARKET order only when breakout confirms.
        BUY: wait for LTP >= ORB high + buffer, then buy at market.
        SELL: wait for LTP <= ORB low - buffer, then sell at market.
        """
        watchlist = []
        for t in filtered:
            sym = t['nse_symbol']
            entry_price = t['entry_price']
            call = t['call']

            sl_pct = orb.get_atr_sl(sym)
            if math.isnan(sl_pct) or sl_pct <= 0:
                sl_pct = 3.0

            sl_price = entry_price * (1 - sl_pct / 100)
            qty = self._fm.calc_equity_qty(entry_price, sl_price)
            margin = qty * entry_price / 5
            ok, amount, trade_id = self._fm.request(self.name, sym, margin)
            if not ok:
                self._log.info(f'FM rejected: {sym} (need Rs {margin:,.0f})')
                continue

            watchlist.append({
                'sym': sym, 'call': call, 'entry_price': entry_price,
                'qty': qty, 'trade_id': trade_id, 'sl_pct': sl_pct,
                'projection': t['projection'],
            })
            self._log.info(f'WATCHING {sym} {call} for breakout @ {entry_price:.1f}')
            audit('news_orb', 'WATCHING_BREAKOUT', sym, side=call,
                  entry=entry_price, qty=qty)

        if not watchlist:
            return []

        # Poll LTP every 10s, place MARKET order when breakout confirms
        pending = []
        remaining = list(watchlist)

        while remaining:
            now = datetime.now()
            if now.hour > 14 or (now.hour == 14 and now.minute >= 45):
                self._log.info(f'Deadline: {len(remaining)} breakouts not triggered')
                for w in remaining:
                    self._fm.release(w['trade_id'], pnl=0)
                    audit('news_orb', 'BREAKOUT_TIMEOUT', w['sym'])
                break

            for w in remaining[:]:
                ltp = self._broker.ltp_safe(w['sym'])
                if ltp <= 0:
                    continue

                triggered = False
                if w['call'] == 'BUY' and ltp >= w['entry_price']:
                    triggered = True
                elif w['call'] == 'SELL' and ltp <= w['entry_price']:
                    triggered = True

                if triggered:
                    self._log.info(f'BREAKOUT {w["sym"]} {w["call"]} LTP={ltp:.1f} >= {w["entry_price"]:.1f}')
                    try:
                        if w['call'] == 'BUY':
                            order_id = self._broker.buy(w['sym'], w['qty'])
                        else:
                            order_id = self._broker.sell(w['sym'], w['qty'])

                        pending.append({
                            'sym': w['sym'], 'call': w['call'],
                            'entry_price': ltp, 'qty': w['qty'],
                            'order_id': order_id, 'trade_id': w['trade_id'],
                            'sl_pct': w['sl_pct'], 'projection': w['projection'],
                        })
                        audit('news_orb', 'BREAKOUT_ENTRY', w['sym'], side=w['call'],
                              ltp=ltp, qty=w['qty'], order_id=order_id)
                    except Exception as e:
                        self._log.error(f'Breakout order failed {w["sym"]}: {e}')
                        self._fm.release(w['trade_id'], pnl=0)
                    remaining.remove(w)

            if remaining:
                time.sleep(10)

        self._log.info(f'{len(pending)} breakout orders filled')
        return pending

    def _process_fills(self, pending: list):
        """Create positions for breakout market orders (already filled)."""
        for order_info in pending:
            sym = order_info['sym']
            call = order_info['call']
            actual = order_info['entry_price']
            sl_pct = order_info['sl_pct']

            if math.isnan(sl_pct) or sl_pct <= 0:
                sl_pct = 3.0
            if call == 'BUY':
                sl_price = actual * (1 - sl_pct / 100)
            else:
                sl_price = actual * (1 + sl_pct / 100)

            side = Side.BUY if call == 'BUY' else Side.SELL

            self._positions.open(
                symbol=sym, side=side, qty=order_info['qty'],
                entry_price=actual, order_id=order_info['order_id'],
                strategy=self.name, sl=sl_price,
                trail_activate_pct=0.3, trail_pct=0.15,
                trade_id=order_info['trade_id'],
            )
            self._log.info(f'POSITION: {sym} {call} @ {actual:.1f} SL={sl_price:.1f}')
