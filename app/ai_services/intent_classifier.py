"""
Trading intent classifier — determines what the trader wants to do.
Maps user messages to trading intents (scan, trade, portfolio, etc.).
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional

from app.ai_services.llm_manager import llm_manager

logger = logging.getLogger(__name__)

# Intent categories for trading
TRADING_INTENTS = {
    "scan_market": "User wants to scan/browse market opportunities",
    "analyze_symbol": "User wants detailed analysis of a specific symbol",
    "place_trade": "User wants to buy or sell",
    "modify_position": "User wants to update stop loss, take profit, or quantity",
    "close_position": "User wants to close/exit a position",
    "view_portfolio": "User wants to see their portfolio or P&L",
    "check_signal": "User is asking about a signal or recommendation",
    "run_backtest": "User wants to backtest a strategy",
    "set_alert": "User wants to set a price or condition alert",
    "risk_query": "User is asking about risk, exposure, or limits",
    "general_query": "General market question or conversation",
    "greeting": "User is greeting or starting a conversation",
}

CLASSIFICATION_PROMPT = """You are a trading intent classifier. Given the trader's message, classify it into exactly ONE of these intents:

{intents}

Respond with ONLY the intent key (e.g., "scan_market", "place_trade"). Nothing else.

If the message contains multiple intents, pick the PRIMARY one (the action they want to take first).

Trader's message: "{message}"
Intent:"""


async def classify_intent(message: str) -> Dict[str, Any]:
    """
    Classify the trader's message into a trading intent.
    Returns: {"intent": str, "confidence": float}
    """
    # Quick keyword matching for obvious intents (fast path)
    lower = message.lower().strip()

    # Fast-path keyword matching
    keyword_map = {
        "scan_market": ["scan", "market overview", "what's moving", "movers", "top gainers", "top losers", "screener"],
        "place_trade": ["buy", "sell", "long", "short", "enter", "place order", "execute"],
        "close_position": ["close", "exit", "square off", "book profit", "book loss"],
        "view_portfolio": ["portfolio", "positions", "holdings", "p&l", "pnl", "balance"],
        "modify_position": ["stop loss", "take profit", "trailing", "modify", "update sl", "update tp", "add more"],
        "analyze_symbol": ["analyze", "analysis", "technical", "chart", "rsi", "macd", "what about"],
        "run_backtest": ["backtest", "back test", "historical", "simulate"],
        "risk_query": ["risk", "exposure", "drawdown", "margin", "leverage"],
        "greeting": ["hi", "hello", "hey", "good morning", "good evening", "namaste"],
    }

    for intent, keywords in keyword_map.items():
        if any(kw in lower for kw in keywords):
            return {"intent": intent, "confidence": 0.85}

    # LLM classification for ambiguous messages
    try:
        intents_str = "\n".join(f"- {k}: {v}" for k, v in TRADING_INTENTS.items())
        prompt = CLASSIFICATION_PROMPT.format(intents=intents_str, message=message)

        client, account_id = await llm_manager.get_client()
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0,
        )
        llm_manager.record_usage(account_id, response.usage.total_tokens if response.usage else 10)

        intent = response.choices[0].message.content.strip().lower().replace('"', '').replace("'", "")

        if intent in TRADING_INTENTS:
            return {"intent": intent, "confidence": 0.9}
        else:
            logger.warning("Unknown intent from LLM: %s", intent)
            return {"intent": "general_query", "confidence": 0.5}

    except Exception as e:
        logger.error("Intent classification failed: %s", e)
        return {"intent": "general_query", "confidence": 0.3}


async def extract_symbol(message: str) -> Optional[str]:
    """Extract stock/crypto symbol from the message."""
    # Common patterns
    import re

    # Direct symbol mention (e.g., "RELIANCE", "NIFTY", "BTC")
    # Look for uppercase words that could be symbols
    symbols = re.findall(r'\b([A-Z]{2,10})\b', message)
    if symbols:
        # Filter out common English words
        noise = {"THE", "AND", "FOR", "BUY", "SELL", "GET", "SET", "NOT", "CAN", "HOW", "WHAT", "WHEN"}
        filtered = [s for s in symbols if s not in noise]
        if filtered:
            return filtered[0]

    # NSE format (e.g., "reliance", "tata motors")
    # Will be resolved by market data service
    return None
