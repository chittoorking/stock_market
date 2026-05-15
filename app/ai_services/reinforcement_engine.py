"""
Reinforcement Learning Engine — the self-improving brain.

How it works:
1. Every closed trade produces a TradeOutcome (win/loss, which indicators were right/wrong)
2. The engine scores each indicator's contribution to that trade
3. Over time, indicator weights shift: indicators that predict winners get more weight,
   indicators that predict losers get penalized
4. Strategy confidence scores update per symbol, timeframe, and market regime
5. A periodic "meta-review" runs across all trades to detect drift and recalibrate

This is NOT a simple moving average of win rates. It's a multi-dimensional feedback loop:
- Per-indicator scoring (RSI was right, MACD was wrong → adjust both)
- Per-regime tracking (trending market vs. ranging → different weights)
- Recency-weighted (recent trades matter more than old ones)
- Exploration vs. exploitation (keeps a minimum weight to avoid blind spots)
"""
from __future__ import annotations

import json
import math
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict

from app.core.redis import redis_get, redis_set, get_redis_client
from app.core.config import settings

logger = logging.getLogger(__name__)

# ─── Constants ───
MIN_WEIGHT = 0.05          # No indicator drops below 5% — prevents blind spots
MAX_WEIGHT = 0.40          # No indicator dominates above 40%
LEARNING_RATE = 0.05       # How fast weights shift per trade
RECENCY_DECAY = 0.95       # Exponential decay for older trades
MIN_TRADES_TO_LEARN = 5    # Don't adjust until we have enough data
EXPLORATION_BONUS = 0.02   # Small bonus for underused indicators


@dataclass
class TradeOutcome:
    """Structured outcome of a single trade for the RL engine."""
    trade_id: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    duration_minutes: float
    timeframe: str
    # Which indicators contributed to the entry signal
    signal_indicators: Dict[str, float]  # indicator_name → score at entry (-1 to +1)
    signal_strength: str
    signal_confidence: float
    # Market conditions at entry
    market_regime: str  # "trending_up", "trending_down", "ranging", "volatile"
    volume_regime: str  # "high", "normal", "low"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def is_winner(self) -> bool:
        return self.pnl > 0

    def reward(self) -> float:
        """
        Calculate reward signal. Not just win/loss — considers magnitude.
        A +5% win is better than +0.1%. A -5% loss is worse than -0.1%.
        Capped to prevent extreme outliers from dominating.
        """
        # Magnitude-weighted reward, capped at ±1
        raw = self.pnl_pct / 10.0  # Normalize: 10% = reward of 1.0
        return max(-1.0, min(1.0, raw))


