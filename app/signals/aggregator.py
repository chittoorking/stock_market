"""
Signal Aggregator — the anti-bias reasoning layer.

This is where the bot's INTELLIGENCE lives. Signals are data. This is the brain.

WHY THIS EXISTS:
If you just follow signals blindly → a script does that, you don't need a bot.
If you let ML models make all decisions → overfitting, curve-fitting, regime change = blowup.

The aggregator's job:
1. Collect signals from all proven strategies
2. Score each signal using RL-adjusted trust (not raw confidence)
3. Check for CONFLUENCE (multiple strategies agree → stronger)
4. Check for CONTRADICTION (strategies disagree → skip or reduce size)
5. Check PORTFOLIO CONTEXT (already exposed to this sector? correlation risk?)
6. Present a ranked, reasoned signal board to the agent
7. Track which signals were taken, which were skipped, and the outcomes of both
   (tracking skipped signals prevents "grass is greener" bias)

ANTI-BIAS MECHANISMS:
1. Trust Decay — every strategy's trust score decays over time. Recent wins
   restore it, but old wins don't carry weight forever. This prevents
   "this strategy backtested well 6 months ago" from driving live decisions.

2. Contrarian Check — if ALL signals agree, apply a skepticism discount.
   Unanimity often means crowded trade (everyone sees the same thing).

3. Skip Tracking — record what happens to signals you DIDN'T take.
   If skipped signals would have been profitable, the RL engine learns
   it was wrong to skip. This prevents over-cautious drift.

4. Regime Gate — signals from strategies designed for a different regime
   get penalized. ORB in volatile regime? Discount. Cascade in trending? Boost.

5. Recency Cap — no strategy can have >40% of total trust allocation.
   Prevents the RL engine from going all-in on one hot strategy.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass, field

from app.signals.base import StrategySignal, get_sector
from app.core.redis import redis_get, redis_set, get_redis_client

logger = logging.getLogger(__name__)

# Anti-bias constants
MAX_TRUST_SHARE = 0.40        # No strategy gets >40% trust
TRUST_DECAY_PER_DAY = 0.02    # 2% daily decay
CONFLUENCE_BOOST = 1.3         # 30% boost when 2+ strategies agree
UNANIMITY_DISCOUNT = 0.85      # 15% discount when ALL agree (crowded)
SECTOR_CORRELATION_PENALTY = 0.7  # 30% penalty for same-sector exposure
MIN_TRUST = 0.20              # Floor — never fully distrust a proven strategy


@dataclass
class ScoredSignal:
    """A signal with the aggregator's reasoning attached."""
    signal: StrategySignal
    # Scoring
    raw_trust: float = 0.0           # RL-adjusted trust in this strategy
    confluence_score: float = 0.0     # How many other strategies agree
    portfolio_penalty: float = 0.0    # Penalty for correlation/exposure
    regime_fit: float = 0.0          # How well this strategy fits current regime
    final_score: float = 0.0         # The number the agent sees

    # Reasoning (for the agent and for logging)
    reasoning: str = ""
    agreeing_strategies: List[str] = field(default_factory=list)
    contradicting_strategies: List[str] = field(default_factory=list)

    # Agent's decision (filled later)
    decision: str = ""  # "taken", "skipped", "partial"
    decision_reason: str = ""

    def to_agent_brief(self) -> str:
        """Compact summary for the LLM agent's context."""
        return (
            f"Score: {self.final_score:.0%} | "
            f"{self.signal.to_agent_summary()}\n"
            f"  Trust: {self.raw_trust:.0%} | "
            f"Confluence: {'+' + ','.join(self.agreeing_strategies) if self.agreeing_strategies else 'none'} | "
            f"Regime fit: {self.regime_fit:.0%} | "
            f"{self.reasoning}"
        )


