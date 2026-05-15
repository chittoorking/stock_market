"""
SQLAlchemy async database setup — trades, signals, positions, risk events.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import AsyncGenerator
from enum import Enum as PyEnum

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import (
    String, Float, Integer, DateTime, Text, Boolean,
    ForeignKey, Enum, JSON, Index,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    echo=settings.DEBUG,
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ─── Enums ───

class OrderSide(str, PyEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, PyEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, PyEnum):
    PENDING = "PENDING"
    PLACED = "PLACED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class PositionStatus(str, PyEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"


class SignalStrength(str, PyEnum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class RiskEventType(str, PyEnum):
    STOP_LOSS_HIT = "STOP_LOSS_HIT"
    TAKE_PROFIT_HIT = "TAKE_PROFIT_HIT"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    POSITION_SIZE_LIMIT = "POSITION_SIZE_LIMIT"
    EXPOSURE_LIMIT = "EXPOSURE_LIMIT"
    MARGIN_CALL = "MARGIN_CALL"


# ─── Models ───

class TradingSession(Base):
    """Tracks trader sessions — connection metadata, timestamps, active state."""
    __tablename__ = "trading_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trader_id: Mapped[str] = mapped_column(String(64), index=True)
    broker: Mapped[str] = mapped_column(String(32), default="paper")
    state: Mapped[str] = mapped_column(String(32), default="IDLE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    trades: Mapped[list["Trade"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class Trade(Base):
    """Individual trade record — the core entity."""
    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_session_symbol", "session_id", "symbol"),
        Index("ix_trades_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("trading_sessions.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(Enum(OrderSide))
    order_type: Mapped[str] = mapped_column(Enum(OrderType), default=OrderType.MARKET)
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(Enum(OrderStatus), default=OrderStatus.PENDING)
    pnl: Mapped[float] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float] = mapped_column(Float, nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    broker_order_id: Mapped[str] = mapped_column(String(128), nullable=True)
    signal_id: Mapped[str] = mapped_column(String(64), ForeignKey("signals.id"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    session: Mapped["TradingSession"] = relationship(back_populates="trades")
    signal: Mapped["Signal"] = relationship(back_populates="trades")


class Position(Base):
    """Aggregated position per symbol — derived from trades."""
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(Enum(OrderSide))
    quantity: Mapped[float] = mapped_column(Float)
    avg_entry_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[float] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(Enum(PositionStatus), default=PositionStatus.OPEN)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class Signal(Base):
    """Trading signal — generated by analysis or external webhook."""
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_symbol_strength", "symbol", "strength"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    strength: Mapped[str] = mapped_column(Enum(SignalStrength))
    source: Mapped[str] = mapped_column(String(64))  # "technical", "sentiment", "external", "agent"
    indicator: Mapped[str] = mapped_column(String(64), nullable=True)  # "RSI", "MACD", "news"
    timeframe: Mapped[str] = mapped_column(String(16), nullable=True)  # "1m", "5m", "1h", "1d"
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    entry_suggested: Mapped[float] = mapped_column(Float, nullable=True)
    sl_suggested: Mapped[float] = mapped_column(Float, nullable=True)
    tp_suggested: Mapped[float] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    acted_on: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=True)  # "profit", "loss", "expired"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    trades: Mapped[list["Trade"]] = relationship(back_populates="signal")


class RiskEvent(Base):
    """Risk limit violations and safety triggers."""
    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(Enum(RiskEventType))
    symbol: Mapped[str] = mapped_column(String(32), nullable=True)
    details: Mapped[str] = mapped_column(Text)
    action_taken: Mapped[str] = mapped_column(String(64))  # "position_closed", "order_rejected", "alert_sent"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TradeReview(Base):
    """Post-trade AI review for self-improvement loop."""
    __tablename__ = "trade_reviews"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(64), ForeignKey("trades.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    entry_reason: Mapped[str] = mapped_column(Text)
    exit_reason: Mapped[str] = mapped_column(Text, nullable=True)
    what_went_right: Mapped[str] = mapped_column(Text, nullable=True)
    what_went_wrong: Mapped[str] = mapped_column(Text, nullable=True)
    lesson_learned: Mapped[str] = mapped_column(Text, nullable=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=True)  # 1-5 self-rating
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ─── Session helper ───

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """Create all tables on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified")


async def close_db():
    """Dispose engine on shutdown."""
    await engine.dispose()
    logger.info("Database engine disposed")
