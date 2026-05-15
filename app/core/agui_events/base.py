"""
AGUI event types for the trading dashboard.
Typed event definitions for real-time dashboard updates.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone


@dataclass
class BaseEvent:
    """Base event that all trading events inherit from."""
    event_type: str
    session_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TextMessageEvent(BaseEvent):
    """Plain text message from the AI agent."""
    event_type: str = "text_message"
    content: str = ""
    role: str = "assistant"


@dataclass
class QuickRepliesEvent(BaseEvent):
    """Quick reply buttons for the trader."""
    event_type: str = "quick_replies"
    replies: List[Dict[str, str]] = field(default_factory=list)
    # Each reply: {"label": "Buy RELIANCE", "action": "confirm_buy_RELIANCE"}


@dataclass
class SignalEvent(BaseEvent):
    """Trading signal detected."""
    event_type: str = "signal"
    symbol: str = ""
    strength: str = "NEUTRAL"
    confidence: float = 0.5
    entry_suggested: Optional[float] = None
    sl_suggested: Optional[float] = None
    tp_suggested: Optional[float] = None
    reason: str = ""
    indicator: str = ""
    timeframe: str = ""


@dataclass
class TradeExecutedEvent(BaseEvent):
    """Trade has been executed."""
    event_type: str = "trade_executed"
    trade_id: str = ""
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    price: float = 0.0
    order_type: str = "MARKET"
    status: str = "FILLED"
    broker_order_id: str = ""


@dataclass
class PositionUpdateEvent(BaseEvent):
    """Position data has changed."""
    event_type: str = "position_update"
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    avg_entry: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


@dataclass
class PortfolioEvent(BaseEvent):
    """Full portfolio snapshot."""
    event_type: str = "portfolio"
    positions: List[Dict[str, Any]] = field(default_factory=list)
    total_pnl: float = 0.0
    daily_pnl: float = 0.0
    total_exposure: float = 0.0
    cash_balance: float = 0.0


@dataclass
class MarketDataEvent(BaseEvent):
    """Real-time market data update."""
    event_type: str = "market_data"
    symbol: str = ""
    price: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0


@dataclass
class TechnicalAnalysisEvent(BaseEvent):
    """Technical analysis results."""
    event_type: str = "technical_analysis"
    symbol: str = ""
    timeframe: str = ""
    indicators: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    recommendation: str = "NEUTRAL"


@dataclass
class RiskAlertEvent(BaseEvent):
    """Risk limit triggered."""
    event_type: str = "risk_alert"
    alert_type: str = ""
    symbol: str = ""
    details: str = ""
    severity: str = "high"  # "low", "medium", "high", "critical"
    action_required: bool = True


@dataclass
class BacktestResultEvent(BaseEvent):
    """Backtest completed with results."""
    event_type: str = "backtest_result"
    strategy: str = ""
    symbol: str = ""
    timeframe: str = ""
    total_return: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    total_trades: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TradeReviewEvent(BaseEvent):
    """Post-trade review result."""
    event_type: str = "trade_review"
    trade_id: str = ""
    symbol: str = ""
    pnl: float = 0.0
    entry_reason: str = ""
    exit_reason: str = ""
    lesson: str = ""
    rating: int = 0


@dataclass
class ActivityEvent(BaseEvent):
    """Loading/activity indicator."""
    event_type: str = "activity"
    activity: str = ""  # "analyzing", "placing_order", "scanning"
    is_active: bool = True


@dataclass
class ErrorEvent(BaseEvent):
    """Error event."""
    event_type: str = "error"
    error_code: str = ""
    message: str = ""
    recoverable: bool = True