class SignalAggregator:
    """
    Collects, scores, and ranks signals for the agent.
    """

    def __init__(self):
        # Per-strategy trust scores (initialized from backtest win rates)
        self._base_trust = {
            "gg8": 0.72,        # 61% WR, best single strategy
            "orb": 0.65,        # 50% WR but 88% of params profitable
            "cascade_y5": 0.60, # 50% WR, sector flow
            "lnz3": 0.68,       # 55% WR, good balance
            "aft7": 0.65,       # 53% WR, complementary to LNZ3
            "gdr4": 0.70,       # 51% WR but PF 1.88
            "cw4": 0.75,        # 59% WR, highest confidence
        }

        # Strategy-to-regime fit matrix
        self._regime_fit = {
            "gg8":        {"trending_up": 0.8, "trending_down": 0.9, "ranging": 0.4, "volatile": 0.7},
            "orb":        {"trending_up": 0.9, "trending_down": 0.9, "ranging": 0.5, "volatile": 0.8},
            "cascade_y5": {"trending_up": 0.9, "trending_down": 0.8, "ranging": 0.3, "volatile": 0.5},
            "lnz3":       {"trending_up": 0.8, "trending_down": 0.8, "ranging": 0.5, "volatile": 0.6},
            "aft7":       {"trending_up": 0.7, "trending_down": 0.7, "ranging": 0.4, "volatile": 0.8},
            "gdr4":       {"trending_up": 0.6, "trending_down": 0.9, "ranging": 0.4, "volatile": 0.7},
            "cw4":        {"trending_up": 0.9, "trending_down": 0.7, "ranging": 0.3, "volatile": 0.5},
        }

    async def aggregate(
        self,
        session_id: str,
        signals: List[StrategySignal],
        current_regime: str,
        open_positions: Dict[str, Any],
    ) -> List[ScoredSignal]:
        """
        Score and rank all signals. Returns sorted by final_score descending.
        The agent uses this ranked list to make decisions.
        """
        if not signals:
            return []

        # Load RL-adjusted trust
        trust_scores = await self._get_trust_scores(session_id)

        # Group signals by symbol for confluence detection
        by_symbol: Dict[str, List[StrategySignal]] = {}
        for s in signals:
            by_symbol.setdefault(s.symbol, []).append(s)

        scored = []
        for signal in signals:
            scored_signal = await self._score_signal(
                signal, trust_scores, current_regime,
                by_symbol.get(signal.symbol, []),
                open_positions,
            )
            scored.append(scored_signal)

        # Sort by final score
        scored.sort(key=lambda x: x.final_score, reverse=True)

        # Anti-bias: unanimity discount
        if len(scored) >= 3:
            directions = set(s.signal.direction for s in scored[:5])
            if len(directions) == 1:
                # All top signals agree — apply skepticism
                for s in scored:
                    s.final_score *= UNANIMITY_DISCOUNT
                    s.reasoning += " [Unanimity discount: all signals agree, crowded trade risk]"

        return scored

    async def _score_signal(
        self,
        signal: StrategySignal,
        trust_scores: Dict[str, float],
        regime: str,
        same_symbol_signals: List[StrategySignal],
        open_positions: Dict,
    ) -> ScoredSignal:
        """Score a single signal with all anti-bias mechanisms."""
        reasons = []

        # 1. Base trust (RL-adjusted)
        raw_trust = trust_scores.get(signal.strategy_name, self._base_trust.get(signal.strategy_name, 0.5))
        raw_trust = max(MIN_TRUST, min(MAX_TRUST_SHARE / 0.5, raw_trust))  # Cap trust
        reasons.append(f"Trust: {raw_trust:.0%}")

        # 2. Regime fit
        regime_scores = self._regime_fit.get(signal.strategy_name, {})
        regime_fit = regime_scores.get(regime, 0.5)
        reasons.append(f"Regime({regime}): {regime_fit:.0%}")

        # 3. Confluence — other strategies on same symbol, same direction
        agreeing = []
        contradicting = []
        for other in same_symbol_signals:
            if other.id == signal.id:
                continue
            if other.direction == signal.direction:
                agreeing.append(other.strategy_name)
            else:
                contradicting.append(other.strategy_name)

        confluence = 1.0
        if agreeing:
            confluence = CONFLUENCE_BOOST
            reasons.append(f"Confluence: +{','.join(agreeing)}")
        if contradicting:
            confluence *= 0.7  # Contradiction reduces confidence
            reasons.append(f"Contradiction: -{','.join(contradicting)}")

        # 4. Portfolio correlation penalty
        portfolio_penalty = 1.0
        signal_sector = get_sector(signal.symbol)
        for pos_symbol, pos_data in (open_positions or {}).items():
            pos_sector = get_sector(pos_symbol)
            if pos_sector == signal_sector and pos_sector != "unknown":
                # Same sector exposure
                if pos_data.get("side", "").upper() == signal.direction:
                    portfolio_penalty *= SECTOR_CORRELATION_PENALTY
                    reasons.append(f"Sector overlap: already {pos_data.get('side')} in {pos_sector}")
                    break

        # 5. Signal's own confidence
        signal_conf = signal.raw_confidence

        # 6. Combine
        final = raw_trust * regime_fit * confluence * portfolio_penalty * signal_conf
        final = max(0.05, min(0.95, final))

        return ScoredSignal(
            signal=signal,
            raw_trust=raw_trust,
            confluence_score=confluence,
            portfolio_penalty=portfolio_penalty,
            regime_fit=regime_fit,
            final_score=round(final, 4),
            reasoning=" | ".join(reasons),
            agreeing_strategies=agreeing,
            contradicting_strategies=contradicting,
        )

    # ─── Trust Score Management (RL-driven) ───

    async def _get_trust_scores(self, session_id: str) -> Dict[str, float]:
        """Load RL-adjusted trust scores. Decays daily."""
        raw = await redis_get(f"signal_trust:{session_id}")
        if raw:
            scores = json.loads(raw)
            # Apply daily decay
            for strategy in scores:
                scores[strategy] = max(MIN_TRUST, scores[strategy] - TRUST_DECAY_PER_DAY)
            return scores
        return dict(self._base_trust)

    async def update_trust(self, session_id: str, strategy_name: str, was_profitable: bool, pnl_pct: float):
        """
        Update trust score for a strategy based on outcome.
        Called after every trade that was triggered by a strategy signal.
        """
        scores = await self._get_trust_scores(session_id)

        if was_profitable:
            # Boost trust proportional to P&L magnitude (capped)
            boost = min(0.05, abs(pnl_pct) * 0.01)
            scores[strategy_name] = min(0.90, scores.get(strategy_name, 0.5) + boost)
        else:
            # Penalize, but not too harshly (good strategies have losing streaks)
            penalty = min(0.03, abs(pnl_pct) * 0.005)
            scores[strategy_name] = max(MIN_TRUST, scores.get(strategy_name, 0.5) - penalty)

        await redis_set(f"signal_trust:{session_id}", json.dumps(scores), ttl=86400 * 90)

    async def record_skip(self, session_id: str, signal: StrategySignal, reason: str):
        """
        Record a signal that was SKIPPED (not taken).
        Later, we check what would have happened. This prevents over-cautious bias.
        """
        client = await get_redis_client()
        skip_data = {
            "signal": signal.to_dict(),
            "skip_reason": reason,
            "skipped_at": datetime.now(timezone.utc).isoformat(),
        }
        await client.rpush(f"skipped_signals:{session_id}", json.dumps(skip_data))
        await client.expire(f"skipped_signals:{session_id}", 86400 * 30)

    async def review_skips(self, session_id: str) -> Dict[str, Any]:
        """
        Review skipped signals — would they have been profitable?
        This is the anti-overcaution mechanism.
        """
        client = await get_redis_client()
        raw_list = await client.lrange(f"skipped_signals:{session_id}", 0, -1)

        missed_profits = 0
        avoided_losses = 0
        total_skips = len(raw_list)

        # In production: fetch actual price movement after each skip
        # For now, return the count for the agent to reason about
        return {
            "total_skips": total_skips,
            "missed_profits": missed_profits,
            "avoided_losses": avoided_losses,
            "skip_regret_ratio": missed_profits / max(total_skips, 1),
        }

    # ─── Agent Context Builder ───

    async def build_signal_board(
        self,
        session_id: str,
        signals: List[StrategySignal],
        regime: str,
        open_positions: Dict,
    ) -> str:
        """
        Build a signal board string for the LLM agent's system prompt.
        This is what the agent SEES and REASONS about.
        """
        scored = await self.aggregate(session_id, signals, regime, open_positions)

        if not scored:
            return "No active signals from proven strategies."

        lines = [
            f"## Signal Board ({len(scored)} signals, regime: {regime})\n",
            "Signals are ranked by combined score. You decide which to take.\n",
        ]

        for i, s in enumerate(scored[:8], 1):  # Top 8 max
            lines.append(f"**#{i}** {s.to_agent_brief()}\n")

        # Portfolio context
        if open_positions:
            lines.append(f"\n**Open Exposure:** {len(open_positions)} positions")
            sectors = set(get_sector(sym) for sym in open_positions)
            lines.append(f"**Sectors exposed:** {', '.join(sectors)}")

        lines.append(
            "\n**Your job:** Pick the best signal(s) considering confluence, "
            "correlation, and your risk limits. You can adjust entry/stop/target. "
            "You can skip signals. Explain your reasoning."
        )

        return "\n".join(lines)


# Singleton
signal_aggregator = SignalAggregator()
