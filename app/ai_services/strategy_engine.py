"""
Strategy Engine — manages multiple trading strategies and selects the best one.

Strategies are not hardcoded rules. Each strategy is a CONFIGURATION of the signal analyzer:
- Which indicators to weight heavily
- Which timeframes to scan
- Entry/exit rules
- Risk parameters

The RL engine scores each strategy over time. The strategy engine picks the best one
for the current market regime.

Think of it like this:
- SignalAnalyzer = the calculator (runs indicators, scores them)
- StrategyEngine = the strategy selector (picks which calculator settings to use)
- RLEngine = the judge (tracks which strategies actually make money)
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

from app.ai_services.reinforcement_engine import rl_engine
from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Strategy:
    """A trading strategy configuration."""
    name: str
    description: str
    # Indicator weight overrides (what this strategy emphasizes)
    weight_overrides: Dict[str, float]
    # Which timeframes this strategy works on
    timeframes: List[str]
    # Which regimes this strategy is designed for
    preferred_regimes: List[str]
    # Risk parameters
    stop_loss_atr_multiplier: float = 1.5
    take_profit_atr_multiplier: float = 3.0
    min_confidence: float = 0.55
    # Position sizing adjustment (multiplier on base size)
    position_size_multiplier: float = 1.0


# ─── Built-in Strategies ───

STRATEGIES: Dict[str, Strategy] = {
    "momentum": Strategy(
        name="momentum",
        description="Follow strong trends with volume confirmation. Best in trending markets.",
        weight_overrides={
            "rsi": 0.10,
            "macd": 0.25,
            "bollinger": 0.10,
            "volume": 0.25,
            "trend": 0.25,
            "sentiment": 0.05,
        },
        timeframes=["1h", "1d"],
        preferred_regimes=["trending_up", "trending_down"],
        stop_loss_atr_multiplier=2.0,
        take_profit_atr_multiplier=4.0,
        min_confidence=0.60,
        position_size_multiplier=1.2,
    ),

    "mean_reversion": Strategy(
        name="mean_reversion",
        description="Buy oversold, sell overbought. Best in ranging markets.",
        weight_overrides={
            "rsi": 0.30,
            "macd": 0.10,
            "bollinger": 0.30,
            "volume": 0.10,
            "trend": 0.05,
            "sentiment": 0.15,
        },
        timeframes=["15m", "1h"],
        preferred_regimes=["ranging"],
        stop_loss_atr_multiplier=1.0,
        take_profit_atr_multiplier=2.0,
        min_confidence=0.55,
        position_size_multiplier=0.8,  # Smaller size for counter-trend
    ),

    "breakout": Strategy(
        name="breakout",
        description="Catch breakouts from consolidation with volume surge.",
        weight_overrides={
            "rsi": 0.10,
            "macd": 0.15,
            "bollinger": 0.25,
            "volume": 0.30,
            "trend": 0.15,
            "sentiment": 0.05,
        },
        timeframes=["1h", "1d"],
        preferred_regimes=["ranging", "volatile"],
        stop_loss_atr_multiplier=1.5,
        take_profit_atr_multiplier=3.5,
        min_confidence=0.65,
        position_size_multiplier=1.0,
    ),

    "scalp": Strategy(
        name="scalp",
        description="Quick in-and-out trades on short timeframes. Tight SL/TP.",
        weight_overrides={
            "rsi": 0.20,
            "macd": 0.25,
            "bollinger": 0.15,
            "volume": 0.20,
            "trend": 0.15,
            "sentiment": 0.05,
        },
        timeframes=["1m", "5m", "15m"],
        preferred_regimes=["trending_up", "trending_down", "volatile"],
        stop_loss_atr_multiplier=0.8,
        take_profit_atr_multiplier=1.5,
        min_confidence=0.60,
        position_size_multiplier=0.6,  # Smaller for scalps
    ),

    "sentiment_driven": Strategy(
        name="sentiment_driven",
        description="Trade based on news and sentiment shifts. Best during events.",
        weight_overrides={
            "rsi": 0.10,
            "macd": 0.10,
            "bollinger": 0.05,
            "volume": 0.20,
            "trend": 0.10,
            "sentiment": 0.45,
        },
        timeframes=["1h", "1d"],
        preferred_regimes=["volatile", "trending_up", "trending_down"],
        stop_loss_atr_multiplier=2.0,
        take_profit_atr_multiplier=3.0,
        min_confidence=0.70,
        position_size_multiplier=0.7,
    ),

    "adaptive": Strategy(
        name="adaptive",
        description="Uses RL-learned weights directly. The bot's own evolved strategy.",
        weight_overrides={},  # Empty — uses whatever the RL engine learned
        timeframes=["1h", "1d"],
        preferred_regimes=["trending_up", "trending_down", "ranging", "volatile", "unknown"],
        stop_loss_atr_multiplier=1.5,
        take_profit_atr_multiplier=3.0,
        min_confidence=0.55,
        position_size_multiplier=1.0,
    ),
}


class StrategyEngine:
    """
    Selects and manages trading strategies.
    The adaptive strategy is special — it uses whatever the RL engine has learned.
    """

    def __init__(self):
        self.active_strategies: List[str] = ["adaptive", "momentum", "mean_reversion"]

    async def select_best_strategy(
        self,
        session_id: str,
        symbol: str,
        regime: str,
        timeframe: str,
    ) -> Tuple[Strategy, float]:
        """
        Select the best strategy for current conditions.
        Returns (strategy, confidence_score).
        """
        candidates = []

        for name in self.active_strategies:
            strategy = STRATEGIES.get(name)
            if not strategy:
                continue

            # Check if strategy supports this timeframe and regime
            if timeframe not in strategy.timeframes:
                continue
            if regime not in strategy.preferred_regimes:
                continue

            # Get RL confidence for this strategy in current conditions
            confidence = await rl_engine.get_strategy_confidence(
                session_id, timeframe, regime,
            )

            candidates.append((strategy, confidence))

        if not candidates:
            # Fallback to adaptive (always applicable)
            return STRATEGIES["adaptive"], 0.5

        # Sort by confidence
        candidates.sort(key=lambda x: x[1], reverse=True)
        best_strategy, best_confidence = candidates[0]

        logger.info(
            "Strategy selected: %s (confidence=%.2f, regime=%s, timeframe=%s)",
            best_strategy.name, best_confidence, regime, timeframe,
        )

        return best_strategy, best_confidence

    async def get_strategy_weights(
        self,
        session_id: str,
        strategy: Strategy,
        regime: str,
    ) -> Dict[str, float]:
        """
        Get the final indicator weights for a strategy.
        For 'adaptive' strategy, uses pure RL weights.
        For others, blends strategy overrides with RL adjustments.
        """
        if strategy.name == "adaptive" or not strategy.weight_overrides:
            # Pure RL weights
            return await rl_engine.get_current_weights(session_id, regime)

        # Start with strategy's base weights
        weights = dict(strategy.weight_overrides)

        # Blend with RL adjustments (30% RL, 70% strategy)
        rl_weights = await rl_engine.get_current_weights(session_id, regime)
        for indicator in weights:
            if indicator in rl_weights:
                weights[indicator] = (weights[indicator] * 0.7) + (rl_weights[indicator] * 0.3)

        # Normalize
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        return weights

    def get_all_strategies(self) -> Dict[str, Dict[str, Any]]:
        """Get all strategies for display."""
        return {
            name: {
                "description": s.description,
                "timeframes": s.timeframes,
                "preferred_regimes": s.preferred_regimes,
                "min_confidence": s.min_confidence,
                "active": name in self.active_strategies,
            }
            for name, s in STRATEGIES.items()
        }

    def activate_strategy(self, name: str):
        if name in STRATEGIES and name not in self.active_strategies:
            self.active_strategies.append(name)

    def deactivate_strategy(self, name: str):
        if name in self.active_strategies and name != "adaptive":
            self.active_strategies.remove(name)


# Singleton
strategy_engine = StrategyEngine()
