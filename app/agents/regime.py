"""
Regime Agent — detects market regime and regime CHANGES.

Answers: "What kind of market is this? Has it changed since I entered?"

Regimes:
- trending_up: clear uptrend, EMAs aligned, buy dips
- trending_down: clear downtrend, sell bounces
- ranging: no direction, mean reversion works
- volatile: big swings, reduce size, widen stops
- breakout: transitioning from range to trend

A regime CHANGE is more important than the regime itself.
If you entered in trending_up and regime shifts to volatile → reduce exposure.
"""
from __future__ import annotations

from typing import Dict, Any, List

from app.agents.base import AgentOpinion, ActionType, Urgency


class RegimeAgent:
    NAME = "regime"

    def analyze(
        self,
        all_data: Dict[str, List[Dict]],
        date: str,
        current_bar: int,
        entry_bar: int,
        entry_regime: str,
    ) -> AgentOpinion:
        """Detect current regime and whether it has changed since entry."""
        current_regime = self._detect_regime(all_data, date, current_bar)
        entry_regime_actual = self._detect_regime(all_data, date, entry_bar) if entry_bar > 5 else entry_regime

        regime_changed = current_regime != entry_regime_actual

        if not regime_changed:
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.HOLD,
                urgency=Urgency.LOW,
                confidence=0.60,
                reason=f"Regime stable: {current_regime}",
                data={"regime": current_regime, "changed": False},
            )

        # Regime changed — analyze the transition
        if current_regime == "volatile" and entry_regime_actual in ("trending_up", "trending_down"):
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.TIGHTEN_STOP,
                urgency=Urgency.HIGH,
                confidence=0.80,
                reason=f"Regime shift: {entry_regime_actual} -> VOLATILE. Protect profits, reduce size.",
                suggested_size_change=0.5,
                data={"regime": current_regime, "previous": entry_regime_actual, "changed": True},
            )

        if current_regime == "ranging" and entry_regime_actual in ("trending_up", "trending_down"):
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.TIGHTEN_STOP,
                urgency=Urgency.MEDIUM,
                confidence=0.70,
                reason=f"Trend faded: {entry_regime_actual} -> ranging. Momentum strategies lose edge.",
                data={"regime": current_regime, "changed": True},
            )

        if current_regime in ("trending_up", "trending_down"):
            # New trend forming — is it with or against our position?
            is_with = (current_regime == "trending_up") or (current_regime == "trending_down")
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.HOLD if is_with else ActionType.CLOSE_NOW,
                urgency=Urgency.MEDIUM if is_with else Urgency.HIGH,
                confidence=0.70,
                reason=f"New regime: {current_regime} (was {entry_regime_actual})",
                data={"regime": current_regime, "changed": True},
            )

        return AgentOpinion(
            agent_name=self.NAME, action=ActionType.HOLD, urgency=Urgency.LOW,
            confidence=0.50,
            reason=f"Regime: {current_regime} (was {entry_regime_actual})",
            data={"regime": current_regime, "changed": regime_changed},
        )

    def _detect_regime(self, all_data: Dict, date: str, bar_idx: int) -> str:
        """Simple regime detection from broad market behavior."""
        # Use multiple stocks for regime detection
        changes = []
        for sym in ["RELIANCE", "HDFCBANK", "TCS", "INFY", "SBIN", "ITC", "TATASTEEL"]:
            bars = [b for b in all_data.get(sym, []) if b["timestamp"][:10] == date]
            if bars and len(bars) > bar_idx and bar_idx >= 3:
                start = max(0, bar_idx - 6)
                close_start = bars[start]["close"]
                close_now = bars[bar_idx]["close"]
                changes.append((close_now - close_start) / close_start * 100)

        if not changes:
            return "unknown"

        avg_change = sum(changes) / len(changes)
        spread = max(changes) - min(changes)

        if spread > 2.0:
            return "volatile"
        elif avg_change > 0.3:
            return "trending_up"
        elif avg_change < -0.3:
            return "trending_down"
        else:
            return "ranging"
