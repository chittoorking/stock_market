"""
Signal provider base — all proven strategies implement this interface.

KEY DESIGN: Signals are DATA, not DECISIONS.
- A signal says "GG8 sees a gap-down continuation setup on TATAMOTORS"
- The AGENT decides whether to trade it (considering portfolio, regime, RL confidence, other signals)
- The RL engine tracks which signals actually made money and adjusts trust over time

This prevents:
1. Blind strategy following (a script can do that)
2. ML model bias (signals are inputs, not outputs)
3. Strategy overfitting (RL decays trust when a strategy stops working)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from enum import Enum
import uuid


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalUrgency(str, Enum):
    """How time-sensitive is this signal?"""
    IMMEDIATE = "IMMEDIATE"    # Execute within 1-2 bars or signal dies
    STANDARD = "STANDARD"      # Good for next 15-30 minutes
    PATIENT = "PATIENT"        # Swing — good for hours/days


@dataclass
class StrategySignal:
    """
    A signal from a proven strategy. This is DATA for the agent, not a command.

    The agent sees this and thinks:
    "GG8 says short TATAMOTORS with 0.30% stop. But do I agree?
     - My portfolio already has 2 short positions (correlation risk)
     - RL says GG8 has 58% accuracy in volatile regime (decent)
     - Cascade also fired on TATAMOTORS (confluence)
     → I'll take it, but at 60% of suggested size because of correlation"
    """
    # Identity
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    strategy_name: str = ""          # "gg8", "orb", "cascade_y5", "lnz3", etc.

    # What the signal says
    symbol: str = ""
    direction: str = ""              # LONG or SHORT
    urgency: str = "STANDARD"

    # Suggested trade parameters (the agent can override ANY of these)
    suggested_entry: float = 0.0
    suggested_stop: float = 0.0
    suggested_target: float = 0.0
    risk_reward_ratio: float = 0.0
    risk_pct: float = 0.0           # Stop distance as % of entry

    # Signal quality metrics (raw, no RL adjustment)
    raw_confidence: float = 0.0      # Strategy's own confidence (0-1)
    rvol: float = 0.0               # Relative volume at signal time
    vwap_aligned: bool = False       # Is price on the right side of VWAP?

    # Context the agent uses for reasoning
    signal_reason: str = ""          # Human-readable: "Gap down 1.2%, 1-bar confirm, RVOL 2.8x"
    market_regime: str = ""          # "trending_up", "trending_down", "ranging", "volatile"
    timeframe: str = "5m"
    sector: str = ""                 # "banks", "it", "pharma", etc.

    # Metadata
    triggered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_bars: int = 36           # Signal expires after N bars
    bar_index: int = 0               # Which bar triggered this

    # For tracking — filled by the system after the fact
    was_taken: bool = False
    actual_pnl: Optional[float] = None
    actual_pnl_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_agent_summary(self) -> str:
        """Format for the LLM agent's context — concise, data-rich."""
        dir_emoji = "📈" if self.direction == "LONG" else "📉"
        return (
            f"{dir_emoji} [{self.strategy_name.upper()}] {self.direction} {self.symbol} "
            f"| Entry ₹{self.suggested_entry:.2f} | Stop ₹{self.suggested_stop:.2f} "
            f"| Target ₹{self.suggested_target:.2f} | RR {self.risk_reward_ratio:.1f} "
            f"| RVOL {self.rvol:.1f}x | Confidence {self.raw_confidence:.0%}\n"
            f"  Reason: {self.signal_reason}"
        )


# ─── Sector Definitions (from proven research) ───

SECTOR_MAP = {
    "banks": {
        "leader": "HDFCBANK",
        "laggards": ["ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK"],
    },
    "it": {
        "leader": "TCS",
        "laggards": ["INFY", "WIPRO", "HCLTECH", "TECHM"],
    },
    "pharma": {
        "leader": "SUNPHARMA",
        "laggards": ["CIPLA", "DIVISLAB", "APOLLOHOSP"],
    },
    "metals": {
        "leader": "TATASTEEL",
        "laggards": ["JSWSTEEL", "HINDALCO", "COALINDIA"],
    },
    "energy": {
        "leader": "RELIANCE",
        "laggards": ["ONGC", "BPCL", "NTPC", "POWERGRID"],
    },
    "auto": {
        "leader": "MARUTI",
        "laggards": ["TATAMOTORS", "EICHERMOT", "HEROMOTOCO", "BAJFINANCE"],
    },
    "fmcg": {
        "leader": "HINDUNILVR",
        "laggards": ["ITC", "NESTLEIND", "BRITANNIA", "TATACONSUM"],
    },
}

SYMBOL_TO_SECTOR = {}
for sector, info in SECTOR_MAP.items():
    SYMBOL_TO_SECTOR[info["leader"]] = sector
    for lag in info["laggards"]:
        SYMBOL_TO_SECTOR[lag] = sector


def get_sector(symbol: str) -> str:
    return SYMBOL_TO_SECTOR.get(symbol.upper(), "unknown")


def is_sector_leader(symbol: str) -> bool:
    for info in SECTOR_MAP.values():
        if info["leader"] == symbol.upper():
            return True
    return False


def get_laggards(symbol: str) -> List[str]:
    for info in SECTOR_MAP.values():
        if info["leader"] == symbol.upper():
            return info["laggards"]
    return []
