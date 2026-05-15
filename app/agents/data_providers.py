"""
Data Providers — sub-agents that COMPUTE and WRITE to context graph.
They don't decide anything. They provide data for the LLM agent.

Each provider:
1. Takes raw market data
2. Computes specific metrics
3. Writes structured data to a context dict
4. The LLM agent reads ALL of it and reasons

Providers:
- SectorAnalyzer: sector flows, leader/laggard status, divergences
- VolumeProfiler: RVOL, volume trend, divergence detection
- PriceStructure: ATR, support/resistance, exhaustion patterns, MFE tracking
- SignalCollector: gathers all strategy signals with raw scores
- PositionTracker: open positions, P&L curve, fade detection
- CorrelationMapper: cross-stock relationships, sector concentration
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional
from app.signals.base import SECTOR_MAP, get_sector, is_sector_leader


class SectorAnalyzer:
    """Computes sector-level data. Writes to context, decides nothing."""

    @staticmethod
    def compute(all_data: Dict[str, List[Dict]], date: str, current_bar: int) -> Dict[str, Any]:
        """Returns structured sector data for the agent to read."""
        sectors = {}

        for sector_name, sector_info in SECTOR_MAP.items():
            leader = sector_info["leader"]
            laggards = sector_info["laggards"]

            leader_bars = [b for b in all_data.get(leader, []) if b["timestamp"][:10] == date]
            if not leader_bars or len(leader_bars) <= current_bar:
                continue

            leader_open = leader_bars[0]["open"]
            leader_now = leader_bars[current_bar]["close"]
            leader_change = (leader_now - leader_open) / leader_open * 100

            # Leader momentum: last 6 bars trend
            lookback = max(0, current_bar - 6)
            leader_recent = (leader_bars[current_bar]["close"] - leader_bars[lookback]["close"]) / leader_bars[lookback]["close"] * 100 if lookback < current_bar else 0

            # Laggard analysis
            laggard_data = []
            for lag in laggards:
                lag_bars = [b for b in all_data.get(lag, []) if b["timestamp"][:10] == date]
                if not lag_bars or len(lag_bars) <= current_bar:
                    continue
                lag_change = (lag_bars[current_bar]["close"] - lag_bars[0]["open"]) / lag_bars[0]["open"] * 100
                following = (leader_change > 0 and lag_change > 0) or (leader_change < 0 and lag_change < 0)
                divergence = leader_change - lag_change
                laggard_data.append({
                    "symbol": lag,
                    "change_pct": round(lag_change, 3),
                    "following": following,
                    "divergence_from_leader": round(divergence, 3),
                })

            following_count = sum(1 for l in laggard_data if l["following"])

            sectors[sector_name] = {
                "leader": leader,
                "leader_change_pct": round(leader_change, 3),
                "leader_momentum_6bar": round(leader_recent, 3),
                "laggards_following": following_count,
                "laggards_total": len(laggard_data),
                "strength": round(following_count / len(laggard_data), 2) if laggard_data else 0,
                "laggard_details": laggard_data,
                "biggest_laggard_divergence": max((l["divergence_from_leader"] for l in laggard_data), default=0),
            }

        return sectors


class VolumeProfiler:
    """Computes volume data per stock. Writes to context."""

    @staticmethod
    def compute(bars: List[Dict], current_bar: int) -> Dict[str, Any]:
        if current_bar < 3 or current_bar >= len(bars):
            return {"rvol": 1.0, "trend": "unknown", "divergence": "none"}

        # RVOL (first 3 bars vs average)
        first3_vol = sum(bars[i]["volume"] for i in range(min(3, len(bars))))
        all_vol = [b["volume"] for b in bars[:current_bar + 1]]
        avg_vol = sum(all_vol) / len(all_vol) if all_vol else 1
        rvol = first3_vol / (avg_vol * 3) if avg_vol > 0 else 1

        # Volume trend (last 6 bars)
        window = max(0, current_bar - 6)
        recent_vols = [bars[i]["volume"] for i in range(window, current_bar + 1)]
        recent_prices = [bars[i]["close"] for i in range(window, current_bar + 1)]

        if len(recent_vols) >= 4:
            vol_first_half = sum(recent_vols[:len(recent_vols)//2]) / max(len(recent_vols)//2, 1)
            vol_second_half = sum(recent_vols[len(recent_vols)//2:]) / max(len(recent_vols)//2, 1)
            vol_change = (vol_second_half - vol_first_half) / vol_first_half if vol_first_half > 0 else 0

            price_change = recent_prices[-1] - recent_prices[0] if recent_prices else 0
            price_up = price_change > 0

            # Divergence detection
            if price_up and vol_change < -0.30:
                divergence = f"BEARISH: price up but volume down {vol_change*100:.0f}% — smart money exiting"
            elif not price_up and vol_change < -0.30:
                divergence = f"selling_exhaustion: price down but volume drying up {vol_change*100:.0f}%"
            elif price_up and vol_change > 0.30:
                divergence = "confirmed: price up with rising volume"
            else:
                divergence = "none"
        else:
            vol_change = 0
            divergence = "none"

        # Current bar volume vs average
        current_vol_ratio = bars[current_bar]["volume"] / avg_vol if avg_vol > 0 else 1

        return {
            "rvol_opening": round(rvol, 2),
            "volume_trend_6bar": round(vol_change * 100, 1),
            "current_bar_vol_ratio": round(current_vol_ratio, 2),
            "divergence": divergence,
        }


class PriceStructure:
    """Computes price structure: ATR, exhaustion, levels."""

    @staticmethod
    def compute(bars: List[Dict], current_bar: int, prev_close: Optional[float] = None) -> Dict[str, Any]:
        if current_bar < 2 or current_bar >= len(bars):
            return {}

        # ATR
        true_ranges = []
        for i in range(1, min(current_bar + 1, len(bars))):
            h, l, pc = bars[i]["high"], bars[i]["low"], bars[i-1]["close"]
            true_ranges.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr = sum(true_ranges[-14:]) / min(14, len(true_ranges)) if true_ranges else 0
        price = bars[current_bar]["close"]
        atr_pct = (atr / price * 100) if price > 0 else 0

        # Gap info
        gap_pct = 0
        if prev_close and prev_close > 0:
            gap_pct = (bars[0]["open"] - prev_close) / prev_close * 100

        # Opening drive exhaustion check
        exhaustion = "none"
        if len(bars) >= 2:
            bar0_move = (bars[0]["close"] - bars[0]["open"]) / bars[0]["open"] * 100
            if abs(bar0_move) >= 0.8:
                bar0_range = bars[0]["close"] - bars[0]["open"]
                if bar0_range != 0:
                    bar1_retrace = abs((bars[1]["close"] - bars[0]["close"]) / bar0_range) * 100
                    if bar1_retrace > 50:
                        exhaustion = f"opening_spike_{'+' if bar0_move > 0 else '-'}{abs(bar0_move):.1f}%_retraced_{bar1_retrace:.0f}%"

        # Day's range and VWAP position
        day_bars = bars[:current_bar + 1]
        day_high = max(b["high"] for b in day_bars)
        day_low = min(b["low"] for b in day_bars)
        day_range_pct = (day_high - day_low) / price * 100 if price > 0 else 0

        # Simple VWAP
        cum_tp_vol = sum((b["high"]+b["low"]+b["close"])/3 * b["volume"] for b in day_bars)
        cum_vol = sum(b["volume"] for b in day_bars)
        vwap = cum_tp_vol / cum_vol if cum_vol > 0 else price
        vwap_position = "above" if price > vwap else "below"

        # Morning price change from open
        morning_change = (price - bars[0]["open"]) / bars[0]["open"] * 100

        return {
            "atr": round(atr, 2),
            "atr_pct": round(atr_pct, 3),
            "gap_pct": round(gap_pct, 3),
            "exhaustion": exhaustion,
            "day_range_pct": round(day_range_pct, 3),
            "vwap": round(vwap, 2),
            "vwap_position": vwap_position,
            "morning_change_pct": round(morning_change, 3),
            "price": round(price, 2),
            "day_high": round(day_high, 2),
            "day_low": round(day_low, 2),
        }


class PositionTracker:
    """Tracks P&L curve for open positions. Pure data."""

    @staticmethod
    def compute(
        entry_price: float, direction: str, current_price: float,
        high_since_entry: float, low_since_entry: float,
        bars_held: int,
    ) -> Dict[str, Any]:
        if direction == "LONG":
            pnl_pct = (current_price - entry_price) / entry_price * 100
            mfe = (high_since_entry - entry_price) / entry_price * 100
            mae = (entry_price - low_since_entry) / entry_price * 100  # Max adverse
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100
            mfe = (entry_price - low_since_entry) / entry_price * 100
            mae = (high_since_entry - entry_price) / entry_price * 100

        # Fade: how much of peak profit has been given back
        fade_pct = 0
        if mfe > 0.05:
            profit_given_back = mfe - max(0, pnl_pct)
            fade_pct = (profit_given_back / mfe) * 100

        # Profit zone
        if pnl_pct >= 0.75:
            zone = "strong_win"
        elif pnl_pct >= 0.40:
            zone = "decent_win"
        elif pnl_pct >= 0.15:
            zone = "small_win"
        elif pnl_pct >= 0:
            zone = "scratch"
        elif pnl_pct >= -0.30:
            zone = "small_loss"
        else:
            zone = "significant_loss"

        return {
            "pnl_pct": round(pnl_pct, 3),
            "mfe_pct": round(mfe, 3),
            "mae_pct": round(mae, 3),
            "fade_pct": round(fade_pct, 1),
            "bars_held": bars_held,
            "zone": zone,
            "minutes_held": bars_held * 5,
        }


class CorrelationMapper:
    """Maps cross-stock relationships for the agent."""

    CORRELATED_PAIRS = [
        ("TCS", "INFY"), ("HDFCBANK", "ICICIBANK"), ("TATASTEEL", "JSWSTEEL"),
        ("SBIN", "AXISBANK"), ("RELIANCE", "ONGC"), ("WIPRO", "HCLTECH"),
        ("SUNPHARMA", "CIPLA"), ("MARUTI", "TATAMOTORS"),
    ]

    @staticmethod
    def compute(
        all_data: Dict[str, List[Dict]], date: str, current_bar: int,
        open_positions: Dict[str, str],  # {symbol: direction}
    ) -> Dict[str, Any]:
        # Sector concentration
        sector_counts = {}
        for sym, direction in open_positions.items():
            sec = get_sector(sym)
            sector_counts[sec] = sector_counts.get(sec, 0) + 1

        concentration_warning = ""
        for sec, count in sector_counts.items():
            if count >= 2:
                concentration_warning += f"Over-concentrated in {sec} ({count} positions). "

        # Direction imbalance
        longs = sum(1 for d in open_positions.values() if d == "LONG")
        shorts = sum(1 for d in open_positions.values() if d == "SHORT")

        if longs > 0 and shorts == 0 and longs >= 2:
            concentration_warning += f"All {longs} positions are LONG — no hedge. "
        if shorts > 0 and longs == 0 and shorts >= 2:
            concentration_warning += f"All {shorts} positions are SHORT — no hedge. "

        return {
            "open_positions_count": len(open_positions),
            "longs": longs,
            "shorts": shorts,
            "sector_exposure": sector_counts,
            "concentration_warning": concentration_warning or "Diversified",
        }


def build_full_context(
    all_data: Dict[str, List[Dict]],
    date: str,
    current_bar: int,
    signals: List,
    open_positions: Dict[str, Dict] = None,
) -> str:
    """
    Build the COMPLETE context string that the LLM agent reads.
    This is EVERYTHING — the agent sees all data and reasons from it.
    """
    open_pos = open_positions or {}
    pos_directions = {sym: p.get("direction", "LONG") for sym, p in open_pos.items()}

    lines = []
    time_str = ""
    # Get time from any stock's bar
    for sym, bars in all_data.items():
        db = [b for b in bars if b["timestamp"][:10] == date]
        if db and len(db) > current_bar:
            time_str = db[current_bar]["timestamp"].split(" ")[1][:5]
            break

    lines.append(f"## Market Snapshot — {date} {time_str} (bar {current_bar})")
    lines.append("")

    # Sector analysis
    sectors = SectorAnalyzer.compute(all_data, date, current_bar)
    lines.append("### Sector Flows")
    for sec_name in sorted(sectors, key=lambda s: -abs(sectors[s]["leader_change_pct"])):
        s = sectors[sec_name]
        lines.append(
            f"  {sec_name:8s}: {s['leader']:12s} {s['leader_change_pct']:+.2f}% "
            f"(momentum {s['leader_momentum_6bar']:+.2f}%) | "
            f"{s['laggards_following']}/{s['laggards_total']} following ({s['strength']:.0%})"
        )
        # Show biggest divergence
        for lag in s.get("laggard_details", []):
            if abs(lag["divergence_from_leader"]) > 1.0:
                lines.append(
                    f"    >> {lag['symbol']} diverging: {lag['change_pct']:+.2f}% "
                    f"(gap={lag['divergence_from_leader']:+.2f}% from leader)"
                )
    lines.append("")

    # Signals
    lines.append(f"### Active Signals ({len(signals)} total)")
    # Group by symbol
    by_symbol = {}
    for sig in signals:
        by_symbol.setdefault(sig.symbol, []).append(sig)

    for sym in sorted(by_symbol):
        sigs = by_symbol[sym]
        directions = set(s.direction for s in sigs)
        confluence = len(sigs) > 1 and len(directions) == 1
        contradiction = len(directions) > 1

        for s in sigs:
            marker = ""
            if confluence: marker = " [CONFLUENCE]"
            if contradiction: marker = " [CONTRADICTION]"

            # Get volume and price context for this stock
            sym_bars = [b for b in all_data.get(sym, []) if b["timestamp"][:10] == date]
            prev_bars = [b for b in all_data.get(sym, []) if b["timestamp"][:10] < date]
            prev_close = prev_bars[-1]["close"] if prev_bars else None

            vol = VolumeProfiler.compute(sym_bars, current_bar)
            price = PriceStructure.compute(sym_bars, current_bar, prev_close)

            lines.append(
                f"  [{s.strategy_name:6s}] {s.direction:5s} {sym:12s} "
                f"conf={s.raw_confidence:.0%} | {s.signal_reason[:55]}{marker}"
            )
            if price.get("exhaustion", "none") != "none":
                lines.append(f"    >> EXHAUSTION: {price['exhaustion']}")
            if "BEARISH" in vol.get("divergence", "") or "exhaustion" in vol.get("divergence", ""):
                lines.append(f"    >> VOLUME: {vol['divergence']}")

    lines.append("")

    # Correlation / exposure
    corr = CorrelationMapper.compute(all_data, date, current_bar, pos_directions)
    if open_pos:
        lines.append("### Open Positions")
        for sym, pos_data in open_pos.items():
            sym_bars = [b for b in all_data.get(sym, []) if b["timestamp"][:10] == date]
            if sym_bars and current_bar < len(sym_bars):
                current_price = sym_bars[current_bar]["close"]
                high_since = max(b["high"] for b in sym_bars[pos_data.get("entry_bar", 0):current_bar+1])
                low_since = min(b["low"] for b in sym_bars[pos_data.get("entry_bar", 0):current_bar+1])
                bars_held = current_bar - pos_data.get("entry_bar", 0)

                pt = PositionTracker.compute(
                    pos_data["entry"], pos_data["direction"],
                    current_price, high_since, low_since, bars_held,
                )
                vol = VolumeProfiler.compute(sym_bars, current_bar)

                lines.append(
                    f"  {pos_data['direction']:5s} {sym:12s} entry={pos_data['entry']:.2f} "
                    f"now={current_price:.2f} P&L={pt['pnl_pct']:+.3f}% "
                    f"MFE={pt['mfe_pct']:.3f}% fade={pt['fade_pct']:.0f}% "
                    f"zone={pt['zone']} held={pt['minutes_held']}min"
                )
                if pt["fade_pct"] > 40:
                    lines.append(f"    >> WARNING: gave back {pt['fade_pct']:.0f}% of peak profit")
                if "BEARISH" in vol.get("divergence", ""):
                    lines.append(f"    >> VOLUME: {vol['divergence']}")

        lines.append(f"  Exposure: {corr['concentration_warning']}")
    else:
        lines.append("### No Open Positions")

    lines.append("")
    return "\n".join(lines)
