"""
Correlation Agent — prevents over-concentration.

Answers: "Am I too exposed to one direction/sector/theme?"

Checks:
- Same sector positions (metals already have 2 positions → skip 3rd)
- All positions same direction (all long in a volatile market = dangerous)
- Correlated movements (if TATASTEEL and JSWSTEEL move together → it's one bet, not two)
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional

from app.agents.base import AgentOpinion, ActionType, Urgency
from app.signals.base import get_sector


class CorrelationAgent:
    NAME = "correlation"

    def analyze_new_entry(
        self,
        symbol: str,
        direction: str,
        current_positions: Dict[str, Dict],
    ) -> AgentOpinion:
        """Should we take a new position given what we already hold?"""
        if not current_positions:
            return AgentOpinion(
                agent_name=self.NAME, action=ActionType.TAKE_SIGNAL,
                urgency=Urgency.LOW, confidence=0.80,
                reason="No existing positions, clear to enter",
            )

        target_sector = get_sector(symbol)
        same_sector = []
        same_direction = 0
        opposite_direction = 0

        for pos_sym, pos_data in current_positions.items():
            pos_sector = get_sector(pos_sym)
            pos_dir = pos_data.get("direction", "")

            if pos_sector == target_sector and target_sector != "unknown":
                same_sector.append(pos_sym)

            if pos_dir == direction:
                same_direction += 1
            else:
                opposite_direction += 1

        # Same sector concentration
        if len(same_sector) >= 2:
            return AgentOpinion(
                agent_name=self.NAME, action=ActionType.SKIP_SIGNAL,
                urgency=Urgency.HIGH, confidence=0.85,
                reason=f"Already have {len(same_sector)} positions in {target_sector}: {same_sector}. Over-concentrated.",
                data={"same_sector": same_sector},
            )

        if len(same_sector) == 1:
            return AgentOpinion(
                agent_name=self.NAME, action=ActionType.TAKE_SIGNAL,
                urgency=Urgency.MEDIUM, confidence=0.60,
                reason=f"Already 1 position in {target_sector} ({same_sector[0]}). Acceptable but watch size.",
                suggested_size_change=0.7,  # Reduce size by 30%
            )

        # All same direction
        total = same_direction + opposite_direction
        if total >= 3 and opposite_direction == 0:
            return AgentOpinion(
                agent_name=self.NAME, action=ActionType.TAKE_SIGNAL,
                urgency=Urgency.MEDIUM, confidence=0.55,
                reason=f"All {total} positions are {direction}. No hedge. Reduce size.",
                suggested_size_change=0.5,
            )

        return AgentOpinion(
            agent_name=self.NAME, action=ActionType.TAKE_SIGNAL,
            urgency=Urgency.LOW, confidence=0.80,
            reason=f"Diversified: {same_direction} same dir, {opposite_direction} opposite, sector {target_sector} clear",
        )
