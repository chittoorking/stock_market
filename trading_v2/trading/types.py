"""Core types — shared across all modules.

Every data structure used by more than one module lives here.
No business logic. Just shapes.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = 'BUY'
    SELL = 'SELL'


class OrderType(str, Enum):
    MARKET = 'MARKET'
    LIMIT = 'LIMIT'


class ExitReason(str, Enum):
    SL_HIT = 'SL_HIT'
    TARGET_HIT = 'TARGET_HIT'
    TRAIL_EXIT = 'TRAIL_EXIT'
    EOD_EXIT = 'EOD_EXIT'
    SIGNAL_EXIT = 'SIGNAL_EXIT'
    MANUAL = 'MANUAL'
    FM_REJECTED = 'FM_REJECTED'
    ORDER_FAILED = 'ORDER_FAILED'


@dataclass
class TradeRequest:
    """Agent submits this to request a trade."""
    symbol: str
    side: Side
    entry: float = 0           # 0 = market price
    sl: float = 0              # stop loss price
    target: float = 0          # target price
    trail_activate_pct: float = 0
    trail_pct: float = 0
    projection: float = 0     # expected move %
    conviction: int = 5       # 1-10
    reason: str = ''
    order_type: OrderType = OrderType.MARKET
    is_fno: bool = False
    fno_lot_size: int = 0
    fno_margin: float = 0
    fno_sec_id: str = ''


@dataclass
class Position:
    """A confirmed open position. Only created after broker confirms order."""
    id: str
    symbol: str
    side: Side
    qty: int
    entry_price: float
    order_id: str              # broker order ID — REQUIRED
    strategy: str
    sl: float = 0
    target: float = 0
    trail_activate_pct: float = 0
    trail_pct: float = 0
    peak_price: float = 0
    trail_active: bool = False
    fno_lot_size: int = 0
    fno_margin: float = 0
    fno_sec_id: str = ''
    trade_id: str = ''         # fund manager ID
    opened_at: str = field(default_factory=lambda: datetime.now().strftime('%H:%M:%S'))
    # Exit fields (set on close)
    closed: bool = False
    exit_price: float = 0
    exit_reason: str = ''
    pnl: float = 0
    pnl_pct: float = 0


@dataclass
class Signal:
    """Incoming signal from external source (notification, scan, etc)."""
    source: str                # 'indmoney', 'multyfi', 'gemini', etc
    symbol: str = ''
    entry: float = 0
    sl: float = 0
    target: float = 0
    side: str = 'BUY'
    text: str = ''             # raw notification text
    is_exit: bool = False      # True if this is a close signal
    projection: float = 0
    conviction: int = 5
    metadata: dict = field(default_factory=dict)
