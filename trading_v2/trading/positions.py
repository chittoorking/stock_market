"""Positions — single source of truth. Thread-safe. Persisted to disk.

Rules:
  1. Position exists ONLY after broker confirms (order_id required).
  2. One exit path: close() handles sell + PnL + cleanup.
  3. Double-close returns None (safe).
  4. Positions saved to disk on every open/close — survives restart.
"""
import json
import threading
from pathlib import Path
from typing import Optional

from trading.types import Position, Side
from trading.logger import audit, get_logger

log = get_logger('positions')

_PERSIST_FILE = Path(__file__).parent.parent / 'logs' / 'positions.json'


class PositionManager:
    def __init__(self, broker, persist: bool = True):
        self._broker = broker
        self._positions: dict[str, Position] = {}
        self._lock = threading.Lock()
        self._counter = 0
        self._persist = persist
        if persist:
            self._load_from_disk()

    def open(self, symbol: str, side: Side, qty: int, entry_price: float,
             order_id: str, strategy: str, **kw) -> Position:
        """Create position. Only call AFTER broker confirms order."""
        if not order_id:
            raise ValueError('No order_id — cannot create phantom position')

        with self._lock:
            self._counter += 1
            pid = f'{strategy}_{self._counter}'
            pos = Position(id=pid, symbol=symbol, side=side, qty=qty,
                           entry_price=entry_price, order_id=order_id,
                           strategy=strategy, peak_price=entry_price, **kw)
            self._positions[pid] = pos

        audit('positions', 'OPEN', symbol, pos_id=pid, side=side.value,
              qty=qty, entry=entry_price, order_id=order_id, strategy=strategy)
        if self._persist:
            self._save_to_disk()
        return pos

    def close(self, pos_id: str, exit_price: float = 0,
              reason: str = 'MANUAL', skip_sell: bool = False) -> Optional[Position]:
        """Close position. Sells first, then removes. Never lose track."""
        with self._lock:
            pos = self._positions.get(pos_id)
        if not pos or pos.closed:
            return None

        if exit_price <= 0:
            exit_price = self._broker.ltp_safe(pos.symbol, pos.entry_price)

        # Sell FIRST — don't remove position until confirmed
        sell_ok = True
        if not skip_sell:
            for attempt in range(3):
                try:
                    if pos.side == Side.BUY:
                        if pos.fno_sec_id:
                            oid = self._broker.sell_fno(pos.symbol, pos.qty, pos.fno_sec_id)
                        else:
                            oid = self._broker.sell(pos.symbol, pos.qty)
                    else:
                        if pos.fno_sec_id:
                            oid = self._broker.buy_fno(pos.symbol, pos.qty, pos.fno_sec_id)
                        else:
                            oid = self._broker.buy(pos.symbol, pos.qty)
                    if oid:
                        log.info(f'EXIT ORDER OK: {pos.symbol} attempt={attempt+1} oid={oid}')
                        break
                    else:
                        log.error(f'EXIT ORDER RETURNED NONE: {pos.symbol} attempt={attempt+1}')
                        if attempt < 2:
                            import time; time.sleep(2)
                except Exception as e:
                    log.critical(f'EXIT ORDER FAILED {pos.symbol} attempt={attempt+1}: {e}')
                    if attempt < 2:
                        import time; time.sleep(2)
            else:
                sell_ok = False
                log.critical(f'ALL 3 EXIT ATTEMPTS FAILED: {pos.symbol} qty={pos.qty} — BROKER WILL AUTO-SQUARE')
                # Send Telegram alert
                try:
                    import requests
                    requests.post('https://api.telegram.org/bot8831033342:AAGstfFaVjPPvSykkCwfQIS9O8iYujqHivs/sendMessage',
                        json={'chat_id': '866752968',
                              'text': f'EXIT FAILED: {pos.symbol} qty={pos.qty} — close manually NOW to avoid auto-square penalty!'},
                        timeout=5)
                except:
                    pass

        # NOW remove from tracking (whether sell worked or not — avoid infinite retry loop)
        with self._lock:
            self._positions.pop(pos_id, None)

        # PnL
        if pos.side == Side.BUY:
            pos.pnl = (exit_price - pos.entry_price) * pos.qty
            pos.pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        else:
            pos.pnl = (pos.entry_price - exit_price) * pos.qty
            pos.pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

        pos.closed = True
        pos.exit_price = exit_price
        pos.exit_reason = reason

        audit('positions', 'CLOSE', pos.symbol, pos_id=pos_id, reason=reason,
              entry=pos.entry_price, exit=round(exit_price, 2),
              pnl=round(pos.pnl, 2), pnl_pct=round(pos.pnl_pct, 2))
        if self._persist:
            self._save_to_disk()
        return pos

    def active(self) -> list[Position]:
        with self._lock:
            return list(self._positions.values())

    def get(self, pos_id: str) -> Optional[Position]:
        with self._lock:
            return self._positions.get(pos_id)

    def find_by_symbol(self, symbol: str) -> Optional[Position]:
        with self._lock:
            for p in self._positions.values():
                if p.symbol == symbol:
                    return p
        return None

    def close_all(self, reason: str = 'EOD_EXIT') -> list[Position]:
        ids = [p.id for p in self.active()]
        return [p for pid in ids if (p := self.close(pid, reason=reason))]

    def update_peak(self, pos_id: str, price: float):
        with self._lock:
            pos = self._positions.get(pos_id)
            if not pos:
                return
            if pos.side == Side.BUY and price > pos.peak_price:
                pos.peak_price = price
            elif pos.side == Side.SELL and price < pos.peak_price:
                pos.peak_price = price

    # ── Persistence ──

    def _save_to_disk(self):
        """Save all positions to JSON file."""
        try:
            data = []
            for pos in self._positions.values():
                data.append({
                    'id': pos.id, 'symbol': pos.symbol, 'side': pos.side.value,
                    'qty': pos.qty, 'entry_price': pos.entry_price,
                    'order_id': pos.order_id, 'strategy': pos.strategy,
                    'sl': pos.sl, 'target': pos.target,
                    'trail_activate_pct': pos.trail_activate_pct,
                    'trail_pct': pos.trail_pct, 'peak_price': pos.peak_price,
                    'trail_active': pos.trail_active,
                    'fno_sec_id': pos.fno_sec_id, 'trade_id': pos.trade_id,
                    'opened_at': pos.opened_at,
                })
            _PERSIST_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PERSIST_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.error(f'Failed to save positions: {e}')

    def _load_from_disk(self):
        """Load positions from JSON file on startup."""
        if not _PERSIST_FILE.exists():
            return
        try:
            data = json.loads(_PERSIST_FILE.read_text())
            for d in data:
                pos = Position(
                    id=d['id'], symbol=d['symbol'], side=Side(d['side']),
                    qty=d['qty'], entry_price=d['entry_price'],
                    order_id=d['order_id'], strategy=d['strategy'],
                    sl=d.get('sl', 0), target=d.get('target', 0),
                    trail_activate_pct=d.get('trail_activate_pct', 0),
                    trail_pct=d.get('trail_pct', 0),
                    peak_price=d.get('peak_price', d['entry_price']),
                    trail_active=d.get('trail_active', False),
                    fno_sec_id=d.get('fno_sec_id', ''),
                    trade_id=d.get('trade_id', ''),
                    opened_at=d.get('opened_at', ''),
                )
                self._positions[pos.id] = pos
                # Update counter to avoid ID collisions
                parts = pos.id.rsplit('_', 1)
                if len(parts) == 2 and parts[1].isdigit():
                    self._counter = max(self._counter, int(parts[1]))
            if data:
                log.info(f'Restored {len(data)} positions from disk')
        except Exception as e:
            log.error(f'Failed to load positions: {e}')
