"""Monitor — one background thread, one exit path.

Checks all positions every N seconds:
  SL → close
  Target → close
  Trail → close
  EOD 3:10 PM → close all
  External signal → close (via force_exit)

Every close goes through positions.close() → sell order + FM release.
"""
import threading
import time
from datetime import datetime

from trading.types import Side
from trading.logger import audit, get_logger

log = get_logger('monitor')


class Monitor:
    def __init__(self, broker, positions, fund_manager, poll_interval: int = 10, price_feed=None):
        self._broker = broker
        self._pos = positions
        self._fm = fund_manager
        self._poll = poll_interval
        self._running = False
        self._pf = price_feed  # WebSocket price feed (optional)

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name='monitor').start()
        log.info(f'Monitor started (poll={self._poll}s)')

    def stop(self):
        self._running = False

    def _loop(self):
        import traceback
        while self._running:
            try:
                self._tick()
            except Exception as e:
                log.error(f'Tick error: {e}')
                traceback.print_exc()
            time.sleep(self._poll)
        log.error('Monitor loop exited! _running is False')

    def _tick(self):
        now = datetime.now()

        # EOD 3:10 PM — close all positions in PARALLEL (FIFO order)
        if now.hour > 15 or (now.hour == 15 and now.minute >= 10):
            active = self._pos.active()
            if active:
                # Sort FIFO: earliest opened first
                active.sort(key=lambda p: getattr(p, '_opened_at', 0))
                log.info(f'EOD: closing {len(active)} positions in parallel')
                threads = []
                for p in active:
                    t = threading.Thread(target=self._exit,
                                         args=(p.id,), kwargs={'reason': 'EOD_EXIT'},
                                         daemon=True)
                    t.start()
                    threads.append(t)
                # Wait for all exits to complete (max 30s)
                for t in threads:
                    t.join(timeout=30)
            return

        # Batch LTP for all positions at once (faster than one-by-one)
        active = self._pos.active()
        if not active:
            return

        # Split equity and F&O positions
        equity_syms = []
        fno_positions = []
        for p in active:
            if getattr(p, 'fno_sec_id', ''):
                fno_positions.append(p)
            else:
                equity_syms.append(p.symbol)

        # Batch LTP for equity
        prices = {}
        if equity_syms:
            try:
                from trading.broker import _retry
                prices = _retry(lambda: self._broker._api.get_ltp(list(set(equity_syms))),
                                retries=3, delay=2, label='BATCH_LTP')
            except Exception as e:
                log.warning(f'Batch LTP failed after retries: {e}')

        # Individual LTP for F&O (WebSocket first, REST fallback)
        for pos in fno_positions:
            try:
                price = 0
                if self._pf:
                    price = self._pf.get_fno_ltp(pos.fno_sec_id)
                if price <= 0:
                    price = self._broker.ltp_by_sec_id(pos.fno_sec_id, pos.symbol)
                if price > 0:
                    prices[pos.symbol] = price
            except Exception as e:
                log.warning(f'FnO LTP failed {pos.symbol}: {e}')

        for pos in active:
            try:
                price = float(prices.get(pos.symbol, 0))
                if price <= 0:
                    continue
                self._check(pos, price)
            except Exception as e:
                log.warning(f'Check error {pos.symbol}: {e}')

    def _check(self, pos, price: float):
        if pos.side == Side.BUY:
            move = (price - pos.entry_price) / pos.entry_price * 100
        else:
            move = (pos.entry_price - price) / pos.entry_price * 100

        self._pos.update_peak(pos.id, price)

        # SL
        if pos.sl > 0:
            if (pos.side == Side.BUY and price <= pos.sl) or \
               (pos.side == Side.SELL and price >= pos.sl):
                self._exit(pos.id, price, 'SL_HIT')
                return

        # Target
        if pos.target > 0:
            if (pos.side == Side.BUY and price >= pos.target) or \
               (pos.side == Side.SELL and price <= pos.target):
                self._exit(pos.id, price, 'TARGET_HIT')
                return

        # Trail
        if pos.trail_activate_pct > 0 and pos.trail_pct > 0:
            if not pos.trail_active and move >= pos.trail_activate_pct:
                pos.trail_active = True
                audit('monitor', 'TRAIL_ON', pos.symbol, move=round(move, 2))

            if pos.trail_active:
                peak = pos.peak_price
                if pos.side == Side.BUY:
                    drop = (price - peak) / peak * 100
                else:
                    drop = (peak - price) / peak * 100
                if drop <= -pos.trail_pct:
                    self._exit(pos.id, price, 'TRAIL_EXIT')
                    return

    def _exit(self, pos_id: str, exit_price: float = 0, reason: str = 'MANUAL'):
        pos = self._pos.close(pos_id, exit_price=exit_price, reason=reason)
        if pos and pos.trade_id:
            self._fm.release(pos.trade_id, pnl=pos.pnl)

    def force_exit(self, symbol: str, reason: str = 'SIGNAL_EXIT',
                   strategy: str = None) -> bool:
        """Close position for symbol. If strategy is given, only close that strategy's position."""
        with self._pos._lock:
            candidates = [p for p in self._pos._positions.values()
                          if p.symbol == symbol
                          and (strategy is None or p.strategy == strategy)]
        if not candidates:
            return False
        pos = candidates[0]
        price = self._broker.ltp_safe(pos.symbol, pos.entry_price,
                                       fno_sec_id=getattr(pos, 'fno_sec_id', None))
        self._exit(pos.id, price, reason)
        return True
