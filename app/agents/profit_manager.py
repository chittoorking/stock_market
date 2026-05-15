"""
Profit Manager Agent — the missing piece.

Most trading bots have two modes: HOLD or STOP OUT.
That's binary thinking. Real traders take PARTIAL profits.

This agent tracks the profit curve of each position and answers:
- "Am I giving back too much of what I earned?"
- "Should I take some off the table now?"
- "Should I let the rest run with a tight trail?"

Profit Zones (based on MFE — Maximum Favorable Excursion):

  Zone 0: Underwater (P&L < 0)
    → No profit to manage. Risk agent handles this.

  Zone 1: Scratch (+0.00% to +0.15%)
    → Barely profitable. Don't trail yet. Let it breathe.

  Zone 2: Small Win (+0.15% to +0.40%)
    → Move stop to breakeven. Protect entry capital.

  Zone 3: Decent Win (+0.40% to +0.75%)
    → Take 30% off. Trail rest at +0.15%.

  Zone 4: Strong Win (+0.75%+)
    → Take another 30% off. Trail remaining at +0.40%.
    → If MFE fades 50% from peak → close remaining.

Profit Fade Detection:
  The key insight: it's not about WHERE the price is, it's about
  WHERE IT WAS vs WHERE IT IS NOW.

  If MFE was +0.70% and current P&L is +0.20%, you've given back 71%.
  That's not "still profitable" — that's a position screaming at you
  that momentum died.

  Fade thresholds:
  - 30% fade from MFE → flag (watch closely)
  - 50% fade from MFE → tighten aggressively
  - 75% fade from MFE → close remaining
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from app.agents.base import AgentOpinion, ActionType, Urgency

logger = logging.getLogger(__name__)


@dataclass
class ProfitState:
    """Tracks the profit curve of a position."""
    entry_price: float = 0.0
    direction: str = "LONG"
    initial_risk: float = 0.0

    # Live tracking
    current_price: float = 0.0
    current_pnl_pct: float = 0.0
    mfe: float = 0.0          # Maximum favorable excursion (price)
    mfe_pct: float = 0.0      # MFE as percentage
    mfe_bar: int = 0          # When was MFE hit
    bars_since_mfe: int = 0   # How long since peak
    fade_pct: float = 0.0     # How much of MFE has been given back (0-100%)

    # Profit zone
    zone: int = 0
    highest_zone: int = 0     # Peak zone reached

    # Partial takes
    partial_1_taken: bool = False  # 30% at zone 3
    partial_2_taken: bool = False  # 30% at zone 4
    remaining_pct: float = 1.0    # What fraction of position remains


class ProfitManagerAgent:
    """
    Manages profit taking and fade detection for open positions.
    """
    NAME = "profit_manager"

    # Zone boundaries (% of entry)
    ZONE_1_MIN = 0.00
    ZONE_2_MIN = 0.15
    ZONE_3_MIN = 0.40
    ZONE_4_MIN = 0.75

    # Partial take amounts
    PARTIAL_1_PCT = 0.30   # Take 30% at zone 3
    PARTIAL_2_PCT = 0.30   # Take 30% at zone 4 (60% total, 40% runner)

    # Fade thresholds
    FADE_WATCH = 0.30      # 30% fade → flag
    FADE_TIGHTEN = 0.50    # 50% fade → tighten hard
    FADE_CLOSE = 0.75      # 75% fade → close remaining

    # Minimum MFE to care about fading (don't fade-close a +0.05% peak)
    MIN_MFE_FOR_FADE = 0.20

    def update_state(self, state: ProfitState, current_bar: int, bar_data: Dict) -> ProfitState:
        """Update profit state with latest bar data."""
        price = bar_data.get("close", state.current_price)
        high = bar_data.get("high", price)
        low = bar_data.get("low", price)

        state.current_price = price

        # Calculate P&L
        if state.direction == "LONG":
            state.current_pnl_pct = (price - state.entry_price) / state.entry_price * 100
            favorable_extreme = high
        else:
            state.current_pnl_pct = (state.entry_price - price) / state.entry_price * 100
            favorable_extreme = state.entry_price - (low - state.entry_price)  # Mirror for short

        # Update MFE
        if state.direction == "LONG":
            new_mfe = max(state.mfe, high - state.entry_price)
        else:
            new_mfe = max(state.mfe, state.entry_price - low)

        if new_mfe > state.mfe:
            state.mfe = new_mfe
            state.mfe_pct = new_mfe / state.entry_price * 100
            state.mfe_bar = current_bar
            state.bars_since_mfe = 0
        else:
            state.bars_since_mfe += 1

        # Calculate fade
        if state.mfe > 0:
            current_favorable = price - state.entry_price if state.direction == "LONG" else state.entry_price - price
            profit_given_back = state.mfe - max(0, current_favorable)
            state.fade_pct = (profit_given_back / state.mfe) * 100 if state.mfe > 0 else 0
        else:
            state.fade_pct = 0

        # Update zone
        pnl = state.current_pnl_pct
        if pnl >= self.ZONE_4_MIN:
            state.zone = 4
        elif pnl >= self.ZONE_3_MIN:
            state.zone = 3
        elif pnl >= self.ZONE_2_MIN:
            state.zone = 2
        elif pnl >= self.ZONE_1_MIN:
            state.zone = 1
        else:
            state.zone = 0

        state.highest_zone = max(state.highest_zone, state.zone)

        return state

    def analyze(self, state: ProfitState) -> AgentOpinion:
        """
        Analyze the profit state and recommend action.
        """
        pnl = state.current_pnl_pct
        mfe_pct = state.mfe_pct
        fade = state.fade_pct
        zone = state.zone
        highest = state.highest_zone

        # ─── Fade Detection (most important check) ───

        if mfe_pct >= self.MIN_MFE_FOR_FADE and fade >= self.FADE_CLOSE * 100:
            # Given back 75%+ of peak profit
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.CLOSE_NOW,
                urgency=Urgency.CRITICAL,
                confidence=0.90,
                reason=(
                    f"PROFIT COLLAPSE: MFE was +{mfe_pct:.2f}%, now +{pnl:.2f}% "
                    f"(gave back {fade:.0f}%). {state.bars_since_mfe} bars since peak. "
                    f"Momentum is dead — close remaining {state.remaining_pct:.0%}."
                ),
                data={"mfe_pct": mfe_pct, "fade_pct": fade, "zone": zone, "action": "close_all"},
            )

        if mfe_pct >= self.MIN_MFE_FOR_FADE and fade >= self.FADE_TIGHTEN * 100:
            # Given back 50%+ of peak profit
            # Lock in remaining profit
            if state.direction == "LONG":
                lock_price = state.entry_price + state.mfe * 0.25  # Lock 25% of MFE
            else:
                lock_price = state.entry_price - state.mfe * 0.25

            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.TIGHTEN_STOP,
                urgency=Urgency.HIGH,
                confidence=0.85,
                reason=(
                    f"PROFIT FADING: MFE was +{mfe_pct:.2f}%, now +{pnl:.2f}% "
                    f"(gave back {fade:.0f}%). Tighten stop to lock +{state.mfe * 0.25 / state.entry_price * 100:.2f}%."
                ),
                suggested_stop=round(lock_price, 2),
                data={"mfe_pct": mfe_pct, "fade_pct": fade, "lock_price": lock_price},
            )

        if mfe_pct >= self.MIN_MFE_FOR_FADE and fade >= self.FADE_WATCH * 100:
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.HOLD,
                urgency=Urgency.MEDIUM,
                confidence=0.60,
                reason=(
                    f"Profit fading: MFE +{mfe_pct:.2f}% -> current +{pnl:.2f}% "
                    f"(gave back {fade:.0f}%). Watching closely."
                ),
                data={"mfe_pct": mfe_pct, "fade_pct": fade},
            )

        # ─── Zone-Based Partial Profit Taking ───

        if zone >= 4 and not state.partial_2_taken:
            # Strong win — take second partial
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.REDUCE_POSITION,
                urgency=Urgency.MEDIUM,
                confidence=0.80,
                reason=(
                    f"Zone 4 (+{pnl:.2f}%): Take {self.PARTIAL_2_PCT:.0%} profit. "
                    f"Running {(1 - self.PARTIAL_1_PCT - self.PARTIAL_2_PCT):.0%} with tight trail."
                ),
                suggested_size_change=1.0 - self.PARTIAL_2_PCT,
                data={"zone": 4, "partial": 2, "take_pct": self.PARTIAL_2_PCT},
            )

        if zone >= 3 and not state.partial_1_taken:
            # Decent win — take first partial
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.REDUCE_POSITION,
                urgency=Urgency.MEDIUM,
                confidence=0.75,
                reason=(
                    f"Zone 3 (+{pnl:.2f}%): Take {self.PARTIAL_1_PCT:.0%} off the table. "
                    f"Running {(1 - self.PARTIAL_1_PCT):.0%} with breakeven stop."
                ),
                suggested_size_change=1.0 - self.PARTIAL_1_PCT,
                suggested_stop=state.entry_price,  # Move to breakeven on remaining
                data={"zone": 3, "partial": 1, "take_pct": self.PARTIAL_1_PCT},
            )

        if zone >= 2 and highest >= 2:
            # Small win — move to breakeven
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.TIGHTEN_STOP,
                urgency=Urgency.LOW,
                confidence=0.65,
                reason=f"Zone 2 (+{pnl:.2f}%): Move stop to breakeven to protect entry.",
                suggested_stop=state.entry_price,
                data={"zone": 2},
            )

        # ─── Zone 0-1: Too early to manage profit ───

        if zone == 0 and highest >= 2:
            # Was profitable, now underwater — something changed
            return AgentOpinion(
                agent_name=self.NAME,
                action=ActionType.TIGHTEN_STOP,
                urgency=Urgency.HIGH,
                confidence=0.75,
                reason=(
                    f"Was in zone {highest} (profitable), now underwater ({pnl:+.2f}%). "
                    f"Protect remaining capital."
                ),
                data={"zone": 0, "highest_zone": highest},
            )

        return AgentOpinion(
            agent_name=self.NAME,
            action=ActionType.HOLD,
            urgency=Urgency.LOW,
            confidence=0.50,
            reason=f"Zone {zone}: P&L +{pnl:.2f}%, MFE +{mfe_pct:.2f}%. No action needed yet.",
            data={"zone": zone, "pnl": pnl, "mfe": mfe_pct},
        )
