"""
Volume Divergence Agent — watches for smart money exiting.

This is NOT a signal generator. It's a POSITION MANAGEMENT agent.
It watches open positions for volume-price divergence:

  Price rising + volume falling = institutions selling into strength.
  Price falling + volume falling = selling exhausted, might bounce.

From data analysis: 41 divergence events found. Not useful as entry signal
(50/50 standalone) but CRITICAL as exit signal:
- If your LONG position shows rising price + falling volume → TIGHTEN
- If your SHORT position shows falling price + falling volume → TIGHTEN
  (selling exhaustion = bounce coming)
"""
from __future__ import annotations

from typing import Dict, Any, List
from app.agents.base import AgentOpinion, ActionType, Urgency


class VolumeDivergenceAgent:
    """Watches for volume-price divergence on open positions."""
    NAME = "volume_divergence"

    VOL_DROP_THRESHOLD = 0.30   # Volume must drop >30% over window
    WINDOW_BARS = 6             # Check over 6-bar (30 min) window

    def analyze(
        self,
        symbol: str,
        position_direction: str,
        bars: List[Dict],
        current_bar: int,
    ) -> AgentOpinion:
        if current_bar < self.WINDOW_BARS + 1 or current_bar >= len(bars):
            return AgentOpinion(
                agent_name=self.NAME, action=ActionType.NO_OPINION,
                urgency=Urgency.LOW, reason="Not enough bars for volume analysis",
            )

        window = bars[current_bar - self.WINDOW_BARS:current_bar + 1]

        prices = [b["close"] for b in window]
        volumes = [b["volume"] for b in window]

        price_change = prices[-1] - prices[0]
        price_direction = "up" if price_change > 0 else "down"

        # Volume trend
        vol_start = sum(volumes[:3]) / 3  # First half avg
        vol_end = sum(volumes[-3:]) / 3   # Second half avg
        vol_change = (vol_end - vol_start) / vol_start if vol_start > 0 else 0

        # Divergence: price up + volume down
        if price_direction == "up" and vol_change < -self.VOL_DROP_THRESHOLD:
            if position_direction == "LONG":
                # We're long, price rising but volume dying
                return AgentOpinion(
                    agent_name=self.NAME,
                    action=ActionType.TIGHTEN_STOP,
                    urgency=Urgency.HIGH,
                    confidence=0.75,
                    reason=(
                        f"VOLUME DIVERGENCE: price rising but volume dropped {vol_change*100:.0f}% "
                        f"in {self.WINDOW_BARS} bars. Smart money exiting. Tighten stop."
                    ),
                    data={"vol_change": vol_change, "price_change": price_change},
                )
            elif position_direction == "SHORT":
                # We're short, price rising against us WITH declining volume
                # Weak rally — might be ok to hold
                return AgentOpinion(
                    agent_name=self.NAME,
                    action=ActionType.HOLD,
                    urgency=Urgency.LOW,
                    confidence=0.60,
                    reason=f"Price against us but volume weak ({vol_change*100:.0f}%). Rally may fade.",
                )

        if price_direction == "down" and vol_change < -self.VOL_DROP_THRESHOLD:
            if position_direction == "SHORT":
                # We're short, price falling but selling volume dying
                return AgentOpinion(
                    agent_name=self.NAME,
                    action=ActionType.TIGHTEN_STOP,
                    urgency=Urgency.MEDIUM,
                    confidence=0.65,
                    reason=(
                        f"Selling exhaustion: price falling but volume dropped {vol_change*100:.0f}%. "
                        f"Bounce likely. Tighten stop."
                    ),
                    data={"vol_change": vol_change},
                )

        return AgentOpinion(
            agent_name=self.NAME, action=ActionType.NO_OPINION,
            urgency=Urgency.LOW, confidence=0.50,
            reason=f"No divergence: price {price_direction}, vol {vol_change*100:+.0f}%",
        )
