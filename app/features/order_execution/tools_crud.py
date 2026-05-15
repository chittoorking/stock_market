"""
Order execution CRUD tools — the agent's hands for trading.
Create/Update/Delete positions through the broker.
Risk check hook runs BEFORE every trade.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from crewai_tools import tool

logger = logging.getLogger(__name__)


def create_order_execution_tools(session_id: str):
    """Factory: create order execution tools scoped to this session."""

    @tool("place_order")
    async def place_order(
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> str:
        """
        Place a trade order with the broker.
        IMPORTANT: This tool actually executes a trade. Risk checks run automatically.

        symbol: Stock/crypto symbol
        side: "BUY" or "SELL"
        quantity: Number of shares/units
        order_type: "MARKET", "LIMIT", "STOP_LOSS", "STOP_LIMIT"
        price: Required for LIMIT orders
        stop_loss: Stop loss price (auto-calculated if not provided)
        take_profit: Take profit price (auto-calculated if not provided)
        """
        from app.services.risk_service import risk_service
        from app.services.broker_service import broker_service
        from app.core.conversation_state import transition_state, TradingState
        from app.core.agui_events.emit import emit_trade_executed, emit_risk_alert

        # ─── RISK CHECK HOOK (before_tool_call equivalent) ───
        risk_result = await risk_service.check_before_trade(
            session_id=session_id,
            symbol=symbol,
            side=side.upper(),
            quantity=quantity,
            price=price,
        )

        if not risk_result["approved"]:
            await emit_risk_alert(session_id, {
                "event_type": risk_result.get("violation", "POSITION_SIZE_LIMIT"),
                "symbol": symbol,
                "details": risk_result["reason"],
                "severity": "high",
            })
            return f"🚫 **Trade Rejected by Risk Manager**\n{risk_result['reason']}"

        # ─── Transition state ───
        await transition_state(
            session_id, TradingState.ORDER_PLACING,
            reason=f"placing_{side}_{symbol}",
        )

        try:
            # Execute via broker
            trade_id = str(uuid.uuid4())[:12]
            broker_result = await broker_service.place_order(
                symbol=symbol,
                side=side.upper(),
                quantity=quantity,
                order_type=order_type.upper(),
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

            if broker_result.get("status") == "REJECTED":
                await transition_state(session_id, TradingState.ERROR, reason="order_rejected")
                return f"❌ Order rejected by broker: {broker_result.get('reason', 'Unknown')}"

            # ─── After-tool hook: update state and emit ───
            trade_data = {
                "id": trade_id,
                "symbol": symbol,
                "side": side.upper(),
                "quantity": quantity,
                "order_type": order_type.upper(),
                "entry_price": broker_result.get("fill_price", price or 0),
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "status": broker_result.get("status", "FILLED"),
                "broker_order_id": broker_result.get("order_id", ""),
            }

            await emit_trade_executed(session_id, trade_data)

            # Update risk state
            await risk_service.record_trade(session_id, trade_data)

            # Transition to appropriate state
            if broker_result.get("status") == "FILLED":
                await transition_state(
                    session_id, TradingState.POSITION_OPEN,
                    reason=f"order_filled_{symbol}",
                    metadata={"trade_id": trade_id, "symbol": symbol},
                )
            else:
                await transition_state(
                    session_id, TradingState.ORDER_PLACED,
                    reason=f"order_placed_{symbol}",
                )

            # Persist to DB
            from app.core.database import async_session_factory, Trade, OrderSide, OrderType as OT, OrderStatus
            async with async_session_factory() as db:
                db_trade = Trade(
                    id=trade_id,
                    session_id=session_id,
                    symbol=symbol,
                    side=side.upper(),
                    order_type=order_type.upper(),
                    quantity=quantity,
                    entry_price=broker_result.get("fill_price"),
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    status=broker_result.get("status", "FILLED"),
                    broker_order_id=broker_result.get("order_id"),
                )
                db.add(db_trade)
                await db.commit()

            emoji = "📈" if side.upper() == "BUY" else "📉"
            return (
                f"{emoji} **Order Executed**\n"
                f"Symbol: {symbol}\n"
                f"Side: {side.upper()} | Qty: {quantity}\n"
                f"Price: ₹{broker_result.get('fill_price', 0):.2f}\n"
                f"Stop Loss: {f'₹{stop_loss:.2f}' if stop_loss else 'Not set'}\n"
                f"Take Profit: {f'₹{take_profit:.2f}' if take_profit else 'Not set'}\n"
                f"Status: {broker_result.get('status', 'FILLED')}\n"
                f"Order ID: {broker_result.get('order_id', trade_id)}"
            )

        except Exception as e:
            logger.error("Order execution failed: %s", e)
            await transition_state(session_id, TradingState.ERROR, reason=f"order_error: {e}")
            return f"❌ Order execution failed: {str(e)}"

    @tool("update_position")
    async def update_position(
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        add_quantity: Optional[float] = None,
    ) -> str:
        """
        Modify an open position — update stop loss, take profit, or add to position.
        symbol: The symbol to modify
        stop_loss: New stop loss price
        take_profit: New take profit price
        add_quantity: Additional quantity to add (averages entry price)
        """
        from app.services.broker_service import broker_service
        from app.core.conversation_state import transition_state, TradingState
        from app.core.agui_events.emit import emit_position_update

        await transition_state(session_id, TradingState.POSITION_MODIFYING, reason=f"modifying_{symbol}")

        try:
            result = await broker_service.modify_position(
                symbol=symbol,
                stop_loss=stop_loss,
                take_profit=take_profit,
                add_quantity=add_quantity,
            )

            await emit_position_update(session_id, result)
            await transition_state(session_id, TradingState.POSITION_OPEN, reason=f"modified_{symbol}")

            lines = [f"✏️ **Position Updated: {symbol}**"]
            if stop_loss:
                lines.append(f"Stop Loss → ₹{stop_loss:.2f}")
            if take_profit:
                lines.append(f"Take Profit → ₹{take_profit:.2f}")
            if add_quantity:
                lines.append(f"Added {add_quantity} units (new avg: ₹{result.get('avg_entry_price', 0):.2f})")

            return "\n".join(lines)

        except Exception as e:
            logger.error("Position modification failed: %s", e)
            await transition_state(session_id, TradingState.POSITION_OPEN, reason="modification_failed")
            return f"❌ Failed to modify position: {str(e)}"

    @tool("close_position")
    async def close_position(
        symbol: str,
        quantity: Optional[float] = None,
        reason: str = "manual_close",
    ) -> str:
        """
        Close an open position (full or partial).
        symbol: The symbol to close
        quantity: Quantity to close (None = close all)
        reason: Why closing ("manual_close", "stop_loss", "take_profit", "risk_limit")
        """
        from app.services.broker_service import broker_service
        from app.services.risk_service import risk_service
        from app.core.conversation_state import transition_state, TradingState
        from app.core.agui_events.emit import emit_trade_executed

        await transition_state(session_id, TradingState.CLOSING_POSITION, reason=f"closing_{symbol}")

        try:
            result = await broker_service.close_position(
                symbol=symbol,
                quantity=quantity,
            )

            pnl = result.get("pnl", 0)
            pnl_emoji = "✅" if pnl >= 0 else "❌"

            trade_data = {
                "id": result.get("trade_id", ""),
                "symbol": symbol,
                "side": "SELL",  # Closing a long = sell
                "quantity": quantity or result.get("quantity", 0),
                "entry_price": result.get("exit_price", 0),
                "status": "FILLED",
            }
            await emit_trade_executed(session_id, trade_data)

            # Record P&L in risk state
            await risk_service.record_close(session_id, symbol, pnl)

            # Transition state
            if result.get("remaining_quantity", 0) > 0:
                await transition_state(session_id, TradingState.POSITION_OPEN, reason="partial_close")
            else:
                await transition_state(session_id, TradingState.POSITION_CLOSED, reason=reason)

            return (
                f"{pnl_emoji} **Position Closed: {symbol}**\n"
                f"Exit Price: ₹{result.get('exit_price', 0):.2f}\n"
                f"P&L: ₹{pnl:+,.2f} ({result.get('pnl_pct', 0):+.2f}%)\n"
                f"Duration: {result.get('duration', 'N/A')}\n"
                f"Reason: {reason.replace('_', ' ').title()}"
            )

        except Exception as e:
            logger.error("Position close failed: %s", e)
            await transition_state(session_id, TradingState.POSITION_OPEN, reason="close_failed")
            return f"❌ Failed to close position: {str(e)}"

    @tool("check_order_status")
    async def check_order_status(order_id: str = "") -> str:
        """
        Check the status of a pending order.
        order_id: Broker order ID (if empty, shows all pending orders)
        """
        from app.services.broker_service import broker_service

        try:
            if order_id:
                status = await broker_service.get_order_status(order_id)
                return (
                    f"📋 Order {order_id}\n"
                    f"Status: {status.get('status', 'UNKNOWN')}\n"
                    f"Filled: {status.get('filled_quantity', 0)}/{status.get('quantity', 0)}\n"
                    f"Price: ₹{status.get('fill_price', 0):.2f}"
                )
            else:
                orders = await broker_service.get_pending_orders()
                if not orders:
                    return "No pending orders."
                lines = ["📋 **Pending Orders:**"]
                for o in orders:
                    lines.append(
                        f"  {o['symbol']} {o['side']} {o['quantity']} "
                        f"@ ₹{o.get('price', 'MKT')} — {o['status']}"
                    )
                return "\n".join(lines)

        except Exception as e:
            return f"Failed to check order status: {str(e)}"

    @tool("cancel_order")
    async def cancel_order(order_id: str) -> str:
        """Cancel a pending order."""
        from app.services.broker_service import broker_service
        from app.core.conversation_state import transition_state, TradingState

        try:
            result = await broker_service.cancel_order(order_id)
            await transition_state(session_id, TradingState.IDLE, reason="order_cancelled")
            return f"✅ Order {order_id} cancelled successfully."
        except Exception as e:
            return f"❌ Failed to cancel order: {str(e)}"

    return [place_order, update_position, close_position, check_order_status, cancel_order]
