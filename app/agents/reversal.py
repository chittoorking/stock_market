"""
Reversal Agent — watches for signals AGAINST open positions.

Answers: "Is something firing against my position right now?"

This is the agent that saves you from holding a losing position when
the market has already told you it's wrong. Instead of waiting for
stop loss, this detects:
- A strategy signal in the OPPOSITE direction on the same stock
- A cascade firing against your sector
- Volume spike against your position (institutional selling)
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional

from app.agents.base import AgentOpinion, ActionType, Urgency
from app.signals.proven_strategies import LenzSignal, AftershockSignal
from app.signals.base import get_sector, SECTOR_MAP


class ReversalAgent:
    NAME = "reversal"

    def analyze(
        self,
        symbol: str,
        position_direction: str,
        all_data: Dict[str, List[Dict]],
        date: str,
        current_bar: int,
    ) -> AgentOpinion:
        """Check if any reversal signals are firing against our position."""
        bars = [b for b in all_data.get(symbol, []) if b["timestamp"][:10] == date]
        if not bars or len(bars) <= current_bar or current_bar < 2:
            return AgentOpinion(agent_name=self.NAME, action=ActionType.NO_OPINION,
                                urgency=Urgency.LOW, reason="Insufficient data")

        bars_so_far = bars[:current_bar + 1]
        reversal_signals = []

        # Check LNZ3 (weak opposition → continuation in opposite direction)
        sig = LenzSignal.scan(symbol, bars_so_far)
        if sig and sig.direction != position_direction:
            reversal_signals.append(f"LNZ3 {sig.direction} (drive {sig.signal_reason[:40]})")

        # Check AFT7 (aftershock in opposite direction)
        sig = AftershockSignal.scan(symbol, bars_so_far)
        if sig and sig.direction != position_direction:
            reversal_signals.append(f"AFT7 {sig.direction} ({sig.signal_reason[:40]})")

        # Check volume spike against position
        if current_bar >= 2:
            curr_bar = bars[current_bar]
            prev_bar = bars[current_bar - 1]
            avg_vol = sum(b["volume"] for b in bars[:current_bar]) / current_bar if current_bar > 0 else 1
            vol_ratio = curr_bar["volume"] / avg_vol if avg_vol > 0 else 1

            bar_move = (curr_bar["close"] - curr_bar["open"]) / curr_bar["open"] * 100
            is_against = (position_direction == "LONG" and bar_move < -0.3 and vol_ratio > 2.0) or \
                         (position_direction == "SHORT" and bar_move > 0.3 and vol_ratio > 2.0)

            if is_against:
                reversal_signals.append(f"Volume spike AGAINST ({vol_ratio:.1f}x avg, move {bar_move:+.2f}%)")

        # Check sector leader reversing
        sector = get_sector(symbol)
        sector_info = SECTOR_MAP.get(sector, {})
        leader = sector_info.get("leader", "")
        if leader and leader != symbol:
            leader_bars = [b for b in all_data.get(leader, []) if b["timestamp"][:10] == date]
            if leader_bars and len(leader_bars) > current_bar and current_bar >= 2:
                leader_recent = leader_bars[current_bar]
                leader_prev = leader_bars[current_bar - 2]
                leader_move = (leader_recent["close"] - leader_prev["close"]) / leader_prev["close"] * 100

                if (position_direction == "LONG" and leader_move < -0.5) or \
                   (position_direction == "SHORT" and leader_move > 0.5):
                    reversal_signals.append(f"Sector leader {leader} moving against ({leader_move:+.2f}% in 2 bars)")

        if not reversal_signals:
            return AgentOpinion(
                agent_name=self.NAME, action=ActionType.HOLD, urgency=Urgency.LOW,
                confidence=0.60, reason="No reversal signals detected",
            )

        if len(reversal_signals) >= 2:
            return AgentOpinion(
                agent_name=self.NAME, action=ActionType.CLOSE_NOW, urgency=Urgency.CRITICAL,
                confidence=0.85,
                reason=f"MULTIPLE reversal signals: {' + '.join(reversal_signals)}",
                data={"signals": reversal_signals},
            )

        return AgentOpinion(
            agent_name=self.NAME, action=ActionType.TIGHTEN_STOP, urgency=Urgency.HIGH,
            confidence=0.70,
            reason=f"Reversal signal: {reversal_signals[0]}",
            data={"signals": reversal_signals},
        )
