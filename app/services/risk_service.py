"""
Risk service — the non-negotiable safety layer.
Runs before every trade (after_tool_call hook pattern).
"""
from __future__ import annotations

import logging
from typing import Optional, Dict, Any

from app.core.config import settings
from app.core.session_context import get_risk_state, update_risk_state, get_trader_preferences

logger = logging.getLogger(__name__)


class RiskService:
    """
    Risk management engine.
    Every trade MUST pass through check_before_trade() — no exceptions.
    """

    async def check_before_trade(
        self,
        session_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Pre-trade risk check. Returns {"approved": bool, "reason": str, "violation": str|None}
        This is the main safety gate — called by place_order tool before execution.
        """
        risk_state = await get_risk_state(session_id)
        prefs = await get_trader_preferences(session_id)

        # Get estimated price
        if not price:
            from app.services.market_data_service import market_data_service
            live = await market_data_service.get_live_price(symbol)
            price = live.get("price", 0)

        if not price:
            return {"approved": False, "reason": "Cannot determine price for risk calculation", "violation": "UNKNOWN"}

        trade_value = price * quantity

        # ─── Check 1: Position size limit ───
        max_size = prefs.get("max_position_size", settings.MAX_POSITION_SIZE)
        if trade_value > max_size:
            return {
                "approved": False,
                "reason": f"Trade value ₹{trade_value:,.2f} exceeds max position size ₹{max_size:,.2f}",
                "violation": "POSITION_SIZE_LIMIT",
            }

        # ─── Check 2: Portfolio exposure limit ───
        current_exposure = risk_state.get("total_exposure", 0)
        new_exposure = current_exposure + trade_value
        if new_exposure > settings.MAX_PORTFOLIO_EXPOSURE:
            return {
                "approved": False,
                "reason": (
                    f"New exposure ₹{new_exposure:,.2f} would exceed limit "
                    f"₹{settings.MAX_PORTFOLIO_EXPOSURE:,.2f}"
                ),
                "violation": "EXPOSURE_LIMIT",
            }

        # ─── Check 3: Daily loss limit ───
        daily_pnl = risk_state.get("daily_pnl", 0)
        if daily_pnl < -settings.MAX_DAILY_LOSS:
            return {
                "approved": False,
                "reason": (
                    f"Daily loss ₹{abs(daily_pnl):,.2f} exceeds limit "
                    f"₹{settings.MAX_DAILY_LOSS:,.2f}. Trading halted."
                ),
                "violation": "DAILY_LOSS_LIMIT",
            }

        # ─── Check 4: Max open positions ───
        open_count = risk_state.get("open_positions_count", 0)
        if open_count >= settings.MAX_OPEN_POSITIONS:
            return {
                "approved": False,
                "reason": f"Maximum {settings.MAX_OPEN_POSITIONS} positions already open.",
                "violation": "POSITION_SIZE_LIMIT",
            }

        # ─── Check 5: Leverage limit ───
        max_leverage = prefs.get("max_leverage", settings.MAX_LEVERAGE)
        # Simplified leverage check
        account_value = 1_000_000  # Would be fetched from broker
        leverage = new_exposure / account_value if account_value else 0
        if leverage > max_leverage:
            return {
                "approved": False,
                "reason": f"Leverage {leverage:.1f}x would exceed limit {max_leverage}x",
                "violation": "EXPOSURE_LIMIT",
            }

        logger.info(
            "Risk check APPROVED: %s %s %s (value=₹%.2f, exposure=₹%.2f)",
            side, quantity, symbol, trade_value, new_exposure,
        )

        return {"approved": True, "reason": "All risk checks passed"}

    async def record_trade(self, session_id: str, trade_data: Dict[str, Any]):
        """Update risk state after a trade is executed."""
        risk_state = await get_risk_state(session_id)

        trade_value = trade_data.get("entry_price", 0) * trade_data.get("quantity", 0)

        risk_state["total_exposure"] = risk_state.get("total_exposure", 0) + trade_value
        risk_state["open_positions_count"] = risk_state.get("open_positions_count", 0) + 1
        risk_state["daily_trades_count"] = risk_state.get("daily_trades_count", 0) + 1

        await update_risk_state(session_id, risk_state)

    async def record_close(self, session_id: str, symbol: str, pnl: float):
        """Update risk state after a position is closed."""
        risk_state = await get_risk_state(session_id)

        risk_state["daily_pnl"] = risk_state.get("daily_pnl", 0) + pnl
        risk_state["open_positions_count"] = max(0, risk_state.get("open_positions_count", 0) - 1)

        # Track max drawdown
        if pnl < 0:
            risk_state["max_drawdown_today"] = min(
                risk_state.get("max_drawdown_today", 0),
                risk_state["daily_pnl"],
            )

        await update_risk_state(session_id, risk_state)

    async def get_risk_summary(self, session_id: str) -> Dict[str, Any]:
        """Get full risk summary for display."""
        risk_state = await get_risk_state(session_id)
        return {
            "daily_pnl": risk_state.get("daily_pnl", 0),
            "total_exposure": risk_state.get("total_exposure", 0),
            "open_positions_count": risk_state.get("open_positions_count", 0),
            "daily_trades_count": risk_state.get("daily_trades_count", 0),
            "max_drawdown_today": risk_state.get("max_drawdown_today", 0),
            "limits": {
                "max_position_size": settings.MAX_POSITION_SIZE,
                "max_exposure": settings.MAX_PORTFOLIO_EXPOSURE,
                "max_daily_loss": settings.MAX_DAILY_LOSS,
                "max_positions": settings.MAX_OPEN_POSITIONS,
                "max_leverage": settings.MAX_LEVERAGE,
            },
        }

    async def check_stop_loss_take_profit(self, session_id: str) -> list:
        """
        Background monitoring — check if any open positions hit SL/TP.
        Returns list of actions to take.
        """
        from app.services.broker_service import broker_service
        from app.services.market_data_service import market_data_service

        actions = []
        positions = await broker_service.get_positions()

        for pos in positions:
            symbol = pos.get("symbol")
            sl = pos.get("stop_loss")
            tp = pos.get("take_profit")

            if not (sl or tp):
                continue

            live = await market_data_service.get_live_price(symbol)
            price = live.get("price", 0)

            if not price:
                continue

            if pos.get("side") == "BUY":
                if sl and price <= sl:
                    actions.append({"symbol": symbol, "action": "close", "reason": "stop_loss_hit", "price": price})
                elif tp and price >= tp:
                    actions.append({"symbol": symbol, "action": "close", "reason": "take_profit_hit", "price": price})
            elif pos.get("side") == "SELL":
                if sl and price >= sl:
                    actions.append({"symbol": symbol, "action": "close", "reason": "stop_loss_hit", "price": price})
                elif tp and price <= tp:
                    actions.append({"symbol": symbol, "action": "close", "reason": "take_profit_hit", "price": price})

        return actions


# Singleton
risk_service = RiskService()
