"""
Position Monitor Agent — the orchestrator that runs ALL agents on open positions.

This is the BRAIN that sits between raw price data and trade management.
Every N bars, it:
1. Runs SectorFlowAgent on the position
2. Runs RegimeAgent on the market
3. Runs ReversalAgent on the stock
4. Collects all opinions
5. Makes a FINAL decision: hold / widen / tighten / close

The decision is REASONED — every opinion contributes, conflicts are resolved,
and the final action has a full audit trail of why.

This is what makes the system an AGENT, not a script.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

from app.agents.base import AgentOpinion, ActionType, Urgency
from app.agents.sector_flow import SectorFlowAgent
from app.agents.regime import RegimeAgent
from app.agents.reversal import ReversalAgent
from app.agents.profit_manager import ProfitManagerAgent, ProfitState
from app.agents.volume_divergence import VolumeDivergenceAgent

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    """Everything about an open position the monitor tracks."""
    symbol: str
    direction: str
    entry_price: float
    entry_bar: int
    entry_regime: str = "unknown"
    current_stop: float = 0.0
    current_target: float = 0.0
    initial_risk: float = 0.0
    max_favorable: float = 0.0
    trail_state: int = 0
    bars_held: int = 0
    current_pnl_pct: float = 0.0
    strategy: str = ""


@dataclass
class MonitorDecision:
    """The monitor's final decision after consulting all agents."""
    action: ActionType
    urgency: Urgency
    new_stop: Optional[float] = None
    new_target: Optional[float] = None
    close_now: bool = False
    reasoning: str = ""
    agent_opinions: List[str] = field(default_factory=list)
    confidence: float = 0.0


