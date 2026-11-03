"""Fund Manager — gatekeeper only.

Strategy decides trade size. Fund manager only checks:
  1. Is there enough balance?
  2. Max 7 concurrent trades
  3. Max 5 per strategy
  4. Daily loss > 5% → kill switch
"""
import re
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
        self._daily_pnl_equity = 0.0
        self._daily_pnl_fno = 0.0
        self._loss_hit_equity = False
        self._loss_hit_fno = False
        self._counter = 0
        self._lock = threading.Lock()

        log.info(f'FM: pool=Rs {self.pool:,.0f} | max={MAX_TOTAL} | '
                 f'equity_risk={EQUITY_RISK_PCT}% | loss_limit=Rs {self.daily_loss_limit:,.0f}')

    def calc_equity_qty(self, price, sl_price, lot_size=1):
        """Qty from EQUITY_RISK_PCT of total pool. Same size every trade.
        Floor: minimum Rs 10K margin (Rs 50K notional at 5x MIS).
        If margin exceeds available, FM.request() will reject."""
        if price <= 0 or sl_price <= 0:
            return lot_size
        sl_dist = abs(price - sl_price)
        if sl_dist <= 0:
            return lot_size
        risk_amt = self.pool * EQUITY_RISK_PCT / 100
        qty = int(risk_amt / sl_dist)
        margin = qty * price / 5
        # Floor: minimum Rs 10K margin per trade (Rs 50K notional)
        # Below that, charges eat >30% of wins
        if margin < 10000:
            qty = int(10000 * 5 / price)  # Rs 50K notional
        return max(qty, lot_size)

    @property
    def available(self):
        """Available capital after deployed + 10% charge buffer."""
        with self._lock:
            buffer = self.pool * 0.10  # 10% reserved for charges
            deployed = sum(d['amount'] for d in self._deployed.values())
            return self.pool - deployed - buffer

    def request(self, strategy, symbol, amount, conviction=5):
        """Strategy says how much it needs. FM just checks if possible."""
        if amount <= 0:
            return False, 0, ""
        with self._lock:
            # Separate kill switches: check if THIS trade's segment is blocked
            _sym_upper = symbol.upper()
            _is_fno = (
                _sym_upper.endswith('FUT') or
                bool(re.search(r'\d+CE$', _sym_upper)) or
                bool(re.search(r'\d+PE$', _sym_upper)) or
                'GOLDM' in _sym_upper or
                'SILVERM' in _sym_upper
            )
            if _is_fno and self._loss_hit_fno:
                audit('fm', 'REJECT', symbol, strategy=strategy, reason='fno_loss_limit')
                return False, 0, ''
            if not _is_fno and self._loss_hit_equity:
                audit('fm', 'REJECT', symbol, strategy=strategy, reason='equity_loss_limit')
                return False, 0, ''

            if len(self._deployed) >= MAX_TOTAL:
                audit('fm', 'REJECT', symbol, strategy=strategy, reason='max_total')
                return False, 0, ''

            active = sum(1 for d in self._deployed.values() if d['strategy'] == strategy)
            if active >= MAX_PER_STRATEGY:
                audit('fm', 'REJECT', symbol, strategy=strategy, reason='max_strategy')
                return False, 0, ''

            buffer = self.pool * 0.10
            deployed = sum(d['amount'] for d in self._deployed.values())
            avail = self.pool - deployed - buffer
            if avail < amount:
                audit('fm', 'REJECT', symbol, strategy=strategy,
                      reason=f'no_funds need={amount:.0f} avail={avail:.0f}')
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
            # Separate equity vs F&O PnL tracking
            _sym_upper = entry.get('sym', '').upper()
            _is_fno = (
                _sym_upper.endswith('FUT') or
                bool(re.search(r'\d+CE$', _sym_upper)) or
                bool(re.search(r'\d+PE$', _sym_upper)) or
                'GOLDM' in _sym_upper or
                'SILVERM' in _sym_upper
            )
            if _is_fno:
                self._daily_pnl_fno += pnl
                if self._daily_pnl_fno <= -self.daily_loss_limit:
                    self._loss_hit_fno = True
                    log.error(f'FnO LOSS LIMIT: {self._daily_pnl_fno:.0f}')
            else:
                self._daily_pnl_equity += pnl
                if self._daily_pnl_equity <= -self.daily_loss_limit:
                    self._loss_hit_equity = True
                    log.error(f'EQUITY LOSS LIMIT: {self._daily_pnl_equity:.0f}')
        audit('fm', 'RELEASE', entry['sym'], trade_id=trade_id, pnl=round(pnl, 2))

    def status(self):
        with self._lock:
            buffer = self.pool * 0.10
            deployed = sum(d['amount'] for d in self._deployed.values())
            return {
                'pool': self.pool, 'deployed': round(deployed),
                'available': round(self.pool - deployed - buffer),
                'daily_pnl_equity': round(self._daily_pnl_equity, 2),
                'daily_pnl_fno': round(self._daily_pnl_fno, 2),
                'daily_pnl': round(self._daily_pnl_equity + self._daily_pnl_fno, 2),
                'loss_hit_equity': self._loss_hit_equity,
                'loss_hit_fno': self._loss_hit_fno,
                'active': len(self._deployed),
            }

    def reset_daily(self):
        with self._lock:
            self._daily_pnl_equity = 0
            self._daily_pnl_fno = 0
            self._loss_hit_equity = False
            self._loss_hit_fno = False
            # Don't clear _deployed — other agents may have overnight positions
        try:
            import sys, os
            sys.path.insert(0, os.path.expanduser('~/news-trading'))
            from live.indmoney_client import get_funds
            bal = get_funds()
            if bal and bal > 0:
                self.pool = bal
        except Exception:
            pass
        audit('fm', 'DAILY_RESET', '', pool=self.pool)
