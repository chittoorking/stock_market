"""
Agent base — all specialized agents implement this interface.

Each agent has ONE job. It receives data, analyzes it, and returns
a typed recommendation. Agents don't execute — they ADVISE.

The orchestrator collects all agent opinions and makes the final call.

Agent types:
- SectorFlowAgent: "Is the sector supporting this trade?"
- RegimeAgent: "What regime are we in? Is it changing?"
- MonitorAgent: "How is this open position doing? Adjust?"
- ReversalAgent: "Are any signals firing AGAINST our position?"
- RiskAgent: "Are we overexposed? Approaching limits?"
- CorrelationAgent: "Are our positions too correlated?"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum


class Urgency(str, Enum):
    LOW = "LOW"           # Informational, no action needed
    MEDIUM = "MEDIUM"     # Consider adjusting
    HIGH = "HIGH"         # Act soon
    CRITICAL = "CRITICAL" # Act NOW


class ActionType(str, Enum):
    HOLD = "HOLD"
    WIDEN_STOP = "WIDEN_STOP"
    TIGHTEN_STOP = "TIGHTEN_STOP"
    MOVE_TARGET = "MOVE_TARGET"
    ADD_TO_POSITION = "ADD_TO_POSITION"
    REDUCE_POSITION = "REDUCE_POSITION"
    CLOSE_NOW = "CLOSE_NOW"
    SKIP_SIGNAL = "SKIP_SIGNAL"
    TAKE_SIGNAL = "TAKE_SIGNAL"
    NO_OPINION = "NO_OPINION"


@dataclass
class AgentOpinion:
    """What one agent thinks about a situation."""
    agent_name: str
    action: ActionType
    urgency: Urgency
    confidence: float = 0.0      # 0-1
    reason: str = ""
    # Optional adjustments
    suggested_stop: Optional[float] = None
    suggested_target: Optional[float] = None
    suggested_size_change: Optional[float] = None  # Multiplier: 0.5 = halve, 2.0 = double
    data: Dict[str, Any] = field(default_factory=dict)

    def to_brief(self) -> str:
        return f"[{self.agent_name}] {self.action.value} ({self.urgency.value}, {self.confidence:.0%}): {self.reason}"
