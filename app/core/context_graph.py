"""
Market Context Graph — the agent's memory of everything happening in the market.

This is NOT a generic graph database. It's a purpose-built context tracker that answers:
- "What do I know about RELIANCE right now?" (signals, regime, positions, sector flow)
- "What happened last time cascade fired on banks?" (signal history → outcome)
- "Am I overexposed to any sector?" (correlation tracking)
- "Has the regime changed since my last trade?" (regime transition tracking)
- "Which signals had confluence today?" (cross-strategy correlation)

Structure:
    SymbolNode ──[sector]──→ SectorNode
        │                        │
        ├── signals[]            ├── leader_flow (direction, strength)
        ├── regime               ├── laggard_status[]
        ├── positions[]          ├── cascade_count_today
        ├── recent_trades[]      └── regime
        ├── vwap_position
        ├── rvol
        └── correlations{}

    MarketNode (NIFTY / global)
        ├── regime
        ├── institutional_flow
        ├── vix / volatility
        └── affects → all SymbolNodes

Everything persists in Redis with TTLs. The graph rebuilds itself from live data
on restart — no cold start problem.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict

from app.core.redis import redis_get, redis_set, redis_delete, get_redis_client
from app.signals.base import get_sector, SECTOR_MAP

logger = logging.getLogger(__name__)

TTL_INTRADAY = 28800     # 8 hours (one trading day)
TTL_HISTORY = 86400 * 7  # 7 days for signal/trade history
TTL_REGIME = 3600        # 1 hour (regime can change)


@dataclass
class SignalRecord:
    """A signal that was generated, with its outcome (if known)."""
    signal_id: str
    strategy: str
    symbol: str
    direction: str
    confidence: float
    score: float  # Aggregator's final score
    timestamp: str
    was_taken: bool = False
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    regime_at_signal: str = ""
    confluence_with: List[str] = field(default_factory=list)


@dataclass
class SymbolContext:
    """Everything the agent knows about a single symbol RIGHT NOW."""
    symbol: str
    sector: str = ""

    # Current market state
    price: float = 0.0
    vwap: float = 0.0
    vwap_position: str = ""  # "above" or "below"
    rvol: float = 0.0
    regime: str = "unknown"
    regime_since: str = ""  # When did this regime start

    # Active signals (not yet expired)
    active_signals: List[Dict] = field(default_factory=list)
    signal_count_today: int = 0

    # Open position (if any)
    has_position: bool = False
    position_side: str = ""
    position_entry: float = 0.0
    position_pnl: float = 0.0
    position_bars_held: int = 0

    # Recent trade history (last 7 days)
    recent_trades: List[Dict] = field(default_factory=list)
    win_rate_recent: float = 0.0
    avg_pnl_recent: float = 0.0

    # Strategy performance on this symbol
    strategy_scores: Dict[str, float] = field(default_factory=dict)
    # e.g., {"gg8": 0.72, "lnz3": 0.45} — how well each strategy works HERE

    # Correlations with other symbols
    correlations: Dict[str, float] = field(default_factory=dict)


@dataclass
class SectorContext:
    """Sector-level awareness."""
    sector: str
    leader: str = ""
    leader_direction: str = ""   # Current flow direction
    leader_move_pct: float = 0.0
    laggard_following: int = 0   # How many laggards are following
    laggard_total: int = 0
    cascade_count_today: int = 0
    last_cascade_bar: int = 0
    regime: str = "unknown"
    strength: float = 0.0        # 0-1, how strong is sector flow


@dataclass
class MarketContext:
    """Global market awareness — NIFTY, VIX, overall flow."""
    regime: str = "unknown"
    regime_since: str = ""
    nifty_change_pct: float = 0.0
    institutional_flow: str = "neutral"  # "buying", "selling", "neutral"
    volatility_level: str = "normal"     # "low", "normal", "high", "extreme"
    time_of_day: str = ""                # "morning_open", "morning", "midday", "afternoon", "close"
    signals_generated_today: int = 0
    trades_executed_today: int = 0
    daily_pnl: float = 0.0


class ContextGraph:
    """
    The market context graph. Stores everything in Redis, queryable in real-time.
    The agent's system prompt includes a snapshot of this graph.
    """

    def __init__(self, session_id: str = "auto"):
        self.session_id = session_id

    # ─── Symbol Context ───

    async def update_symbol(self, symbol: str, data: Dict[str, Any]):
        """Update a symbol's context from live market data."""
        key = f"ctx:symbol:{self.session_id}:{symbol}"
        existing = await self._load_symbol(symbol)

        # Merge new data
        existing.price = data.get("price", data.get("close", existing.price))
        existing.vwap = data.get("vwap", existing.vwap)
        existing.vwap_position = "above" if existing.price > existing.vwap else "below"
        existing.rvol = data.get("rvol", existing.rvol)
        existing.sector = get_sector(symbol)

        # Regime tracking with transition detection
        new_regime = data.get("regime", existing.regime)
        if new_regime != existing.regime:
            existing.regime_since = datetime.now(timezone.utc).isoformat()
            logger.info("Regime change: %s %s → %s", symbol, existing.regime, new_regime)
        existing.regime = new_regime

        await redis_set(key, json.dumps(asdict(existing)), ttl=TTL_INTRADAY)

    async def get_symbol(self, symbol: str) -> SymbolContext:
        return await self._load_symbol(symbol)

    async def _load_symbol(self, symbol: str) -> SymbolContext:
        key = f"ctx:symbol:{self.session_id}:{symbol}"
        raw = await redis_get(key)
        if raw:
            try:
                return SymbolContext(**json.loads(raw))
            except Exception:
                pass
        return SymbolContext(symbol=symbol, sector=get_sector(symbol))

    # ─── Signal History ───

    async def record_signal(self, signal_record: SignalRecord):
        """Record a signal that was generated (taken or not)."""
        client = await get_redis_client()

        # Add to symbol's signal history
        key = f"ctx:signals:{self.session_id}:{signal_record.symbol}"
        await client.rpush(key, json.dumps(asdict(signal_record)))
        await client.expire(key, TTL_HISTORY)

        # Update symbol's active signals
        ctx = await self._load_symbol(signal_record.symbol)
        ctx.active_signals.append({
            "id": signal_record.signal_id,
            "strategy": signal_record.strategy,
            "direction": signal_record.direction,
            "confidence": signal_record.confidence,
        })
        ctx.signal_count_today += 1
        await redis_set(
            f"ctx:symbol:{self.session_id}:{signal_record.symbol}",
            json.dumps(asdict(ctx)), ttl=TTL_INTRADAY,
        )

        # Global counter
        mkt = await self.get_market()
        mkt.signals_generated_today += 1
        await self._save_market(mkt)

    async def record_signal_outcome(self, symbol: str, signal_id: str, pnl: float, pnl_pct: float):
        """Update a signal's outcome after the trade closes."""
        client = await get_redis_client()
        key = f"ctx:signals:{self.session_id}:{symbol}"
        raw_list = await client.lrange(key, 0, -1)

        for i, raw in enumerate(raw_list):
            record = json.loads(raw)
            if record.get("signal_id") == signal_id:
                record["pnl"] = pnl
                record["pnl_pct"] = pnl_pct
                await client.lset(key, i, json.dumps(record))
                break

    async def get_signal_history(self, symbol: str, limit: int = 20) -> List[SignalRecord]:
        """Get recent signal history for a symbol."""
        client = await get_redis_client()
        key = f"ctx:signals:{self.session_id}:{symbol}"
        raw_list = await client.lrange(key, -limit, -1)
        return [SignalRecord(**json.loads(r)) for r in raw_list]

    # ─── Position Tracking ───

    async def update_position(self, symbol: str, position: Optional[Dict]):
        """Update position tracking for a symbol."""
        ctx = await self._load_symbol(symbol)
        if position:
            ctx.has_position = True
            ctx.position_side = position.get("side", "")
            ctx.position_entry = position.get("avg_entry_price", 0)
            ctx.position_pnl = position.get("unrealized_pnl", 0)
            ctx.position_bars_held = position.get("bars_held", 0)
        else:
            ctx.has_position = False
            ctx.position_side = ""
            ctx.position_entry = 0
            ctx.position_pnl = 0
            ctx.position_bars_held = 0

        await redis_set(
            f"ctx:symbol:{self.session_id}:{symbol}",
            json.dumps(asdict(ctx)), ttl=TTL_INTRADAY,
        )

    async def record_trade(self, symbol: str, trade: Dict):
        """Record a completed trade in the symbol's history."""
        client = await get_redis_client()
        key = f"ctx:trades:{self.session_id}:{symbol}"
        await client.rpush(key, json.dumps(trade))
        await client.expire(key, TTL_HISTORY)

        # Update win rate
        raw_list = await client.lrange(key, 0, -1)
        trades = [json.loads(r) for r in raw_list]
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        total = len(trades)

        ctx = await self._load_symbol(symbol)
        ctx.win_rate_recent = wins / total if total > 0 else 0
        ctx.avg_pnl_recent = sum(t.get("pnl", 0) for t in trades) / total if total else 0
        ctx.recent_trades = trades[-5:]  # Last 5

        await redis_set(
            f"ctx:symbol:{self.session_id}:{symbol}",
            json.dumps(asdict(ctx)), ttl=TTL_INTRADAY,
        )

    # ─── Sector Context ───

    async def update_sector(self, sector: str, data: Dict[str, Any]):
        """Update sector-level context."""
        key = f"ctx:sector:{self.session_id}:{sector}"
        existing = await self.get_sector(sector)

        existing.leader_direction = data.get("leader_direction", existing.leader_direction)
        existing.leader_move_pct = data.get("leader_move_pct", existing.leader_move_pct)
        existing.laggard_following = data.get("laggard_following", existing.laggard_following)
        existing.laggard_total = data.get("laggard_total", existing.laggard_total)
        existing.regime = data.get("regime", existing.regime)

        if existing.laggard_total > 0:
            existing.strength = existing.laggard_following / existing.laggard_total
        else:
            existing.strength = 0.0

        await redis_set(key, json.dumps(asdict(existing)), ttl=TTL_INTRADAY)

    async def record_cascade(self, sector: str, bar_index: int):
        """Record a cascade event for a sector."""
        ctx = await self.get_sector(sector)
        ctx.cascade_count_today += 1
        ctx.last_cascade_bar = bar_index
        await redis_set(
            f"ctx:sector:{self.session_id}:{sector}",
            json.dumps(asdict(ctx)), ttl=TTL_INTRADAY,
        )

    async def get_sector(self, sector: str) -> SectorContext:
        key = f"ctx:sector:{self.session_id}:{sector}"
        raw = await redis_get(key)
        if raw:
            try:
                return SectorContext(**json.loads(raw))
            except Exception:
                pass
        info = SECTOR_MAP.get(sector, {})
        return SectorContext(
            sector=sector,
            leader=info.get("leader", ""),
            laggard_total=len(info.get("laggards", [])),
        )

    # ─── Market (Global) Context ───

    async def get_market(self) -> MarketContext:
        key = f"ctx:market:{self.session_id}"
        raw = await redis_get(key)
        if raw:
            try:
                return MarketContext(**json.loads(raw))
            except Exception:
                pass
        return MarketContext()

    async def _save_market(self, mkt: MarketContext):
        await redis_set(
            f"ctx:market:{self.session_id}",
            json.dumps(asdict(mkt)), ttl=TTL_INTRADAY,
        )

    async def update_market(self, data: Dict[str, Any]):
        """Update global market context."""
        mkt = await self.get_market()

        new_regime = data.get("regime", mkt.regime)
        if new_regime != mkt.regime:
            mkt.regime_since = datetime.now(timezone.utc).isoformat()
            logger.info("Market regime change: %s → %s", mkt.regime, new_regime)
        mkt.regime = new_regime

        mkt.nifty_change_pct = data.get("nifty_change_pct", mkt.nifty_change_pct)
        mkt.institutional_flow = data.get("institutional_flow", mkt.institutional_flow)
        mkt.volatility_level = data.get("volatility_level", mkt.volatility_level)
        mkt.daily_pnl = data.get("daily_pnl", mkt.daily_pnl)

        # Determine time of day
        now = datetime.now(timezone.utc)
        # IST = UTC + 5:30, market opens 9:15 IST = 3:45 UTC
        ist_hour = (now.hour + 5) % 24 + (30 / 60)
        if ist_hour < 9.5:
            mkt.time_of_day = "pre_market"
        elif ist_hour < 10.0:
            mkt.time_of_day = "morning_open"
        elif ist_hour < 11.5:
            mkt.time_of_day = "morning"
        elif ist_hour < 13.5:
            mkt.time_of_day = "midday"
        elif ist_hour < 15.0:
            mkt.time_of_day = "afternoon"
        else:
            mkt.time_of_day = "close"

        await self._save_market(mkt)

    # ─── Confluence Detection ───

    async def find_confluence(self, symbol: str) -> Dict[str, Any]:
        """
        Find confluence for a symbol — multiple signals/factors agreeing.
        This is the context graph's killer feature.
        """
        ctx = await self._load_symbol(symbol)
        sector_ctx = await self.get_sector(ctx.sector)
        market_ctx = await self.get_market()

        confluence = {
            "symbol": symbol,
            "factors": [],
            "score": 0.0,
            "direction_consensus": None,
        }

        directions = {"LONG": 0, "SHORT": 0}

        # Factor 1: Multiple strategy signals
        for sig in ctx.active_signals:
            direction = sig.get("direction", "")
            if direction in directions:
                directions[direction] += 1
                confluence["factors"].append(
                    f"{sig['strategy']} says {direction} (conf: {sig.get('confidence', 0):.0%})"
                )

        # Factor 2: Sector flow alignment
        if sector_ctx.leader_direction:
            if sector_ctx.strength > 0.5:
                directions[sector_ctx.leader_direction] += 1
                confluence["factors"].append(
                    f"Sector {ctx.sector} flowing {sector_ctx.leader_direction} "
                    f"(strength: {sector_ctx.strength:.0%}, "
                    f"{sector_ctx.laggard_following}/{sector_ctx.laggard_total} laggards)"
                )

        # Factor 3: Market regime alignment
        if market_ctx.regime in ("trending_up",) and "LONG" in directions:
            directions["LONG"] += 0.5
            confluence["factors"].append(f"Market regime: {market_ctx.regime}")
        elif market_ctx.regime in ("trending_down",) and "SHORT" in directions:
            directions["SHORT"] += 0.5
            confluence["factors"].append(f"Market regime: {market_ctx.regime}")

        # Factor 4: VWAP alignment
        if ctx.vwap_position == "above":
            directions["LONG"] += 0.3
        elif ctx.vwap_position == "below":
            directions["SHORT"] += 0.3

        # Factor 5: Historical performance on this symbol
        if ctx.win_rate_recent > 0.6:
            confluence["factors"].append(f"Recent win rate on {symbol}: {ctx.win_rate_recent:.0%}")

        # Determine consensus
        if directions["LONG"] > directions["SHORT"]:
            confluence["direction_consensus"] = "LONG"
            confluence["score"] = min(1.0, directions["LONG"] / 4)
        elif directions["SHORT"] > directions["LONG"]:
            confluence["direction_consensus"] = "SHORT"
            confluence["score"] = min(1.0, directions["SHORT"] / 4)
        else:
            confluence["direction_consensus"] = None
            confluence["score"] = 0.0

        return confluence

    # ─── Exposure Analysis ───

    async def get_exposure_map(self) -> Dict[str, Any]:
        """Get full exposure analysis — sectors, correlations, concentration."""
        client = await get_redis_client()

        # Find all symbols with positions
        exposure = {
            "by_sector": {},
            "by_direction": {"LONG": 0, "SHORT": 0},
            "total_positions": 0,
            "warnings": [],
        }

        # Scan all symbol contexts
        keys = []
        async for key in client.scan_iter(f"ctx:symbol:{self.session_id}:*"):
            keys.append(key)

        for key in keys:
            raw = await client.get(key)
            if not raw:
                continue
            try:
                ctx = SymbolContext(**json.loads(raw))
            except Exception:
                continue

            if not ctx.has_position:
                continue

            exposure["total_positions"] += 1
            sector = ctx.sector or "unknown"

            if sector not in exposure["by_sector"]:
                exposure["by_sector"][sector] = {"count": 0, "symbols": [], "net_pnl": 0}

            exposure["by_sector"][sector]["count"] += 1
            exposure["by_sector"][sector]["symbols"].append(ctx.symbol)
            exposure["by_sector"][sector]["net_pnl"] += ctx.position_pnl
            exposure["by_direction"][ctx.position_side] += 1

        # Generate warnings
        for sector, data in exposure["by_sector"].items():
            if data["count"] >= 3:
                exposure["warnings"].append(
                    f"Concentrated in {sector}: {data['count']} positions ({', '.join(data['symbols'])})"
                )

        if exposure["by_direction"]["LONG"] > 0 and exposure["by_direction"]["SHORT"] == 0:
            exposure["warnings"].append("All positions are LONG — no hedge")
        if exposure["by_direction"]["SHORT"] > 0 and exposure["by_direction"]["LONG"] == 0:
            exposure["warnings"].append("All positions are SHORT — no hedge")

        return exposure

    # ─── Agent Context Snapshot ───

    async def build_agent_context(self, symbols: Optional[List[str]] = None) -> str:
        """
        Build a context snapshot for the LLM agent's system prompt.
        This is the graph rendered as text the agent can reason about.
        """
        market = await self.get_market()
        exposure = await self.get_exposure_map()

        lines = [
            f"## Market Context (as of {market.time_of_day})\n",
            f"Market Regime: **{market.regime}**"
            f"{f' (since {market.regime_since[:16]})' if market.regime_since else ''}",
            f"NIFTY: {market.nifty_change_pct:+.2f}% | "
            f"Flow: {market.institutional_flow} | "
            f"Volatility: {market.volatility_level}",
            f"Today: {market.signals_generated_today} signals, "
            f"{market.trades_executed_today} trades, "
            f"P&L: ₹{market.daily_pnl:+,.2f}\n",
        ]

        # Exposure warnings
        if exposure["warnings"]:
            lines.append("**⚠️ Exposure Warnings:**")
            for w in exposure["warnings"]:
                lines.append(f"  - {w}")
            lines.append("")

        # Sector summaries
        active_sectors = set()
        for sector_name in SECTOR_MAP:
            sector = await self.get_sector(sector_name)
            if sector.leader_direction or sector.cascade_count_today > 0:
                active_sectors.add(sector_name)
                lines.append(
                    f"**{sector_name.upper()}:** "
                    f"Leader {sector.leader} → {sector.leader_direction or 'flat'} "
                    f"({sector.leader_move_pct:+.2f}%) | "
                    f"Laggards: {sector.laggard_following}/{sector.laggard_total} following | "
                    f"Cascades today: {sector.cascade_count_today}"
                )

        if not active_sectors:
            lines.append("No active sector flows detected.")

        # Symbol details (if requested)
        if symbols:
            lines.append("\n**Symbol Details:**")
            for sym in symbols[:5]:
                ctx = await self._load_symbol(sym)
                confluence = await self.find_confluence(sym)

                lines.append(
                    f"\n  **{sym}** ({ctx.sector}) — Regime: {ctx.regime}"
                    f"{' | HAS POSITION: ' + ctx.position_side + ' P&L: ₹' + f'{ctx.position_pnl:+,.2f}' if ctx.has_position else ''}"
                )
                if ctx.active_signals:
                    sigs = ", ".join(f"{s['strategy']}→{s['direction']}" for s in ctx.active_signals[:3])
                    lines.append(f"    Active signals: {sigs}")
                if confluence["factors"]:
                    lines.append(f"    Confluence ({confluence['score']:.0%}): {', '.join(confluence['factors'][:3])}")
                if ctx.win_rate_recent > 0:
                    lines.append(f"    Recent: {ctx.win_rate_recent:.0%} WR, avg P&L ₹{ctx.avg_pnl_recent:+,.2f}")

        return "\n".join(lines)

    # ─── Daily Reset ───

    async def reset_daily(self):
        """Clear intraday data at start of new trading day."""
        client = await get_redis_client()
        async for key in client.scan_iter(f"ctx:symbol:{self.session_id}:*"):
            await client.delete(key)
        async for key in client.scan_iter(f"ctx:sector:{self.session_id}:*"):
            await client.delete(key)
        await client.delete(f"ctx:market:{self.session_id}")
        logger.info("Context graph reset for new trading day")


# Factory
def create_context_graph(session_id: str = "auto") -> ContextGraph:
    return ContextGraph(session_id)
