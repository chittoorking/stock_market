"""Shared capital pool — all sessions draw from the same Rs pool.

Prevents over-deployment when multiple sessions are active simultaneously.

Usage:
    pool = CapitalPool(total=200000, leverage=5)  # Rs 2L own = Rs 10L deployed

    # S1 at 9:15
    alloc = pool.request('S1', 10, 100000)  # 10 stocks, Rs 1L each
    # ... trade ...
    pool.release('S1', realized_pnl)

    # S4 at 10:15 — automatically gets whatever S1 hasn't freed yet
    alloc = pool.request('S4', 10, 100000)
"""
import json, time, logging
from pathlib import Path

log = logging.getLogger('capital_pool')

STATE_FILE = Path(__file__).parent.parent / 'data' / 'capital_state.json'


class CapitalPool:
    def __init__(self, total_own: int, leverage: int = 5):
        self.total_own = total_own
        self.leverage = leverage
        self.total_deployed = total_own * leverage   # Rs 10L if 2L own
        self._sessions = {}   # session_name → deployed_amount
        self._load()

    def _load(self):
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                # Only reload today's state
                import datetime
                today = datetime.date.today().isoformat()
                if data.get('date') == today:
                    self._sessions = data.get('sessions', {})
                    log.info(f'Capital pool loaded: {self._sessions}')
                    return
            except Exception:
                pass
        self._sessions = {}
        self._save()

    def _save(self):
        import datetime
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({
            'date': datetime.date.today().isoformat(),
            'total_own': self.total_own,
            'total_deployed': self.total_deployed,
            'sessions': self._sessions,
        }, indent=2))

    @property
    def deployed(self) -> int:
        return sum(self._sessions.values())

    @property
    def available(self) -> int:
        return max(0, self.total_deployed - self.deployed)

    def request(self, session: str, n_stocks: int, per_stock: int) -> int:
        """Request capital for a session. Returns actual per-stock allocation."""
        wanted = n_stocks * per_stock
        avail = self.available
        if avail < per_stock:
            log.warning(f'{session}: only Rs {avail:,} available, need Rs {per_stock:,}/stock — SKIP')
            return 0
        # Cap to available, reduce stocks if needed
        actual = min(wanted, avail)
        per_stock_actual = actual // n_stocks
        if per_stock_actual < per_stock * 0.5:
            log.warning(f'{session}: capital reduced to Rs {per_stock_actual:,}/stock (was Rs {per_stock:,})')
        self._sessions[session] = actual
        self._save()
        log.info(f'{session}: allocated Rs {actual:,} ({n_stocks}×Rs {per_stock_actual:,}) | pool used: Rs {self.deployed:,}/Rs {self.total_deployed:,}')
        return per_stock_actual

    def release(self, session: str, realized_pnl: float = 0):
        """Release capital back to pool after session exits."""
        freed = self._sessions.pop(session, 0)
        # Add realized P&L back to own capital (compounds over time)
        if realized_pnl > 0:
            self.total_own += int(realized_pnl)
            self.total_deployed = self.total_own * self.leverage
        self._save()
        log.info(f'{session}: released Rs {freed:,}, P&L={realized_pnl:+,.0f} | pool free: Rs {self.available:,}')

    def status(self) -> str:
        lines = [f'Capital Pool: Rs {self.total_own:,} own × {self.leverage}x = Rs {self.total_deployed:,} deployed']
        for s, v in self._sessions.items():
            lines.append(f'  {s}: Rs {v:,} active')
        lines.append(f'  Available: Rs {self.available:,}')
        return '\n'.join(lines)