class PositionMonitorAgent:
    """
    Orchestrates all sub-agents to make position management decisions.
    """

    def __init__(self):
        self.sector_agent = SectorFlowAgent()
        self.regime_agent = RegimeAgent()
        self.reversal_agent = ReversalAgent()
        self.profit_agent = ProfitManagerAgent()
        self.volume_agent = VolumeDivergenceAgent()
        self._profit_states: Dict[str, ProfitState] = {}

    def evaluate(
        self,
        position: PositionState,
        all_data: Dict[str, List[Dict]],
        date: str,
        current_bar: int,
    ) -> MonitorDecision:
        """
        Run all agents and produce a final decision.
        This is where agent reasoning happens.
        """
        opinions: List[AgentOpinion] = []

        # 1. Sector Flow
        sector_opinion = self.sector_agent.analyze(
            symbol=position.symbol,
            position_direction=position.direction,
            all_data=all_data,
            date=date,
            current_bar=current_bar,
            entry_bar=position.entry_bar,
        )
        opinions.append(sector_opinion)

        # 2. Regime
        regime_opinion = self.regime_agent.analyze(
            all_data=all_data,
            date=date,
            current_bar=current_bar,
            entry_bar=position.entry_bar,
            entry_regime=position.entry_regime,
        )
        opinions.append(regime_opinion)

        # 3. Reversal signals
        reversal_opinion = self.reversal_agent.analyze(
            symbol=position.symbol,
            position_direction=position.direction,
            all_data=all_data,
            date=date,
            current_bar=current_bar,
        )
        opinions.append(reversal_opinion)

        # 4. Volume Divergence — smart money exit detection
        sym_bars_all = [b for b in all_data.get(position.symbol, []) if b["timestamp"][:10] == date]
        vol_opinion = self.volume_agent.analyze(
            symbol=position.symbol,
            position_direction=position.direction,
            bars=sym_bars_all,
            current_bar=current_bar,
        )
        if vol_opinion.action != ActionType.NO_OPINION:
            opinions.append(vol_opinion)

        # 5. Profit Manager — tracks MFE, fade, partial takes
        sym = position.symbol
        if sym not in self._profit_states:
            self._profit_states[sym] = ProfitState(
                entry_price=position.entry_price,
                direction=position.direction,
                initial_risk=position.initial_risk,
            )

        # Get current bar data
        sym_bars = [b for b in all_data.get(sym, []) if b["timestamp"][:10] == date]
        bar_data = sym_bars[current_bar] if current_bar < len(sym_bars) else {}

        self._profit_states[sym] = self.profit_agent.update_state(
            self._profit_states[sym], current_bar, bar_data,
        )
        profit_opinion = self.profit_agent.analyze(self._profit_states[sym])
        opinions.append(profit_opinion)

        # ─── Resolve conflicts and produce final decision ───
        return self._resolve(position, opinions)

    def _resolve(
        self,
        position: PositionState,
        opinions: List[AgentOpinion],
    ) -> MonitorDecision:
        """
        Conflict resolution — how do we handle disagreeing agents?

        Priority order:
        1. CRITICAL + CLOSE_NOW from ANY agent → close (safety first)
        2. Multiple agents agree → follow the consensus
        3. Sector + regime agree → follow them (they see the bigger picture)
        4. Single agent disagrees → note it but follow majority
        """
        briefs = [o.to_brief() for o in opinions]

        # Rule 0: CRITICAL from ANY agent → obey immediately
        critical = [o for o in opinions if o.urgency == Urgency.CRITICAL]
        if critical:
            best = max(critical, key=lambda o: o.confidence)
            close = best.action == ActionType.CLOSE_NOW
            return MonitorDecision(
                action=best.action,
                urgency=Urgency.CRITICAL,
                close_now=close,
                new_stop=best.suggested_stop,
                reasoning=f"CRITICAL: {best.reason}",
                agent_opinions=briefs,
                confidence=best.confidence,
            )

        # Rule 1: Profit manager with HIGH urgency overrides HOLD consensus
        # (it sees the P&L curve — context agents see abstract state)
        profit_opinions = [o for o in opinions if o.agent_name == "profit_manager" and o.urgency in (Urgency.HIGH, Urgency.MEDIUM)]
        if profit_opinions:
            pm = profit_opinions[0]
            if pm.action in (ActionType.TIGHTEN_STOP, ActionType.REDUCE_POSITION, ActionType.CLOSE_NOW):
                return MonitorDecision(
                    action=pm.action,
                    urgency=pm.urgency,
                    new_stop=pm.suggested_stop,
                    close_now=pm.action == ActionType.CLOSE_NOW,
                    reasoning=f"PROFIT MANAGER: {pm.reason}",
                    agent_opinions=briefs,
                    confidence=pm.confidence,
                )

        # Rule 2: Count action votes (weighted by confidence)
        action_scores: Dict[ActionType, float] = {}
        for o in opinions:
            weight = o.confidence
            if o.urgency == Urgency.HIGH:
                weight *= 1.5
            elif o.urgency == Urgency.CRITICAL:
                weight *= 2.0
            action_scores[o.action] = action_scores.get(o.action, 0) + weight

        # Get winning action
        if not action_scores:
            return MonitorDecision(
                action=ActionType.HOLD, urgency=Urgency.LOW,
                reasoning="No opinions", agent_opinions=briefs, confidence=0.5,
            )

        best_action = max(action_scores, key=action_scores.get)
        best_score = action_scores[best_action]
        total_score = sum(action_scores.values())
        consensus = best_score / total_score if total_score > 0 else 0

        # Build the decision
        decision = MonitorDecision(
            action=best_action,
            urgency=self._get_max_urgency(opinions),
            reasoning="",
            agent_opinions=briefs,
            confidence=consensus,
        )

        # Apply stop/target adjustments based on winning action
        if best_action == ActionType.WIDEN_STOP:
            # Widen stop by 50% of initial risk
            if position.direction == "LONG":
                decision.new_stop = position.entry_price - position.initial_risk * 1.5
            else:
                decision.new_stop = position.entry_price + position.initial_risk * 1.5
            decision.reasoning = f"WIDEN STOP to {decision.new_stop:.2f}: sector supporting, give room"

        elif best_action == ActionType.TIGHTEN_STOP:
            # If profitable, lock in half the profit
            if position.current_pnl_pct > 0:
                profit_per_unit = position.max_favorable * 0.5
                if position.direction == "LONG":
                    decision.new_stop = position.entry_price + profit_per_unit
                else:
                    decision.new_stop = position.entry_price - profit_per_unit
                decision.reasoning = f"TIGHTEN STOP to {decision.new_stop:.2f}: lock 50% of max profit"
            else:
                # Not profitable — move to breakeven if possible
                decision.new_stop = position.entry_price
                decision.reasoning = "TIGHTEN to breakeven: conditions weakening"

        elif best_action == ActionType.CLOSE_NOW:
            decision.close_now = True
            reasons = [o.reason for o in opinions if o.action in (ActionType.CLOSE_NOW, ActionType.TIGHTEN_STOP)]
            decision.reasoning = f"CLOSE: {reasons[0] if reasons else 'Multiple concerns'}"

        elif best_action == ActionType.HOLD:
            # Check if we should also adjust stop based on sector
            sector_opinion = next((o for o in opinions if o.agent_name == "sector_flow"), None)
            if sector_opinion and sector_opinion.action == ActionType.WIDEN_STOP:
                decision.action = ActionType.WIDEN_STOP
                if position.direction == "LONG":
                    decision.new_stop = position.entry_price - position.initial_risk * 1.3
                else:
                    decision.new_stop = position.entry_price + position.initial_risk * 1.3
                decision.reasoning = f"HOLD + widen: sector strong ({sector_opinion.reason})"
            else:
                decision.reasoning = "HOLD: conditions stable"

        # Add all opinions to reasoning
        if not decision.reasoning:
            decision.reasoning = f"Consensus: {best_action.value} ({consensus:.0%})"

        return decision

    def _get_max_urgency(self, opinions: List[AgentOpinion]) -> Urgency:
        priority = {Urgency.LOW: 0, Urgency.MEDIUM: 1, Urgency.HIGH: 2, Urgency.CRITICAL: 3}
        max_urg = Urgency.LOW
        for o in opinions:
            if priority.get(o.urgency, 0) > priority.get(max_urg, 0):
                max_urg = o.urgency
        return max_urg
