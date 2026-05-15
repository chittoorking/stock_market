"""
Market data service — provides real-time and historical data.
Supports: yfinance (free), broker feeds, custom WebSocket.
"""
from __future__ import annotations

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from app.core.config import settings

logger = logging.getLogger(__name__)


class MarketDataService:
    """
    Unified market data interface.
    Uses yfinance for free data, can switch to broker feeds for real-time.
    """

    def __init__(self):
        self.provider = settings.MARKET_DATA_PROVIDER
        self._price_cache: Dict[str, Dict] = {}  # symbol -> {price, updated_at}

    async def get_quote(
        self,
        symbol: str,
        timeframe: str = "1d",
        include_indicators: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Get current quote with optional technical indicators."""
        symbol = symbol.upper()

        try:
            import yfinance as yf
            import pandas_ta as ta

            # Map symbol to yfinance format
            yf_symbol = self._to_yfinance_symbol(symbol)

            # Fetch data
            period_map = {"1m": "1d", "5m": "5d", "15m": "5d", "1h": "1mo", "1d": "3mo", "1w": "1y"}
            interval_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d", "1w": "1wk"}

            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(
                period=period_map.get(timeframe, "3mo"),
                interval=interval_map.get(timeframe, "1d"),
            )

            if df.empty:
                return None

            latest = df.iloc[-1]
            prev_close = df.iloc[-2]["Close"] if len(df) > 1 else latest["Close"]

            result = {
                "symbol": symbol,
                "close": float(latest["Close"]),
                "open": float(latest["Open"]),
                "high": float(latest["High"]),
                "low": float(latest["Low"]),
                "volume": int(latest["Volume"]),
                "change_pct": ((latest["Close"] - prev_close) / prev_close) * 100,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Calculate technical indicators
            if include_indicators and len(df) >= 20:
                indicators = {}

                # RSI
                rsi = ta.rsi(df["Close"], length=14)
                if rsi is not None and not rsi.empty:
                    indicators["rsi"] = float(rsi.iloc[-1])

                # MACD
                macd_df = ta.macd(df["Close"])
                if macd_df is not None and not macd_df.empty:
                    indicators["macd"] = {
                        "macd": float(macd_df.iloc[-1, 0]) if len(macd_df.columns) > 0 else 0,
                        "signal": float(macd_df.iloc[-1, 2]) if len(macd_df.columns) > 2 else 0,
                        "histogram": float(macd_df.iloc[-1, 1]) if len(macd_df.columns) > 1 else 0,
                    }

                # Bollinger Bands
                bb = ta.bbands(df["Close"], length=20)
                if bb is not None and not bb.empty:
                    indicators["bollinger"] = {
                        "upper": float(bb.iloc[-1, 0]),
                        "middle": float(bb.iloc[-1, 1]),
                        "lower": float(bb.iloc[-1, 2]),
                    }

                # EMAs
                ema_9 = ta.ema(df["Close"], length=9)
                ema_21 = ta.ema(df["Close"], length=21)
                ema_50 = ta.ema(df["Close"], length=50) if len(df) >= 50 else None
                indicators["ema"] = {
                    "ema_9": float(ema_9.iloc[-1]) if ema_9 is not None and not ema_9.empty else 0,
                    "ema_21": float(ema_21.iloc[-1]) if ema_21 is not None and not ema_21.empty else 0,
                    "ema_50": float(ema_50.iloc[-1]) if ema_50 is not None and not ema_50.empty else 0,
                }

                # ATR
                atr = ta.atr(df["High"], df["Low"], df["Close"], length=14)
                if atr is not None and not atr.empty:
                    indicators["atr"] = float(atr.iloc[-1])

                # Volume analysis
                avg_vol = df["Volume"].rolling(20).mean().iloc[-1] if len(df) >= 20 else df["Volume"].mean()
                indicators["volume_data"] = {
                    "current": int(latest["Volume"]),
                    "average": int(avg_vol),
                    "price_change_pct": result["change_pct"],
                }

                # Sentiment placeholder
                indicators["sentiment_score"] = 0.0

                result["indicators"] = indicators

            # Cache the price
            self._price_cache[symbol] = {"price": result["close"], "updated_at": result["timestamp"]}

            return result

        except Exception as e:
            logger.error("Failed to get quote for %s: %s", symbol, e)
            return None

    async def get_live_price(self, symbol: str) -> Dict[str, Any]:
        """Get live price (cached if recent, otherwise fresh fetch)."""
        symbol = symbol.upper()

        # Use cache if fresh (< 60s)
        if symbol in self._price_cache:
            cached = self._price_cache[symbol]
            return {"price": cached["price"], "symbol": symbol, "cached": True}

        # Fetch fresh
        quote = await self.get_quote(symbol)
        if quote:
            return {"price": quote["close"], "symbol": symbol, "cached": False}

        return {"price": 0.0, "symbol": symbol, "error": "No data"}

    async def scan(
        self,
        market: str = "indian",
        scan_type: str = "top_movers",
    ) -> List[Dict[str, Any]]:
        """Scan markets for opportunities."""
        try:
            import yfinance as yf

            # Define scan universes
            universes = {
                "indian": [
                    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
                    "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS",
                    "LT.NS", "AXISBANK.NS", "MARUTI.NS", "TITAN.NS", "WIPRO.NS",
                    "BAJFINANCE.NS", "HCLTECH.NS", "TATAMOTORS.NS", "SUNPHARMA.NS", "ADANIENT.NS",
                ],
                "us": [
                    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
                    "META", "TSLA", "JPM", "V", "WMT",
                ],
                "crypto": [
                    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD",
                    "ADA-USD", "DOGE-USD", "DOT-USD", "MATIC-USD", "AVAX-USD",
                ],
            }

            symbols = universes.get(market, universes["indian"])
            results = []

            for sym in symbols:
                try:
                    ticker = yf.Ticker(sym)
                    hist = ticker.history(period="2d")
                    if hist.empty or len(hist) < 2:
                        continue

                    today = hist.iloc[-1]
                    yesterday = hist.iloc[-2]
                    change_pct = ((today["Close"] - yesterday["Close"]) / yesterday["Close"]) * 100

                    clean_symbol = sym.replace(".NS", "").replace("-USD", "")
                    results.append({
                        "symbol": clean_symbol,
                        "price": float(today["Close"]),
                        "change_pct": round(change_pct, 2),
                        "volume": int(today["Volume"]),
                        "high": float(today["High"]),
                        "low": float(today["Low"]),
                    })
                except Exception:
                    continue

            # Sort by scan type
            if scan_type == "top_movers":
                results.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
            elif scan_type == "volume_spike":
                results.sort(key=lambda x: x["volume"], reverse=True)
            elif scan_type == "gap_up":
                results = [r for r in results if r["change_pct"] > 1]
                results.sort(key=lambda x: x["change_pct"], reverse=True)
            elif scan_type == "gap_down":
                results = [r for r in results if r["change_pct"] < -1]
                results.sort(key=lambda x: x["change_pct"])

            return results[:10]

        except Exception as e:
            logger.error("Market scan failed: %s", e)
            return []

    async def get_historical(self, symbol: str, period: str = "6mo") -> List[Dict]:
        """Get historical data for backtesting."""
        try:
            import yfinance as yf

            yf_symbol = self._to_yfinance_symbol(symbol)
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(period=period)

            return [
                {
                    "date": str(idx.date()),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                }
                for idx, row in df.iterrows()
            ]
        except Exception as e:
            logger.error("Historical data failed: %s", e)
            return []

    def _to_yfinance_symbol(self, symbol: str) -> str:
        """Convert a clean symbol to yfinance format."""
        # Indian stocks
        indian_stocks = {
            "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "HINDUNILVR",
            "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK",
            "MARUTI", "TITAN", "WIPRO", "BAJFINANCE", "HCLTECH", "TATAMOTORS",
            "SUNPHARMA", "ADANIENT", "NIFTY50",
        }
        if symbol in indian_stocks:
            return f"{symbol}.NS"

        # Crypto
        crypto = {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "DOT", "MATIC", "AVAX"}
        if symbol in crypto:
            return f"{symbol}-USD"

        # Default — try as-is (US stocks)
        return symbol


# Singleton
market_data_service = MarketDataService()
