"""
Portfolio REST endpoints — for dashboard and mobile app.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter

from app.services.broker_service import broker_service
from app.services.risk_service import risk_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/positions")
async def get_positions():
    """Get all open positions."""
    positions = await broker_service.get_positions()
    return {"positions": positions, "count": len(positions)}


@router.get("/balance")
async def get_balance():
    """Get account balance."""
    balance = await broker_service.get_balance()
    return balance


@router.get("/risk/{session_id}")
async def get_risk_status(session_id: str):
    """Get risk status for a session."""
    summary = await risk_service.get_risk_summary(session_id)
    return summary


@router.get("/history/{session_id}")
async def get_trade_history(session_id: str, limit: int = 50):
    """Get trade history for a session."""
    from app.core.database import async_session_factory, Trade
    from sqlalchemy import select

    async with async_session_factory() as db:
        result = await db.execute(
            select(Trade)
            .where(Trade.session_id == session_id)
            .order_by(Trade.created_at.desc())
            .limit(limit)
        )
        trades = result.scalars().all()

        return {
            "trades": [
                {
                    "id": t.id,
                    "symbol": t.symbol,
                    "side": t.side,
                    "quantity": t.quantity,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "pnl": t.pnl,
                    "status": t.status,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in trades
            ],
            "count": len(trades),
        }
