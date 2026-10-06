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
    def __init__(self, broker, positions, fund_manager, poll_interval: int = 10):
        self._broker = broker
        self._pos = positions
        self._fm = fund_manager
        self._poll = poll_interval
        self._running = False

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

        # EOD 3:10 PM — close everything, verify with broker
        if now.hour > 15 or (now.hour == 15 and now.minute >= 10):
            active = self._pos.active()
            if active:
                log.info(f'EOD: closing {len(active)} positions')
                for p in active:
                    self._exit(p.id, reason='EOD_EXIT')

            # Verify broker has zero positions — keep trying until clean
            import time as _t
            for verify_attempt in range(5):
                _t.sleep(3)
                try:
                    from live.indmoney_client import get_positions
                    broker_pos = get_positions() or []
                    open_pos = [p for p in broker_pos if abs(p.get('net_qty', 0)) > 0]
                    if not open_pos:
                        log.info('EOD: broker confirmed zero positions')
                        break
                    # Still have positions — force close with wider price
                    log.warning(f'EOD: broker still has {len(open_pos)} positions, attempt {verify_attempt+2}')
                    for bp in open_pos:
                        sym = bp.get('symbol', '')
                        qty = abs(bp.get('net_qty', 0))
                        is_short = bp.get('net_qty', 0) < 0
                        if qty <= 0 or not sym:
                            continue
                        ltp = self._broker.ltp_safe(sym)
                        if ltp <= 0:
                            continue
                        # Widen buffer on each retry: 0.5%, 1%, 2%, 3%, 5%
                        buffer = [0.005, 0.01, 0.02, 0.03, 0.05][min(verify_attempt, 4)]
                        try:
                            if is_short:
                                price = round(ltp * (1 + buffer), 2)
                                self._broker.buy(sym, qty, price=price,
                                                order_type='LIMIT', validity='IOC')
                            else:
                                price = round(ltp * (1 - buffer), 2)
                                self._broker.sell(sym, qty, price=price,
                                                 order_type='LIMIT', validity='IOC')
                            log.info(f'EOD retry: {sym} qty={qty} price={price} buffer={buffer*100:.1f}%')
                        except Exception as e:
                            log.error(f'EOD retry failed {sym}: {e}')
                except Exception as e:
                    log.error(f'EOD verify error: {e}')

            return

        # Batch LTP for all positions at once (faster than one-by-one)
        active = self._pos.active()
        if not active:
            return
        symbols = list(set(p.symbol for p in active))
        try:
            prices = self._broker._api.get_ltp(symbols)
        except Exception:
            prices = {}
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
        if pos:
            audit('monitor', 'EXIT', pos.symbol, reason=reason,
                  entry=pos.entry_price, exit=round(pos.exit_price, 2),
                  pnl=round(pos.pnl, 2), pnl_pct=round(pos.pnl_pct, 2),
                  side=pos.side.value, qty=pos.qty,
                  peak=round(pos.peak_price, 2) if pos.peak_price else 0,
                  trail_active=pos.trail_active)
            if pos.trade_id:
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
        price = self._broker.ltp_safe(pos.symbol, pos.entry_price)
        self._exit(pos.id, price, reason)
        return True