@dataclass
class IndicatorPerformance:
    """Tracks how well a single indicator has performed over time."""
    name: str
    total_trades: int = 0
    correct_predictions: int = 0  # Indicator direction matched trade outcome
    total_reward: float = 0.0
    weighted_reward: float = 0.0  # Recency-weighted
    avg_confidence_when_right: float = 0.0
    avg_confidence_when_wrong: float = 0.0
    # Per-regime tracking
    regime_scores: Dict[str, float] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        if self.total_trades == 0:
            return 0.5
        return self.correct_predictions / self.total_trades

    @property
    def avg_reward(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.weighted_reward / self.total_trades


@dataclass
class StrategyProfile:
    """Performance profile for a specific strategy/timeframe/regime combination."""
    strategy_key: str  # e.g., "rsi_macd_1h_trending"
    total_trades: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    avg_pnl_pct: float = 0.0
    max_win_pct: float = 0.0
    max_loss_pct: float = 0.0
    win_streak: int = 0
    loss_streak: int = 0
    current_streak: int = 0
    sharpe_approx: float = 0.0  # Approximate Sharpe from trade returns
    last_updated: str = ""

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.wins / self.total_trades

    @property
    def expectancy(self) -> float:
        """Expected value per trade."""
        if self.total_trades == 0:
            return 0.0
        return self.total_pnl / self.total_trades


class ReinforcementEngine:
    """
    The core RL engine. Maintains state in Redis, learns from every trade.

    Architecture:
    - Indicator weights are global (shared across symbols) but regime-adjusted
    - Strategy profiles are per (timeframe × regime) combination
    - Trade history feeds the learning loop
    - Meta-review runs periodically to prevent drift
    """

    def __init__(self):
        self._default_weights = {
            "rsi": 0.20,
            "macd": 0.20,
            "bollinger": 0.15,
            "volume": 0.15,
            "trend": 0.15,
            "sentiment": 0.15,
        }

    # ─── Core Learning Loop ───

    async def record_outcome(self, session_id: str, outcome: TradeOutcome):
        """
        Record a trade outcome and update the RL state.
        This is called after every position close.
        """
        reward = outcome.reward()

        # 1. Update per-indicator performance
        for indicator_name, indicator_score in outcome.signal_indicators.items():
            await self._update_indicator_performance(
                session_id, indicator_name, indicator_score, outcome, reward,
            )

        # 2. Update strategy profile
        strategy_key = f"{outcome.timeframe}_{outcome.market_regime}"
        await self._update_strategy_profile(session_id, strategy_key, outcome)

        # 3. Adjust indicator weights based on new evidence
        await self._adjust_weights(session_id, outcome, reward)

        # 4. Store the outcome for meta-review
        await self._store_outcome(session_id, outcome)

        logger.info(
            "RL recorded: %s %s pnl=%.2f%% reward=%.3f regime=%s",
            outcome.symbol, "WIN" if outcome.is_winner() else "LOSS",
            outcome.pnl_pct, reward, outcome.market_regime,
        )

    async def get_current_weights(self, session_id: str, regime: str = "normal") -> Dict[str, float]:
        """
        Get the current indicator weights, adjusted for market regime.
        This is what the SignalAnalyzer uses instead of hardcoded weights.
        """
        # Load base learned weights
        raw = await redis_get(f"rl:weights:{session_id}")
        if raw:
            weights = json.loads(raw)
        else:
            weights = dict(self._default_weights)

        # Apply regime adjustment
        regime_adj = await self._get_regime_adjustments(session_id, regime)
        for indicator, adj in regime_adj.items():
            if indicator in weights:
                weights[indicator] = max(MIN_WEIGHT, min(MAX_WEIGHT, weights[indicator] + adj))

        # Normalize to sum to 1.0
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        return weights

    async def get_strategy_confidence(self, session_id: str, timeframe: str, regime: str) -> float:
        """
        Get confidence score for a strategy in current conditions.
        Used to decide whether to auto-execute or skip.
        """
        key = f"{timeframe}_{regime}"
        profile = await self._load_strategy_profile(session_id, key)

        if profile.total_trades < MIN_TRADES_TO_LEARN:
            return 0.5  # Not enough data — neutral confidence

        # Composite confidence from win rate, expectancy, and recency
        wr_score = profile.win_rate  # 0 to 1
        exp_score = min(1.0, max(0.0, (profile.expectancy + 5) / 10))  # Normalize around 0
        streak_bonus = 0.05 * profile.current_streak if profile.current_streak > 0 else -0.05 * abs(profile.current_streak)

        confidence = (wr_score * 0.4 + exp_score * 0.4 + 0.5 + streak_bonus) * 0.5
        return max(0.1, min(0.95, confidence))

    async def should_trade(self, session_id: str, signal: Dict[str, Any]) -> Tuple[bool, str]:
        """
        The autonomous decision gate. Should the bot execute this signal?
        Returns (should_execute, reason).
        """
        timeframe = signal.get("timeframe", "1h")
        regime = await self.detect_regime(signal.get("symbol", ""), signal)

        # Get strategy confidence
        strategy_conf = await self.get_strategy_confidence(session_id, timeframe, regime)

        # Get signal confidence
        signal_conf = signal.get("confidence", 0.5)

        # Combined confidence
        combined = strategy_conf * 0.4 + signal_conf * 0.6

        # Decision thresholds
        if combined >= 0.70:
            return True, f"High confidence ({combined:.0%}): strategy={strategy_conf:.0%}, signal={signal_conf:.0%}"
        elif combined >= 0.55:
            # Check recent performance
            profile = await self._load_strategy_profile(session_id, f"{timeframe}_{regime}")
            if profile.current_streak >= -2:  # Not on a losing streak
                return True, f"Moderate confidence ({combined:.0%}), no losing streak"
            return False, f"Moderate confidence ({combined:.0%}) but on losing streak ({profile.current_streak})"
        else:
            return False, f"Low confidence ({combined:.0%}): strategy={strategy_conf:.0%}, signal={signal_conf:.0%}"

    # ─── Meta-Review (periodic recalibration) ───

    async def run_meta_review(self, session_id: str) -> Dict[str, Any]:
        """
        Periodic meta-review across all trade history.
        Detects:
        - Indicator drift (an indicator that used to work but stopped)
        - Regime shift (market changed but weights haven't adapted)
        - Overfit (too concentrated on one indicator)
        """
        outcomes = await self._load_all_outcomes(session_id)
        if len(outcomes) < MIN_TRADES_TO_LEARN * 2:
            return {"status": "insufficient_data", "trades": len(outcomes)}

        # Split into recent (last 20%) vs. historical
        split_point = int(len(outcomes) * 0.8)
        historical = outcomes[:split_point]
        recent = outcomes[split_point:]

        review = {
            "total_trades": len(outcomes),
            "recent_trades": len(recent),
            "adjustments": [],
        }

        # Compare indicator accuracy: recent vs. historical
        for indicator in self._default_weights:
            hist_accuracy = self._calc_indicator_accuracy(historical, indicator)
            recent_accuracy = self._calc_indicator_accuracy(recent, indicator)

            drift = recent_accuracy - hist_accuracy
            if abs(drift) > 0.15:  # Significant drift
                direction = "improving" if drift > 0 else "degrading"
                review["adjustments"].append({
                    "indicator": indicator,
                    "drift": round(drift, 3),
                    "direction": direction,
                    "historical_accuracy": round(hist_accuracy, 3),
                    "recent_accuracy": round(recent_accuracy, 3),
                })

                # Apply correction
                weights = json.loads(await redis_get(f"rl:weights:{session_id}") or "{}")
                if not weights:
                    weights = dict(self._default_weights)

                # Boost improving indicators, penalize degrading ones
                adjustment = drift * LEARNING_RATE * 2  # Meta-review adjusts faster
                weights[indicator] = max(MIN_WEIGHT, min(MAX_WEIGHT,
                    weights.get(indicator, self._default_weights[indicator]) + adjustment
                ))

                # Normalize
                total = sum(weights.values())
                weights = {k: v / total for k, v in weights.items()}
                await redis_set(f"rl:weights:{session_id}", json.dumps(weights))

        # Check for overfit
        current_weights = json.loads(await redis_get(f"rl:weights:{session_id}") or "{}")
        if current_weights:
            max_w = max(current_weights.values())
            min_w = min(current_weights.values())
            if max_w / max(min_w, 0.01) > 5:  # One indicator 5x another
                review["warning"] = "Potential overfit detected — weights are too concentrated"

        review["status"] = "completed"
        logger.info("Meta-review completed: %d adjustments", len(review["adjustments"]))
        return review

    # ─── Regime Detection ───

    async def detect_regime(self, symbol: str, market_data: Dict[str, Any]) -> str:
        """
        Detect current market regime from indicators.
        Used to select the right weight profile.
        """
        ema = market_data.get("ema", market_data.get("indicators", {}).get("ema", {}))
        atr = market_data.get("atr", market_data.get("indicators", {}).get("atr", 0))
        close = market_data.get("close", market_data.get("entry_suggested", 0))

        ema_9 = ema.get("ema_9", 0)
        ema_21 = ema.get("ema_21", 0)
        ema_50 = ema.get("ema_50", 0)

        # Trend detection
        if ema_9 and ema_21 and ema_50:
            if ema_9 > ema_21 > ema_50:
                trend = "trending_up"
            elif ema_9 < ema_21 < ema_50:
                trend = "trending_down"
            else:
                trend = "ranging"
        else:
            trend = "unknown"

        # Volatility detection
        if close and atr:
            atr_pct = (atr / close) * 100
            if atr_pct > 3.0:
                return "volatile"

        return trend

    # ─── Private: Weight Adjustment ───

    async def _adjust_weights(self, session_id: str, outcome: TradeOutcome, reward: float):
        """
        Core RL update: adjust indicator weights based on trade outcome.
        Uses a modified policy gradient approach:
        - If trade was profitable, increase weight of indicators that agreed with the trade direction
        - If trade was a loss, decrease weight of indicators that agreed (they were wrong)
        """
        raw = await redis_get(f"rl:weights:{session_id}")
        weights = json.loads(raw) if raw else dict(self._default_weights)

        for indicator, score in outcome.signal_indicators.items():
            if indicator not in weights:
                continue

            # Was this indicator right?
            # If BUY trade and indicator was positive → indicator agreed
            # If trade won → indicator was right. If lost → indicator was wrong.
            if outcome.side == "BUY":
                indicator_agreed = score > 0
            else:
                indicator_agreed = score < 0

            if outcome.is_winner() and indicator_agreed:
                # Correct prediction → increase weight
                delta = LEARNING_RATE * abs(reward) * abs(score)
                weights[indicator] = min(MAX_WEIGHT, weights[indicator] + delta)
            elif not outcome.is_winner() and indicator_agreed:
                # Wrong prediction → decrease weight
                delta = LEARNING_RATE * abs(reward) * abs(score)
                weights[indicator] = max(MIN_WEIGHT, weights[indicator] - delta)
            elif outcome.is_winner() and not indicator_agreed:
                # Indicator disagreed but trade won → slight penalty (indicator missed it)
                delta = LEARNING_RATE * 0.3 * abs(reward)
                weights[indicator] = max(MIN_WEIGHT, weights[indicator] - delta)
            # If lost and indicator disagreed → indicator was right to disagree, no change needed

        # Exploration bonus: underweight indicators get a small boost
        min_current = min(weights.values())
        for k in weights:
            if weights[k] == min_current:
                weights[k] += EXPLORATION_BONUS

        # Normalize
        total = sum(weights.values())
        weights = {k: round(v / total, 4) for k, v in weights.items()}

        await redis_set(f"rl:weights:{session_id}", json.dumps(weights))

    async def _update_indicator_performance(
        self, session_id: str, indicator: str, score: float,
        outcome: TradeOutcome, reward: float,
    ):
        """Update per-indicator performance tracking."""
        key = f"rl:indicator:{session_id}:{indicator}"
        raw = await redis_get(key)
        perf = json.loads(raw) if raw else {
            "name": indicator, "total_trades": 0, "correct_predictions": 0,
            "total_reward": 0, "weighted_reward": 0,
            "regime_scores": {},
        }

        perf["total_trades"] += 1
        perf["total_reward"] += reward

        # Apply recency decay
        perf["weighted_reward"] = perf["weighted_reward"] * RECENCY_DECAY + reward

        # Was prediction correct?
        predicted_up = score > 0
        actually_up = outcome.pnl > 0
        if predicted_up == actually_up or (score == 0):
            perf["correct_predictions"] += 1

        # Per-regime tracking
        regime = outcome.market_regime
        if regime not in perf["regime_scores"]:
            perf["regime_scores"][regime] = {"correct": 0, "total": 0}
        perf["regime_scores"][regime]["total"] += 1
        if predicted_up == actually_up:
            perf["regime_scores"][regime]["correct"] += 1

        await redis_set(key, json.dumps(perf), ttl=86400 * 90)  # 90 days

    async def _update_strategy_profile(self, session_id: str, strategy_key: str, outcome: TradeOutcome):
        """Update strategy profile for this timeframe/regime."""
        profile = await self._load_strategy_profile(session_id, strategy_key)

        profile.total_trades += 1
        profile.total_pnl += outcome.pnl
        profile.avg_pnl_pct = profile.total_pnl / profile.total_trades if profile.total_trades else 0

        if outcome.is_winner():
            profile.wins += 1
            profile.max_win_pct = max(profile.max_win_pct, outcome.pnl_pct)
            if profile.current_streak >= 0:
                profile.current_streak += 1
            else:
                profile.current_streak = 1
            profile.win_streak = max(profile.win_streak, profile.current_streak)
        else:
            profile.max_loss_pct = min(profile.max_loss_pct, outcome.pnl_pct)
            if profile.current_streak <= 0:
                profile.current_streak -= 1
            else:
                profile.current_streak = -1
            profile.loss_streak = max(profile.loss_streak, abs(profile.current_streak))

        profile.last_updated = datetime.now(timezone.utc).isoformat()

        key = f"rl:strategy:{session_id}:{strategy_key}"
        await redis_set(key, json.dumps(asdict(profile)), ttl=86400 * 90)

    async def _load_strategy_profile(self, session_id: str, strategy_key: str) -> StrategyProfile:
        key = f"rl:strategy:{session_id}:{strategy_key}"
        raw = await redis_get(key)
        if raw:
            data = json.loads(raw)
            return StrategyProfile(**data)
        return StrategyProfile(strategy_key=strategy_key)

    async def _store_outcome(self, session_id: str, outcome: TradeOutcome):
        """Store outcome in Redis list for meta-review."""
        client = await get_redis_client()
        key = f"rl:outcomes:{session_id}"
        await client.rpush(key, json.dumps(asdict(outcome)))
        await client.expire(key, 86400 * 90)  # 90 days

    async def _load_all_outcomes(self, session_id: str) -> List[Dict]:
        client = await get_redis_client()
        key = f"rl:outcomes:{session_id}"
        raw_list = await client.lrange(key, 0, -1)
        return [json.loads(item) for item in raw_list]

    async def _get_regime_adjustments(self, session_id: str, regime: str) -> Dict[str, float]:
        """Get per-regime weight adjustments based on historical performance."""
        adjustments = {}
        for indicator in self._default_weights:
            key = f"rl:indicator:{session_id}:{indicator}"
            raw = await redis_get(key)
            if not raw:
                continue
            perf = json.loads(raw)
            regime_data = perf.get("regime_scores", {}).get(regime)
            if regime_data and regime_data["total"] >= 3:
                accuracy = regime_data["correct"] / regime_data["total"]
                # If indicator is >60% accurate in this regime, boost; <40%, penalize
                adjustments[indicator] = (accuracy - 0.5) * 0.1
        return adjustments

    def _calc_indicator_accuracy(self, outcomes: List[Dict], indicator: str) -> float:
        """Calculate indicator accuracy across a set of outcomes."""
        correct = 0
        total = 0
        for o in outcomes:
            indicators = o.get("signal_indicators", {})
            if indicator not in indicators:
                continue
            score = indicators[indicator]
            pnl = o.get("pnl", 0)
            predicted_up = score > 0
            actually_up = pnl > 0
            total += 1
            if predicted_up == actually_up:
                correct += 1
        return correct / total if total > 0 else 0.5

    # ─── Dashboard / Reporting ───

    async def get_learning_status(self, session_id: str) -> Dict[str, Any]:
        """Get full RL status for dashboard."""
        weights = await self.get_current_weights(session_id)
        outcomes = await self._load_all_outcomes(session_id)

        # Per-indicator performance
        indicator_stats = {}
        for indicator in self._default_weights:
            key = f"rl:indicator:{session_id}:{indicator}"
            raw = await redis_get(key)
            if raw:
                perf = json.loads(raw)
                indicator_stats[indicator] = {
                    "weight": weights.get(indicator, 0),
                    "accuracy": perf["correct_predictions"] / max(perf["total_trades"], 1),
                    "total_trades": perf["total_trades"],
                    "weighted_reward": round(perf["weighted_reward"], 3),
                }

        return {
            "current_weights": weights,
            "total_outcomes": len(outcomes),
            "indicator_performance": indicator_stats,
            "default_weights": self._default_weights,
        }


# Singleton
rl_engine = ReinforcementEngine()
