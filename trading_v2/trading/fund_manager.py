"""Fund Manager — gatekeeper only.

Strategy decides trade size. Fund manager only checks:
  1. Is there enough balance?
  2. Max 6 concurrent trades
  3. Max 3 per strategy
  4. Daily loss > 10% → kill switch
"""
import threading
from datetime import datetime

from trading.logger import audit, get_logger

log = get_logger('fund_manager')

from trading.config import MAX_CONCURRENT as MAX_TOTAL, MAX_PER_STRATEGY, DAILY_LOSS_PCT, EQUITY_RISK_PCT


class FundManager:
    def __init__(self, pool: float = 100_000, **kwargs):
        try:
            import sys, os
            sys.path.insert(0, os.path.expanduser('~/news-trading'))
            from live.indmoney_client import get_funds
            bal = get_funds()
            if bal and bal > 0:
                self.pool = bal
            else:
                self.pool = pool
        except Exception:
            self.pool = pool

        self.daily_loss_limit = self.pool * DAILY_LOSS_PCT / 100
        self._deployed = {}
        self._daily_pnl = 0.0
        self._loss_hit = False
        self._counter = 0
        self._lock = threading.Lock()

        log.info(f'FM: pool=Rs {self.pool:,.0f} | max={MAX_TOTAL} | '
                 f'equity_risk={EQUITY_RISK_PCT}% | loss_limit=Rs {self.daily_loss_limit:,.0f}')

    def calc_equity_qty(self, price, sl_price, lot_size=1):
        """For equity strategies (News ORB, IPO): qty from 2% risk budget."""
        if price <= 0 or sl_price <= 0:
            return lot_size
        sl_dist = abs(price - sl_price)
        if sl_dist <= 0:
            return lot_size
        risk_amt = self.pool * EQUITY_RISK_PCT / 100
        qty = int(risk_amt / sl_dist)
        return max(qty, lot_size)

    @property
    def available(self):
        with self._lock:
            return self.pool - sum(d['amount'] for d in self._deployed.values())

    def request(self, strategy, symbol, amount, conviction=5):
        """Strategy says how much it needs. FM just checks if possible."""
        with self._lock:
            if self._loss_hit:
                return False, 0, ''

            if len(self._deployed) >= MAX_TOTAL:
                audit('fm', 'REJECT', symbol, strategy=strategy, reason='max_total')
                return False, 0, ''

            active = sum(1 for d in self._deployed.values() if d['strategy'] == strategy)
            if active >= MAX_PER_STRATEGY:
                audit('fm', 'REJECT', symbol, strategy=strategy, reason='max_strategy')
                return False, 0, ''

            deployed = sum(d['amount'] for d in self._deployed.values())
            if self.pool - deployed < amount:
                audit('fm', 'REJECT', symbol, strategy=strategy,
                      reason=f'no_funds need={amount:.0f} avail={self.pool-deployed:.0f}')
                return False, 0, ''

            self._counter += 1
            tid = f'{strategy}_{self._counter}'
            self._deployed[tid] = {
                'strategy': strategy, 'sym': symbol,
                'amount': amount, 'time': datetime.now().strftime('%H:%M'),
            }

        audit('fm', 'APPROVE', symbol, strategy=strategy, amount=round(amount), trade_id=tid)
        return True, amount, tid

    def release(self, trade_id, pnl=0):
        with self._lock:
            entry = self._deployed.pop(trade_id, None)
            if not entry:
                return
            self._daily_pnl += pnl
            if self._daily_pnl <= -self.daily_loss_limit:
                self._loss_hit = True
                log.error(f'LOSS LIMIT: {self._daily_pnl:.0f}')
        audit('fm', 'RELEASE', entry['sym'], trade_id=trade_id, pnl=round(pnl, 2))

    def status(self):
        with self._lock:
            deployed = sum(d['amount'] for d in self._deployed.values())
            return {
                'pool': self.pool, 'deployed': round(deployed),
                'available': round(self.pool - deployed),
                'daily_pnl': round(self._daily_pnl, 2),
                'loss_hit': self._loss_hit,
                'active': len(self._deployed),
            }

    def reset_daily(self):
        with self._lock:
            self._daily_pnl = 0
            self._loss_hit = False
            self._deployed.clear()
        try:
            import sys, os
            sys.path.insert(0, os.path.expanduser('~/news-trading'))
            from live.indmoney_client import get_funds
            bal = get_funds()
            if bal and bal > 0:
                self.pool = bal
        except Exception:
            pass
        audit('fm', 'DAILY_RESET', pool=self.pool)
