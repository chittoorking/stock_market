"""BaseAgent — extend this to add a new trading strategy.

Two types:
  1. Reactive: override on_signal(signal) — receives external notifications
  2. Autonomous: override run() — scans/polls independently

submit(TradeRequest) handles: FM → broker → position creation.
If anything fails, capital is released. No phantoms.
"""
from typing import Optional

from trading.types import TradeRequest, Side, Position
from trading.logger import get_logger


class BaseAgent:
    name: str = 'base'

    def __init__(self, broker, positions, fund_manager, monitor):
        self._broker = broker
        self._positions = positions
        self._fm = fund_manager
        self._monitor = monitor
        self._log = get_logger(self.name)
        self._log.info(f'Agent ready: {self.name}')

    def submit(self, req: TradeRequest) -> Optional[str]:
        """Submit trade. FM → broker → position. Returns pos_id or None."""
        # 1. Get price
        entry = req.entry if req.entry > 0 else self._broker.ltp_safe(req.symbol)
        if entry <= 0:
            self._log.error(f'No price: {req.symbol}')
            return None

        # 2. Calculate qty and margin
        if req.is_fno:
            base_lot = self._get_lot_size(req.symbol)
            lot = req.fno_lot_size if req.fno_lot_size > 0 else base_lot
            if base_lot <= 0:
                base_lot = 1
            qty = lot

            is_futures = "FUT" in req.symbol.upper()
            if req.fno_margin > 0:
                # Use Multyfi's actual margin requirement
                margin = req.fno_margin * (qty / base_lot) if base_lot > 0 else req.fno_margin
            elif is_futures:
                margin = entry * qty * 0.15  # fallback estimate
            else:
                margin = entry * qty  # options premium is the cost

            # Scale down if can't afford — try 2 lots, then 1 lot
            avail = self._fm.available
            while margin > avail and qty > base_lot:
                qty -= base_lot
                margin = entry * qty * 0.15 if is_futures else entry * qty
                self._log.info(f'{req.symbol}: scaling down to {qty} (avail=Rs {avail:,.0f})')
        else:
            sl = req.sl if req.sl > 0 else entry * 0.97
            qty = self._fm.calc_equity_qty(entry, sl)
            margin = qty * entry / 5  # MIS 5x

        # 3. FM gatekeeper
        ok, amount, trade_id = self._fm.request(self.name, req.symbol, margin)
        if not ok:
            self._log.info(f'FM rejected: {req.symbol}')
            return None

        # 3. Order
        try:
            if req.is_fno and req.fno_sec_id:
                if req.side == Side.BUY:
                    oid = self._broker.buy_fno(req.symbol, qty, req.fno_sec_id)
                else:
                    oid = self._broker.sell_fno(req.symbol, qty, req.fno_sec_id)
            elif req.order_type.value == 'LIMIT' and req.entry > 0:
                if req.side == Side.BUY:
                    oid = self._broker.buy_limit(req.symbol, qty, req.entry)
                else:
                    oid = self._broker.sell(req.symbol, qty, price=req.entry, order_type='LIMIT')
            else:
                if req.side == Side.BUY:
                    oid = self._broker.buy(req.symbol, qty)
                else:
                    oid = self._broker.sell(req.symbol, qty)
        except Exception as e:
            self._log.error(f'ORDER FAILED: {req.symbol} | {e}')
            self._fm.release(trade_id, pnl=0)
            return None

        # 4. Position (only after confirmed order)
        pos = self._positions.open(
            symbol=req.symbol, side=req.side, qty=qty,
            entry_price=entry, order_id=oid, strategy=self.name,
            sl=req.sl, target=req.target,
            trail_activate_pct=req.trail_activate_pct,
            trail_pct=req.trail_pct,
            fno_sec_id=req.fno_sec_id, trade_id=trade_id,
        )
        self._log.info(f'OPENED: {req.symbol} {req.side.value} qty={qty} @ {entry} | {req.reason}')
        return pos.id

    # Known lot sizes for index/commodity F&O
    _LOT_SIZES = {
        'NIFTY': 65, 'NIFTY 50': 65, 'BANKNIFTY': 30, 'SENSEX': 20,
        'GOLD': 100, 'GOLDM': 10, 'SILVER': 30, 'SILVERM': 5,
        'CRUDEOIL': 100, 'CRUDEOILM': 10, 'NATURALGAS': 1250,
    }

    def _get_lot_size(self, sym: str) -> int:
        """Get F&O lot size."""
        clean = sym.replace(' 50', '').replace(' ', '').strip().upper()
        # Strip expiry suffix: NATURALGAS26SEPFUT → NATURALGAS
        import re
        base = re.sub(r'\d{2}[A-Z]{3}FUT$', '', clean).rstrip('M') or clean
        return self._LOT_SIZES.get(clean) or self._LOT_SIZES.get(base) or 1

    def on_signal(self, signal) -> dict:
        """Override: handle incoming signal. Return result dict."""
        return {'error': f'{self.name} does not handle signals'}

    def run(self):
        """Override: autonomous scanning/polling."""
        pass
