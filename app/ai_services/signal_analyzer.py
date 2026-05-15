"""
Signal analyzer — the brain of the trading agent.
Combines technical analysis, sentiment, and market regime to generate signals.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import uuid

from app.core.config import settings

logger = logging.getLogger(__name__)


class SignalAnalyzer:
    """
    Multi-factor signal generation engine.
    Combines technical indicators, sentiment, and regime detection.
    """

    def __init__(self):
        self.indicator_weights = {
            "rsi": 0.20,
            "macd": 0.20,
            "bollinger": 0.15,
            "volume": 0.15,
            "trend": 0.15,
            "sentiment": 0.15,
        }

    async def analyze(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        timeframe: str = "1h",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run full analysis and generate a signal.
        If session_id is provided, uses RL-learned weights instead of defaults.
        Returns signal dict with strength, confidence, and trade parameters.
        """
        # Use RL-learned weights if available
        active_weights = self.indicator_weights
        if session_id:
            try:
                from app.ai_services.reinforcement_engine import rl_engine
                regime = await rl_engine.detect_regime(symbol, market_data)
                active_weights = await rl_engine.get_current_weights(session_id, regime)
            except Exception:
                pass  # Fall back to default weights

        indicators = {}

        # 1. RSI Analysis
        indicators["rsi"] = self._analyze_rsi(market_data.get("rsi", 50))

        # 2. MACD Analysis
        indicators["macd"] = self._analyze_macd(market_data.get("macd", {}))

        # 3. Bollinger Bands
        indicators["bollinger"] = self._analyze_bollinger(
            market_data.get("close", 0),
            market_data.get("bollinger", {}),
        )

        # 4. Volume Analysis
        indicators["volume"] = self._analyze_volume(market_data.get("volume_data", {}))

        # 5. Trend Analysis (EMA crossover)
        indicators["trend"] = self._analyze_trend(market_data.get("ema", {}))

        # 6. Sentiment (placeholder for news/social sentiment)
        indicators["sentiment"] = market_data.get("sentiment_score", 0)

        # Weighted signal score (-1 to +1) using RL-learned or default weights
        total_score = sum(
            indicators.get(key, 0) * weight
            for key, weight in active_weights.items()
        )

        # Map score to signal strength
        strength = self._score_to_strength(total_score)
        confidence = min(abs(total_score), 1.0)

        # Calculate trade parameters
        close_price = market_data.get("close", 0)
        atr = market_data.get("atr", close_price * 0.02)  # Default 2% ATR

        signal = {
            "id": str(uuid.uuid4())[:12],
            "symbol": symbol,
            "strength": strength,
            "confidence": confidence,
            "score": round(total_score, 4),
            "timeframe": timeframe,
            "indicators": indicators,
            "entry_suggested": close_price,
            "sl_suggested": self._calculate_stop_loss(close_price, strength, atr),
            "tp_suggested": self._calculate_take_profit(close_price, strength, atr),
            "reason": self._build_reason(indicators, strength),
            "source": "agent",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "Signal generated: %s %s (confidence=%.2f, score=%.4f)",
            symbol, strength, confidence, total_score,
        )
        return signal

    def _analyze_rsi(self, rsi: float) -> float:
        """RSI → signal score (-1 to +1)."""
        if rsi <= 20:
            return 0.9   # Strong oversold → buy
        elif rsi <= 30:
            return 0.5   # Oversold
        elif rsi >= 80:
            return -0.9  # Strong overbought → sell
        elif rsi >= 70:
            return -0.5  # Overbought
        else:
            return 0.0   # Neutral

    def _analyze_macd(self, macd_data: Dict) -> float:
        """MACD → signal score."""
        macd_line = macd_data.get("macd", 0)
        signal_line = macd_data.get("signal", 0)
        histogram = macd_data.get("histogram", 0)

        if macd_line > signal_line and histogram > 0:
            return 0.7  # Bullish crossover
        elif macd_line < signal_line and histogram < 0:
            return -0.7  # Bearish crossover
        elif histogram > 0:
            return 0.3
        elif histogram < 0:
            return -0.3
        return 0.0

    def _analyze_bollinger(self, price: float, bb: Dict) -> float:
        """Bollinger Bands → signal score."""
        upper = bb.get("upper", price * 1.02)
        lower = bb.get("lower", price * 0.98)
        middle = bb.get("middle", price)

        if price <= lower:
            return 0.6   # At lower band → buy
        elif price >= upper:
            return -0.6  # At upper band → sell
        elif price < middle:
            return 0.2
        elif price > middle:
            return -0.2
        return 0.0

    def _analyze_volume(self, vol_data: Dict) -> float:
        """Volume analysis → signal score."""
        current = vol_data.get("current", 0)
        average = vol_data.get("average", 1)
        price_change = vol_data.get("price_change_pct", 0)

        if average == 0:
            return 0.0

        vol_ratio = current / average

        if vol_ratio > 2.0 and price_change > 0:
            return 0.8   # High volume + up move → bullish
        elif vol_ratio > 2.0 and price_change < 0:
            return -0.8  # High volume + down move → bearish
        elif vol_ratio > 1.5 and price_change > 0:
            return 0.4
        elif vol_ratio > 1.5 and price_change < 0:
            return -0.4
        return 0.0

    def _analyze_trend(self, ema_data: Dict) -> float:
        """EMA trend analysis → signal score."""
        ema_short = ema_data.get("ema_9", 0)
        ema_medium = ema_data.get("ema_21", 0)
        ema_long = ema_data.get("ema_50", 0)

        if not all([ema_short, ema_medium, ema_long]):
            return 0.0

        if ema_short > ema_medium > ema_long:
            return 0.8   # Strong uptrend
        elif ema_short < ema_medium < ema_long:
            return -0.8  # Strong downtrend
        elif ema_short > ema_medium:
            return 0.3   # Short-term bullish
        elif ema_short < ema_medium:
            return -0.3  # Short-term bearish
        return 0.0

    def _score_to_strength(self, score: float) -> str:
        """Map composite score to signal strength."""
        if score >= 0.6:
            return "STRONG_BUY"
        elif score >= 0.3:
            return "BUY"
        elif score <= -0.6:
            return "STRONG_SELL"
        elif score <= -0.3:
            return "SELL"
        return "NEUTRAL"

    def _calculate_stop_loss(self, price: float, strength: str, atr: float) -> float:
        """Calculate stop loss based on ATR and signal direction."""
        sl_multiplier = settings.DEFAULT_STOP_LOSS_PCT / 100
        atr_sl = atr * 1.5

        if "BUY" in strength:
            return round(price - max(price * sl_multiplier, atr_sl), 2)
        elif "SELL" in strength:
            return round(price + max(price * sl_multiplier, atr_sl), 2)
        return round(price - price * sl_multiplier, 2)

    def _calculate_take_profit(self, price: float, strength: str, atr: float) -> float:
        """Calculate take profit with risk-reward ratio of at least 2:1."""
        tp_multiplier = settings.DEFAULT_TAKE_PROFIT_PCT / 100
        atr_tp = atr * 3.0  # 2:1 ratio on 1.5 ATR stop loss

        if "BUY" in strength:
            return round(price + max(price * tp_multiplier, atr_tp), 2)
        elif "SELL" in strength:
            return round(price - max(price * tp_multiplier, atr_tp), 2)
        return round(price + price * tp_multiplier, 2)

    def _build_reason(self, indicators: Dict[str, float], strength: str) -> str:
        """Build a human-readable reason for the signal."""
        reasons = []

        rsi = indicators.get("rsi", 0)
        if abs(rsi) > 0.3:
            reasons.append(f"RSI {'oversold' if rsi > 0 else 'overbought'}")

        macd = indicators.get("macd", 0)
        if abs(macd) > 0.3:
            reasons.append(f"MACD {'bullish' if macd > 0 else 'bearish'} crossover")

        volume = indicators.get("volume", 0)
        if abs(volume) > 0.3:
            reasons.append(f"{'High' if volume > 0 else 'Weak'} volume confirmation")

        trend = indicators.get("trend", 0)
        if abs(trend) > 0.3:
            reasons.append(f"EMA {'uptrend' if trend > 0 else 'downtrend'}")

        if not reasons:
            return f"Mixed signals, overall {strength}"

        return f"{strength}: " + ", ".join(reasons)


# Singleton
signal_analyzer = SignalAnalyzer()
