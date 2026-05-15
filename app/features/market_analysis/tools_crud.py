"""
Market analysis CRUD tools — the agent's eyes on the market.
Factory pattern: tools are scoped to session_id via closure.
Session-scoped CRUD tools created via factory closures.
"""
from __future__ import annotations

import logging
from typing import Optional, List

from crewai_tools import tool

logger = logging.getLogger(__name__)


def create_market_analysis_tools(session_id: str):
    """Factory: create market analysis tools scoped to this session."""

    @tool("scan_markets")
    async def scan_markets(
        market: str = "indian",
        scan_type: str = "top_movers",
    ) -> str:
        """
        Scan markets for opportunities.
        market: "indian", "us", "crypto"
        scan_type: "top_movers", "volume_spike", "breakout", "gap_up", "gap_down"
        Returns a summary of interesting stocks/assets.
        """
        from app.services.market_data_service import market_data_service
        from app.core.conversation_state import transition_state, TradingState

        await transition_state(session_id, TradingState.MARKET_WATCH, reason="scanning_markets")

        try:
            results = await market_data_service.scan(market=market, scan_type=scan_type)

            if not results:
                return "No significant movers found right now. Markets are quiet."

            lines = [f"📊 **Market Scan: {scan_type.replace('_', ' ').title()}** ({market.upper()})\n"]
            for item in results[:10]:
                change_emoji = "🟢" if item.get("change_pct", 0) >= 0 else "🔴"
                lines.append(
                    f"{change_emoji} **{item['symbol']}** — ₹{item.get('price', 0):.2f} "
                    f"({item.get('change_pct', 0):+.2f}%) Vol: {item.get('volume', 0):,}"
                )

            lines.append("\nWant me to analyze any of these in detail?")
            return "\n".join(lines)

        except Exception as e:
            logger.error("Market scan failed: %s", e)
            return f"Market scan failed: {str(e)}"

    @tool("get_market_data")
    async def get_market_data(
        symbol: str,
        timeframe: str = "1d",
        include_indicators: bool = True,
    ) -> str:
        """
        Get market data for a specific symbol.
        symbol: Stock/crypto symbol (e.g., "RELIANCE", "NIFTY50", "BTC")
        timeframe: "1m", "5m", "15m", "1h", "1d", "1w"
        include_indicators: Include RSI, MACD, Bollinger Bands, etc.
        """
        from app.services.market_data_service import market_data_service

        try:
            data = await market_data_service.get_quote(symbol, timeframe, include_indicators)

            if not data:
                return f"Could not fetch data for {symbol}. Check the symbol name."

            lines = [f"📈 **{symbol}** ({timeframe})\n"]
            lines.append(f"Price: ₹{data.get('close', 0):.2f}")
            lines.append(f"Change: {data.get('change_pct', 0):+.2f}%")
            lines.append(f"Open: ₹{data.get('open', 0):.2f} | High: ₹{data.get('high', 0):.2f} | Low: ₹{data.get('low', 0):.2f}")
            lines.append(f"Volume: {data.get('volume', 0):,}")

            if include_indicators and data.get("indicators"):
                ind = data["indicators"]
                lines.append(f"\n**Indicators:**")
                if "rsi" in ind:
                    lines.append(f"RSI(14): {ind['rsi']:.1f}")
                if "macd" in ind:
                    macd = ind["macd"]
                    lines.append(f"MACD: {macd.get('macd', 0):.2f} | Signal: {macd.get('signal', 0):.2f}")
                if "bollinger" in ind:
                    bb = ind["bollinger"]
                    lines.append(f"BB: Upper {bb.get('upper', 0):.2f} | Middle {bb.get('middle', 0):.2f} | Lower {bb.get('lower', 0):.2f}")
                if "ema" in ind:
                    ema = ind["ema"]
                    lines.append(f"EMA: 9={ema.get('ema_9', 0):.2f} | 21={ema.get('ema_21', 0):.2f} | 50={ema.get('ema_50', 0):.2f}")
                if "atr" in ind:
                    lines.append(f"ATR(14): {ind['atr']:.2f}")

            return "\n".join(lines)

        except Exception as e:
            logger.error("Market data fetch failed: %s", e)
            return f"Failed to get data for {symbol}: {str(e)}"

    @tool("run_technical_analysis")
    async def run_technical_analysis(
        symbol: str,
        timeframe: str = "1h",
    ) -> str:
        """
        Run full technical analysis on a symbol and generate a signal.
        This is the core analysis tool — combines RSI, MACD, Bollinger, Volume, Trend.
        Returns a detailed analysis with a BUY/SELL/HOLD recommendation.
        """
        from app.services.market_data_service import market_data_service
        from app.ai_services.signal_analyzer import signal_analyzer
        from app.core.conversation_state import transition_state, TradingState
        from app.core.session_context import push_signal
        from app.core.agui_events.emit import emit_signal

        await transition_state(session_id, TradingState.ANALYZING, reason=f"analyzing_{symbol}")

        try:
            # Fetch full market data with indicators
            data = await market_data_service.get_quote(symbol, timeframe, include_indicators=True)
            if not data:
                return f"Cannot analyze {symbol} — no data available."

            # Run the signal analyzer
            signal = await signal_analyzer.analyze(symbol, data, timeframe)

            # Store signal if actionable
            if signal["strength"] != "NEUTRAL":
                await push_signal(session_id, signal)
                await emit_signal(session_id, signal)

                # Transition to SIGNAL_DETECTED
                await transition_state(
                    session_id, TradingState.SIGNAL_DETECTED,
                    reason=f"signal_{signal['strength']}",
                    metadata={"signal_id": signal["id"], "symbol": symbol},
                )

            # Build detailed response
            lines = [f"🔍 **Technical Analysis: {symbol}** ({timeframe})\n"]
            lines.append(f"**Recommendation: {signal['strength']}** (Confidence: {signal['confidence']:.0%})")
            lines.append(f"\n{signal['reason']}")
            lines.append(f"\n**Indicator Breakdown:**")

            for key, score in signal["indicators"].items():
                direction = "Bullish" if score > 0.1 else "Bearish" if score < -0.1 else "Neutral"
                emoji = "🟢" if score > 0.1 else "🔴" if score < -0.1 else "⚪"
                lines.append(f"  {emoji} {key.upper()}: {direction} ({score:+.2f})")

            if signal["entry_suggested"]:
                lines.append(f"\n**Suggested Trade:**")
                lines.append(f"  Entry: ₹{signal['entry_suggested']:.2f}")
                lines.append(f"  Stop Loss: ₹{signal['sl_suggested']:.2f}")
                lines.append(f"  Take Profit: ₹{signal['tp_suggested']:.2f}")

            if signal["strength"] != "NEUTRAL":
                lines.append(f"\n⚡ Signal queued. Shall I execute this trade?")

            return "\n".join(lines)

        except Exception as e:
            logger.error("Technical analysis failed: %s", e)
            return f"Analysis failed for {symbol}: {str(e)}"

    @tool("analyze_sentiment")
    async def analyze_sentiment(symbol: str) -> str:
        """
        Analyze market sentiment for a symbol using news and social data.
        Returns a sentiment score and summary.
        """
        from app.ai_services.llm_manager import llm_manager

        try:
            client, account_id = await llm_manager.get_client()
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "user",
                    "content": (
                        f"Analyze the current market sentiment for {symbol}. "
                        f"Consider recent news, market conditions, and sector trends. "
                        f"Provide: 1) Sentiment score (-1 to +1), 2) Key factors, "
                        f"3) Risk factors. Be concise."
                    ),
                }],
                max_tokens=300,
                temperature=0.3,
            )
            llm_manager.record_usage(account_id, response.usage.total_tokens if response.usage else 100)

            return f"📰 **Sentiment Analysis: {symbol}**\n\n{response.choices[0].message.content}"

        except Exception as e:
            logger.error("Sentiment analysis failed: %s", e)
            return f"Sentiment analysis unavailable for {symbol}: {str(e)}"

    return [scan_markets, get_market_data, run_technical_analysis, analyze_sentiment]
