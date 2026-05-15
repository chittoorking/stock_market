"""
Sector Flow Agent — monitors sector health in real-time.

Answers: "Is the sector supporting my trade right now?"

What it tracks:
- Leader direction and magnitude
- How many laggards are following (and who's diverging)
- Sector momentum: accelerating or decelerating?
- Cross-sector rotation (money leaving IT → entering metals)

What it recommends:
- Sector accelerating + position in direction → WIDEN_STOP (let it run)
- Sector decelerating → TIGHTEN_STOP (protect profits)
- Sector reversed → CLOSE_NOW
- Your stock lagging its sector → patience, but reduce target
- Your stock leading its sector → tighten trail, take what you have
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional

from app.agents.base import AgentOpinion, ActionType, Urgency
from app.signals.base import SECTOR_MAP, get_sector


class SectorFlowAgent:
    NAME = "sector_flow"

    def analyze(
        self,
        symbol: str,
        position_direction: str,
        all_data: Dict[str, List[Dict]],
        date: str,
        current_bar: int,
        entry_bar: int,
    ) -> AgentOpinion:
        """Analyze sector health for a specific position."""
        sector = get_sector(symbol)
        sector_info = SECTOR_MAP.get(sector)

        if not sector_info:
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.NO_OPINION,
                urgency=Urgency.LOW,
                reason=f"Unknown sector for {symbol}",
            )

        leader = sector_info["leader"]
        laggards = sector_info["laggards"]

        # Get leader's current flow
        leader_bars = [b for b in all_data.get(leader, []) if b["timestamp"][:10] == date]
        if not leader_bars or len(leader_bars) <= current_bar:
            return AgentOpinion(agent_name=self.NAME, action=ActionType.NO_OPINION,
                                urgency=Urgency.LOW, reason="No leader data")

        leader_open = leader_bars[0]["open"]
        leader_now = leader_bars[current_bar]["close"]
        leader_change = (leader_now - leader_open) / leader_open * 100

        # Leader at entry time
        leader_at_entry = leader_bars[entry_bar]["close"] if entry_bar < len(leader_bars) else leader_open
        leader_change_at_entry = (leader_at_entry - leader_open) / leader_open * 100

        # Sector momentum: is it accelerating or decelerating?
        momentum = leader_change - leader_change_at_entry
        is_accelerating = (position_direction == "LONG" and momentum > 0.1) or \
                          (position_direction == "SHORT" and momentum < -0.1)
        is_decelerating = (position_direction == "LONG" and momentum < -0.1) or \
                          (position_direction == "SHORT" and momentum > 0.1)
        is_reversed = (position_direction == "LONG" and leader_change < -0.2) or \
                      (position_direction == "SHORT" and leader_change > 0.2)

        # How many laggards still following?
        following = 0
        diverging = []
        for lag in laggards:
            lag_bars = [b for b in all_data.get(lag, []) if b["timestamp"][:10] == date]
            if lag_bars and len(lag_bars) > current_bar:
                lag_change = (lag_bars[current_bar]["close"] - lag_bars[0]["open"]) / lag_bars[0]["open"] * 100
                if (leader_change > 0 and lag_change > 0) or (leader_change < 0 and lag_change < 0):
                    following += 1
                else:
                    diverging.append(lag)

        strength = following / len(laggards) if laggards else 0

        # Is our stock leading or lagging its sector?
        our_bars = [b for b in all_data.get(symbol, []) if b["timestamp"][:10] == date]
        our_change = 0
        if our_bars and len(our_bars) > current_bar:
            our_change = (our_bars[current_bar]["close"] - our_bars[0]["open"]) / our_bars[0]["open"] * 100

        stock_vs_sector = our_change - leader_change

        # ─── Decision ───
        if is_reversed:
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.CLOSE_NOW,
                urgency=Urgency.CRITICAL,
                confidence=0.85,
                reason=f"Sector {sector} REVERSED: leader {leader} now {leader_change:+.2f}% (was {leader_change_at_entry:+.2f}% at entry)",
                data={"leader_change": leader_change, "strength": strength},
            )

        if is_decelerating and strength < 0.5:
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.TIGHTEN_STOP,
                urgency=Urgency.HIGH,
                confidence=0.75,
                reason=f"Sector {sector} fading: {leader} {leader_change:+.2f}% (momentum {momentum:+.2f}%), only {following}/{len(laggards)} following",
                data={"leader_change": leader_change, "strength": strength, "momentum": momentum},
            )

        if is_accelerating and strength >= 0.75:
            # Our stock lagging sector? → patience, widen stop
            if stock_vs_sector < -0.3:
                return AgentOpinion(
                    agent_name=self.NAME,
                    action=ActionType.WIDEN_STOP,
                    urgency=Urgency.MEDIUM,
                    confidence=0.80,
                    reason=f"Sector {sector} ACCELERATING ({leader} {leader_change:+.2f}%, {strength:.0%} following) but {symbol} lagging by {stock_vs_sector:+.2f}%. Give room to catch up.",
                    data={"leader_change": leader_change, "strength": strength, "stock_vs_sector": stock_vs_sector},
                )
            else:
                return AgentOpinion(
                    agent_name=self.NAME,
                    action=ActionType.HOLD,
                    urgency=Urgency.LOW,
                    confidence=0.80,
                    reason=f"Sector {sector} strong: {leader} {leader_change:+.2f}%, {strength:.0%} following, momentum {momentum:+.2f}%",
                    data={"leader_change": leader_change, "strength": strength},
                )

        # Default: sector neutral
        return AgentOpinion(
            agent_name=self.NAME,
            action=ActionType.HOLD,
            urgency=Urgency.LOW,
            confidence=0.50,
            reason=f"Sector {sector} neutral: {leader} {leader_change:+.2f}%, {following}/{len(laggards)} following",
            data={"leader_change": leader_change, "strength": strength},
        )
