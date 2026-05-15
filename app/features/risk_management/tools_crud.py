"""
Risk management CRUD tools — the safety net.
These tools enforce limits and provide risk visibility.
"""
from __future__ import annotations

import logging
from typing import Optional

from crewai_tools import tool

logger = logging.getLogger(__name__)


def create_risk_management_tools(session_id: str):
    """Factory: create risk management tools scoped to this session."""

    @tool("check_risk_limits")
    async def check_risk_limits(
        symbol: Optional[str] = None,
        proposed_size: Optional[float] = None,
    ) -> str:
        """
        Check current risk limits and exposure.
        symbol: Optional — check risk for a specific proposed trade
        proposed_size: Optional — proposed position size to validate
        """
        from app.services.risk_service import risk_service
        from app.core.config import settings

        try:
            risk_state = await risk_service.get_risk_summary(session_id)

            lines = ["🛡️ **Risk Dashboard**\n"]

            # Daily P&L
            daily_pnl = risk_state.get("daily_pnl", 0)
            daily_limit = settings.MAX_DAILY_LOSS
            daily_pct = abs(daily_pnl) / daily_limit * 100 if daily_limit else 0
            lines.append(f"Daily P&L: ₹{daily_pnl:+,.2f} / ₹{daily_limit:,.0f} limit ({daily_pct:.0f}% used)")

            # Exposure
            exposure = risk_state.get("total_exposure", 0)
            max_exposure = settings.MAX_PORTFOLIO_EXPOSURE
            exp_pct = exposure / max_exposure * 100 if max_exposure else 0
            lines.append(f"Exposure: ₹{exposure:,.2f} / ₹{max_exposure:,.0f} ({exp_pct:.0f}%)")

            # Open positions
            open_pos = risk_state.get("open_positions_count", 0)
            max_pos = settings.MAX_OPEN_POSITIONS
            lines.append(f"Open Positions: {open_pos} / {max_pos}")

            # Per-symbol check
            if symbol and proposed_size:
                result = await risk_service.check_before_trade(
                    session_id=session_id,
                    symbol=symbol,
                    side="BUY",
                    quantity=proposed_size,
                )
                if result["approved"]:
                    lines.append(f"\n✅ Trade of {proposed_size} {symbol} is within limits.")
                else:
                    lines.append(f"\n🚫 Trade blocked: {result['reason']}")

            # Warnings
            if daily_pct > 80:
                lines.append("\n⚠️ WARNING: Approaching daily loss limit!")
            if exp_pct > 80:
                lines.append("\n⚠️ WARNING: Approaching exposure limit!")
            if open_pos >= max_pos:
                lines.append("\n⚠️ WARNING: Maximum positions reached!")

            return "\n".join(lines)

        except Exception as e:
            logger.error("Risk check failed: %s", e)
            return f"Risk check failed: {str(e)}"

    @tool("set_risk_params")
    async def set_risk_params(
        max_position_size: Optional[float] = None,
        max_daily_loss: Optional[float] = None,
        default_stop_loss_pct: Optional[float] = None,
        default_take_profit_pct: Optional[float] = None,
        max_leverage: Optional[float] = None,
    ) -> str:
        """
        Update risk parameters for this session.
        These override the defaults from config.
        """
        from app.core.session_context import get_trader_preferences, set_trader_preferences

        try:
            prefs = await get_trader_preferences(session_id)

            updates = []
            if max_position_size is not None:
                prefs["max_position_size"] = max_position_size
                updates.append(f"Max Position Size → ₹{max_position_size:,.0f}")
            if max_daily_loss is not None:
                prefs["max_daily_loss"] = max_daily_loss
                updates.append(f"Max Daily Loss → ₹{max_daily_loss:,.0f}")
            if default_stop_loss_pct is not None:
                prefs["default_stop_loss_pct"] = default_stop_loss_pct
                updates.append(f"Default SL → {default_stop_loss_pct}%")
            if default_take_profit_pct is not None:
                prefs["default_take_profit_pct"] = default_take_profit_pct
                updates.append(f"Default TP → {default_take_profit_pct}%")
            if max_leverage is not None:
                prefs["max_leverage"] = max_leverage
                updates.append(f"Max Leverage → {max_leverage}x")

            await set_trader_preferences(session_id, prefs)

            if updates:
                return "🛡️ **Risk Parameters Updated:**\n" + "\n".join(f"  ✓ {u}" for u in updates)
            return "No changes specified."

        except Exception as e:
            return f"Failed to update risk params: {str(e)}"

    return [check_risk_limits, set_risk_params]
