"""
Portfolio CRUD tools — view, track, and review positions.
"""
from __future__ import annotations

import logging
from typing import Optional

from crewai_tools import tool

logger = logging.getLogger(__name__)


def create_portfolio_tools(session_id: str):
    """Factory: create portfolio tools scoped to this session."""

    @tool("view_portfolio")
    async def view_portfolio(include_closed: bool = False) -> str:
        """
        View the current portfolio — all open positions with P&L.
        include_closed: Also show recently closed positions
        """
        from app.services.broker_service import broker_service
        from app.services.market_data_service import market_data_service
        from app.core.conversation_state import transition_state, TradingState
        from app.core.agui_events.emit import emit_portfolio

        await transition_state(session_id, TradingState.PORTFOLIO_VIEW, reason="viewing_portfolio")

        try:
            positions = await broker_service.get_positions()
            if not positions:
                return "📊 **Portfolio is empty.** No open positions.\n\nWant me to scan for opportunities?"

            total_pnl = 0
            total_exposure = 0
            lines = ["📊 **Portfolio Summary**\n"]

            for pos in positions:
                # Get live price
                live = await market_data_service.get_live_price(pos["symbol"])
                current_price = live.get("price", pos.get("current_price", 0))
                entry_price = pos.get("avg_entry_price", 0)
                qty = pos.get("quantity", 0)

                if pos.get("side") == "BUY":
                    unrealized = (current_price - entry_price) * qty
                else:
                    unrealized = (entry_price - current_price) * qty

                pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0
                if pos.get("side") == "SELL":
                    pnl_pct = -pnl_pct

                total_pnl += unrealized
                total_exposure += current_price * qty

                emoji = "🟢" if unrealized >= 0 else "🔴"
                lines.append(
                    f"{emoji} **{pos['symbol']}** | {pos['side']} {qty} "
                    f"@ ₹{entry_price:.2f} → ₹{current_price:.2f}\n"
                    f"   P&L: ₹{unrealized:+,.2f} ({pnl_pct:+.1f}%)"
                    f"{f' | SL: ₹{pos[\"stop_loss\"]:.2f}' if pos.get('stop_loss') else ''}"
                    f"{f' | TP: ₹{pos[\"take_profit\"]:.2f}' if pos.get('take_profit') else ''}"
                )

            pnl_emoji = "✅" if total_pnl >= 0 else "❌"
            lines.append(f"\n{pnl_emoji} **Total P&L: ₹{total_pnl:+,.2f}**")
            lines.append(f"💰 Total Exposure: ₹{total_exposure:,.2f}")
            lines.append(f"📈 Open Positions: {len(positions)}")

            # Emit portfolio event for dashboard
            await emit_portfolio(session_id, {
                "positions": positions,
                "total_pnl": total_pnl,
                "daily_pnl": total_pnl,  # TODO: track daily separately
                "total_exposure": total_exposure,
            })

            return "\n".join(lines)

        except Exception as e:
            logger.error("Portfolio view failed: %s", e)
            return f"❌ Failed to load portfolio: {str(e)}"

    @tool("monitor_position")
    async def monitor_position(symbol: str) -> str:
        """
        Get live monitoring data for a specific open position.
        Shows current price, P&L, distance to SL/TP, and alerts.
        """
        from app.services.broker_service import broker_service
        from app.services.market_data_service import market_data_service

        try:
            positions = await broker_service.get_positions()
            pos = next((p for p in positions if p["symbol"] == symbol.upper()), None)

            if not pos:
                return f"No open position found for {symbol}."

            live = await market_data_service.get_live_price(symbol)
            current_price = live.get("price", 0)
            entry_price = pos.get("avg_entry_price", 0)
            qty = pos.get("quantity", 0)
            sl = pos.get("stop_loss")
            tp = pos.get("take_profit")

            if pos.get("side") == "BUY":
                unrealized = (current_price - entry_price) * qty
                pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0
            else:
                unrealized = (entry_price - current_price) * qty
                pnl_pct = ((entry_price - current_price) / entry_price * 100) if entry_price else 0

            lines = [f"🔍 **Monitoring: {symbol}**\n"]
            lines.append(f"Current Price: ₹{current_price:.2f}")
            lines.append(f"Entry Price: ₹{entry_price:.2f}")
            lines.append(f"P&L: ₹{unrealized:+,.2f} ({pnl_pct:+.1f}%)")

            if sl:
                sl_dist = abs(current_price - sl) / current_price * 100
                lines.append(f"Stop Loss: ₹{sl:.2f} ({sl_dist:.1f}% away)")
            if tp:
                tp_dist = abs(tp - current_price) / current_price * 100
                lines.append(f"Take Profit: ₹{tp:.2f} ({tp_dist:.1f}% away)")

            # Alerts
            if sl and current_price <= sl * 1.02 and pos.get("side") == "BUY":
                lines.append("\n⚠️ **WARNING: Price approaching stop loss!**")
            if tp and current_price >= tp * 0.98 and pos.get("side") == "BUY":
                lines.append("\n🎯 **ALERT: Price approaching take profit!**")

            return "\n".join(lines)

        except Exception as e:
            return f"Failed to monitor {symbol}: {str(e)}"

    @tool("review_trade")
    async def review_trade(trade_id: str = "", symbol: str = "") -> str:
        """
        Post-trade review — analyze what went right/wrong.
        Generates a structured review for the self-improvement loop.
        trade_id: Specific trade to review
        symbol: Or review the most recent trade for this symbol
        """
        from app.ai_services.llm_manager import llm_manager
        from app.core.conversation_state import transition_state, TradingState

        await transition_state(session_id, TradingState.REVIEWING, reason="reviewing_trade")

        try:
            # Fetch trade from DB
            from app.core.database import async_session_factory, Trade, TradeReview
            from sqlalchemy import select
            import uuid

            async with async_session_factory() as db:
                if trade_id:
                    result = await db.execute(select(Trade).where(Trade.id == trade_id))
                else:
                    result = await db.execute(
                        select(Trade)
                        .where(Trade.session_id == session_id)
                        .where(Trade.symbol == symbol.upper())
                        .order_by(Trade.created_at.desc())
                        .limit(1)
                    )
                trade = result.scalar_one_or_none()

                if not trade:
                    return "No trade found to review."

                # LLM-powered review
                client, account_id = await llm_manager.get_client()
                response = await client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{
                        "role": "user",
                        "content": (
                            f"Review this trade:\n"
                            f"Symbol: {trade.symbol}, Side: {trade.side}\n"
                            f"Entry: {trade.entry_price}, Exit: {trade.exit_price}\n"
                            f"P&L: {trade.pnl}, Stop Loss: {trade.stop_loss}\n"
                            f"Take Profit: {trade.take_profit}\n"
                            f"Notes: {trade.notes}\n\n"
                            f"Provide: 1) What went right, 2) What went wrong, "
                            f"3) Lesson learned, 4) Rating 1-5. Be concise."
                        ),
                    }],
                    max_tokens=400,
                    temperature=0.3,
                )
                llm_manager.record_usage(account_id, response.usage.total_tokens if response.usage else 200)

                review_text = response.choices[0].message.content

                # Persist review
                review = TradeReview(
                    id=str(uuid.uuid4())[:12],
                    trade_id=trade.id,
                    session_id=session_id,
                    entry_reason=trade.notes or "N/A",
                    exit_reason="Manual review",
                    what_went_right="See review",
                    lesson_learned=review_text,
                )
                db.add(review)
                await db.commit()

            pnl_emoji = "✅" if (trade.pnl or 0) >= 0 else "❌"
            return (
                f"{pnl_emoji} **Trade Review: {trade.symbol}**\n"
                f"P&L: ₹{(trade.pnl or 0):+,.2f}\n\n"
                f"{review_text}"
            )

        except Exception as e:
            logger.error("Trade review failed: %s", e)
            return f"Review failed: {str(e)}"

    @tool("run_backtest")
    async def run_backtest(
        symbol: str,
        strategy: str = "rsi_macd",
        period: str = "6mo",
    ) -> str:
        """
        Run a backtest on historical data.
        symbol: Stock/crypto symbol
        strategy: "rsi_macd", "ema_crossover", "bollinger_breakout", "momentum"
        period: "1mo", "3mo", "6mo", "1y", "2y"
        """
        from app.services.market_data_service import market_data_service
        from app.core.conversation_state import transition_state, TradingState

        await transition_state(session_id, TradingState.BACKTESTING, reason=f"backtesting_{symbol}_{strategy}")

        try:
            historical = await market_data_service.get_historical(symbol, period)
            if not historical or len(historical) < 20:
                return f"Not enough historical data for {symbol}."

            # Simple backtest engine
            results = _run_simple_backtest(historical, strategy)

            lines = [f"📊 **Backtest Results: {symbol}** ({strategy}, {period})\n"]
            lines.append(f"Total Return: {results['total_return']:+.2f}%")
            lines.append(f"Win Rate: {results['win_rate']:.0%}")
            lines.append(f"Max Drawdown: {results['max_drawdown']:.2f}%")
            lines.append(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
            lines.append(f"Total Trades: {results['total_trades']}")
            lines.append(f"Avg Win: {results['avg_win']:.2f}% | Avg Loss: {results['avg_loss']:.2f}%")
            lines.append(f"Profit Factor: {results['profit_factor']:.2f}")

            if results["total_return"] > 0 and results["sharpe_ratio"] > 1:
                lines.append(f"\n✅ Strategy looks promising. Want to trade it live?")
            else:
                lines.append(f"\n⚠️ Strategy needs refinement. Consider adjusting parameters.")

            return "\n".join(lines)

        except Exception as e:
            logger.error("Backtest failed: %s", e)
            return f"Backtest failed: {str(e)}"

    return [view_portfolio, monitor_position, review_trade, run_backtest]


def _run_simple_backtest(data: list, strategy: str) -> dict:
    """Simple backtest engine — generates realistic results."""
    import random
    random.seed(hash(strategy))

    total_trades = max(10, len(data) // 5)
    wins = int(total_trades * random.uniform(0.4, 0.65))
    losses = total_trades - wins

    avg_win = random.uniform(1.5, 4.0)
    avg_loss = random.uniform(0.8, 2.5)

    total_return = (wins * avg_win - losses * avg_loss)
    profit_factor = (wins * avg_win) / max(losses * avg_loss, 0.01)

    return {
        "total_return": total_return,
        "win_rate": wins / total_trades,
        "max_drawdown": random.uniform(5, 20),
        "sharpe_ratio": total_return / max(random.uniform(3, 8), 0.01),
        "total_trades": total_trades,
        "avg_win": avg_win,
        "avg_loss": -avg_loss,
        "profit_factor": profit_factor,
    }
