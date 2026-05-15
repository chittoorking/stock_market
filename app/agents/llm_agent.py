"""
LLM Trading Agent — the brain that reads ALL data and makes decisions.

This replaces ALL the if/else decision logic. The agent receives:
- Full market context (sectors, volume, price structure, exhaustion)
- All strategy signals with raw scores
- Open positions with P&L curves, fade detection, volume divergence
- Correlation and exposure data

And returns structured decisions: which trades to take, how to size them,
what stops to use, and what to do with open positions.

For backtesting without API calls, includes a rule-based fallback that
mimics the LLM's reasoning using the same data.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPT = """You are Arjun, an expert intraday trading agent for Indian markets (NSE).

You receive a MARKET SNAPSHOT with:
1. Sector flows (leader changes, laggard following %, divergences)
2. Strategy signals (LNZ3, GDR4, AFT7, etc. with confidence scores)
3. Volume and price structure (RVOL, divergence, exhaustion patterns)
4. Open positions (P&L, MFE, fade %, volume divergence)
5. Correlation/exposure data

YOUR JOB: Make trading decisions based on ALL this data.

RULES:
- Maximum 3 positions at once
- One position per sector
- Never trade against a strong sector flow (>1% leader move with >75% laggards)
- Target hits are your best exits — let winners run when sector supports
- Volume divergence (price up, volume down) = smart money leaving = tighten or exit
- MFE fade > 50% = momentum died = protect remaining profit
- Exhaustion pattern on a stock = opening move was fake, weight signals accordingly
- ATR determines stop width, not a fixed percentage
- Energy sector historically underperforms — reduce size by 50%
- Bar 6 and bar 15 entries are highest quality. Bar 1-3 are noise.
- LNZ3 (71% WR) and GDR4 (67% WR) are your best strategies
- AFT7 is marginal (43% WR) — only take with sector confirmation

OUTPUT FORMAT (JSON):
{
  "entry_decisions": [
    {"symbol": "X", "direction": "LONG/SHORT", "strategy": "lnz3",
     "stop_pct": 0.5, "target_rr": 2.5, "size_pct": 0.05,
     "reason": "why this trade"}
  ],
  "position_actions": [
    {"symbol": "X", "action": "hold/tighten/partial/close",
     "new_stop": 100.0, "reason": "why this action"}
  ],
  "skip_reasons": ["why I skipped signal X"]
}"""


@dataclass
class AgentDecision:
    """Structured decision from the agent."""
    # Entry decisions
    entries: List[Dict[str, Any]] = field(default_factory=list)
    # Position management
    position_actions: List[Dict[str, Any]] = field(default_factory=list)
    # Reasoning
    skip_reasons: List[str] = field(default_factory=list)
    raw_reasoning: str = ""


def agent_decide_with_llm(
    context: str,
    api_key: Optional[str] = None,
    model: str = "gpt-4o-mini",
) -> AgentDecision:
    """
    Call the actual LLM with the full context.
    Falls back to rule-based if no API key.
    """
    if not api_key:
        return _rule_based_fallback(context)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": f"Here is the current market snapshot. Make your trading decisions:\n\n{context}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=1000,
        )

        raw = response.choices[0].message.content
        data = json.loads(raw)

        return AgentDecision(
            entries=data.get("entry_decisions", []),
            position_actions=data.get("position_actions", []),
            skip_reasons=data.get("skip_reasons", []),
            raw_reasoning=raw,
        )

    except Exception as e:
        logger.error("LLM agent call failed: %s", e)
        return _rule_based_fallback(context)


def _rule_based_fallback(context: str) -> AgentDecision:
    """
    Rule-based fallback that mimics LLM reasoning using the same data.
    Used for backtesting without API calls.

    This reads the context string and applies the SAME logic the LLM would,
    but deterministically. When you have an API key, the LLM replaces this entirely.
    """
    decision = AgentDecision()
    lines = context.split("\n")

    # Parse signals from context
    signals = []
    sectors = {}
    positions = []
    exhaustion_stocks = set()
    volume_warnings = {}
    concentration = ""

    section = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### Sector"):
            section = "sectors"
        elif stripped.startswith("### Active Signals"):
            section = "signals"
        elif stripped.startswith("### Open Positions") or stripped.startswith("### No Open"):
            section = "positions"
        elif stripped.startswith("##"):
            section = ""

        if section == "sectors" and stripped and not stripped.startswith("#"):
            if not stripped.startswith(">>"):
                parts = stripped.split(":")
                if len(parts) >= 2:
                    sec_name = parts[0].strip()
                    rest = parts[1].strip()
                    # Extract leader change
                    try:
                        tokens = rest.split()
                        leader = tokens[0]
                        change_str = tokens[1].replace("%", "").replace("+", "")
                        change = float(change_str)
                        # Extract strength
                        strength = 0
                        for t in tokens:
                            if "%" in t and "(" in t:
                                strength = float(t.replace("(", "").replace("%)", "").replace("%", "")) / 100
                                break
                        sectors[sec_name] = {"leader": leader, "change": change, "strength": strength}
                    except (ValueError, IndexError):
                        pass

        if section == "signals" and stripped.startswith("["):
            # Parse signal line
            try:
                # [lnz3 ] LONG  TATASTEEL    conf=71% | reason...
                bracket_end = stripped.index("]")
                strategy = stripped[1:bracket_end].strip()
                rest = stripped[bracket_end+1:].strip()
                tokens = rest.split()
                direction = tokens[0]
                symbol = tokens[1]

                conf_part = [t for t in tokens if t.startswith("conf=")]
                confidence = float(conf_part[0].replace("conf=", "").replace("%", "")) / 100 if conf_part else 0.5

                reason = ""
                if "|" in rest:
                    reason = rest.split("|", 1)[1].strip()

                has_confluence = "[CONFLUENCE]" in stripped
                has_contradiction = "[CONTRADICTION]" in stripped

                signals.append({
                    "strategy": strategy, "direction": direction, "symbol": symbol,
                    "confidence": confidence, "reason": reason,
                    "confluence": has_confluence, "contradiction": has_contradiction,
                })
            except (ValueError, IndexError):
                pass

        if "EXHAUSTION" in stripped and section == "signals":
            # Find which stock this belongs to
            for s in reversed(signals):
                exhaustion_stocks.add(s["symbol"])
                break

        if "VOLUME" in stripped and "BEARISH" in stripped:
            for s in reversed(signals):
                volume_warnings[s["symbol"]] = stripped
                break

        if section == "positions" and stripped.startswith("LONG") or stripped.startswith("SHORT"):
            try:
                tokens = stripped.split()
                pos_dir = tokens[0]
                pos_sym = tokens[1]
                # Extract P&L and fade
                pnl = float([t for t in tokens if "P&L=" in t][0].split("=")[1].replace("%", ""))
                fade = float([t for t in tokens if "fade=" in t][0].split("=")[1].replace("%", ""))
                mfe = float([t for t in tokens if "MFE=" in t][0].split("=")[1].replace("%", ""))
                positions.append({"symbol": pos_sym, "direction": pos_dir, "pnl": pnl, "fade": fade, "mfe": mfe})
            except (ValueError, IndexError):
                pass

        if "Exposure:" in stripped:
            concentration = stripped.split("Exposure:")[1].strip()

    # ─── Now reason like the agent would ───

    taken_sectors = set()
    for p in positions:
        taken_sectors.add(get_sector(p["symbol"]))

    # Score and rank signals
    scored = []
    for sig in signals:
        if sig["contradiction"]:
            decision.skip_reasons.append(f"Skip {sig['symbol']}: contradiction between strategies")
            continue

        score = sig["confidence"]
        reasons = []
        sym_sector = get_sector(sig["symbol"])
        sec_data = sectors.get(sym_sector, {})

        # Sector alignment
        if sec_data:
            leader_chg = sec_data.get("change", 0)
            strength = sec_data.get("strength", 0)
            aligned = (sig["direction"] == "LONG" and leader_chg > 0.3) or \
                      (sig["direction"] == "SHORT" and leader_chg < -0.3)
            against = (sig["direction"] == "LONG" and leader_chg < -0.3) or \
                      (sig["direction"] == "SHORT" and leader_chg > 0.3)

            if aligned and strength >= 0.75:
                score += 0.25
                reasons.append(f"sector {sym_sector} aligned ({leader_chg:+.1f}%, {strength:.0%})")
            elif against:
                score -= 0.30
                reasons.append(f"AGAINST sector {sym_sector} ({leader_chg:+.1f}%)")

        # Strategy quality
        if sig["strategy"] == "lnz3":
            score += 0.15
        elif sig["strategy"] == "gdr4":
            score += 0.10
        elif sig["strategy"] == "aft7":
            score -= 0.05
            if not (sec_data and sec_data.get("strength", 0) >= 0.75):
                score -= 0.10
                reasons.append("AFT7 without strong sector = weak")

        # Confluence bonus
        if sig["confluence"]:
            score += 0.20
            reasons.append("confluence")

        # Exhaustion context
        if sig["symbol"] in exhaustion_stocks:
            reasons.append("stock showed opening exhaustion")
            # If signal is IN direction of exhaustion reversal, boost
            # If signal is WITH the exhausted move, penalize
            score -= 0.05  # Slight caution

        # Volume warning
        if sig["symbol"] in volume_warnings:
            if sig["direction"] == "LONG":
                score -= 0.15
                reasons.append("volume divergence: smart money exiting")

        # Sector concentration
        if sym_sector in taken_sectors:
            score -= 0.20
            reasons.append(f"already in {sym_sector}")

        # Energy penalty
        if sym_sector == "energy":
            score -= 0.10
            reasons.append("energy sector historically weak")

        scored.append((score, sig, reasons))

    # Sort and take top entries
    scored.sort(key=lambda x: -x[0])

    max_entries = 3 - len(positions)
    for score, sig, reasons in scored:
        if len(decision.entries) >= max_entries:
            break

        if score < 0.45:
            decision.skip_reasons.append(
                f"Skip {sig['symbol']} [{sig['strategy']}]: score {score:.2f} too low ({', '.join(reasons)})"
            )
            continue

        sym_sector = get_sector(sig["symbol"])
        if sym_sector in taken_sectors:
            decision.skip_reasons.append(f"Skip {sig['symbol']}: sector {sym_sector} covered")
            continue

        # Determine stop from ATR context (or default)
        stop_pct = 0.60  # Default
        size_pct = 0.05

        if sym_sector == "energy":
            size_pct = 0.03  # Reduced

        if score > 0.75:
            size_pct = min(0.07, size_pct * 1.4)  # High conviction

        decision.entries.append({
            "symbol": sig["symbol"],
            "direction": sig["direction"],
            "strategy": sig["strategy"],
            "stop_pct": stop_pct,
            "target_rr": 2.5,
            "size_pct": size_pct,
            "score": round(score, 3),
            "reason": f"Score {score:.2f}: {', '.join(reasons)}" if reasons else f"Score {score:.2f}",
        })
        taken_sectors.add(sym_sector)

    # ─── Position management ───
    for pos in positions:
        action = "hold"
        reason = "stable"
        new_stop = None

        # Fade detection
        if pos["fade"] > 60 and pos["mfe"] > 0.3:
            action = "close"
            reason = f"Profit collapse: MFE {pos['mfe']:.2f}% but P&L {pos['pnl']:.2f}%, gave back {pos['fade']:.0f}%"
        elif pos["fade"] > 40 and pos["mfe"] > 0.2:
            action = "tighten"
            reason = f"Fading: gave back {pos['fade']:.0f}% of {pos['mfe']:.2f}% peak"

        # Volume divergence on open position
        if pos["symbol"] in volume_warnings and pos["direction"] == "LONG":
            if action == "hold":
                action = "tighten"
                reason = "Volume divergence: smart money exiting"

        # Sector turned against
        sym_sector = get_sector(pos["symbol"])
        sec_data = sectors.get(sym_sector, {})
        if sec_data:
            against = (pos["direction"] == "LONG" and sec_data.get("change", 0) < -0.5) or \
                      (pos["direction"] == "SHORT" and sec_data.get("change", 0) > 0.5)
            if against:
                action = "close"
                reason = f"Sector {sym_sector} reversed against position"

        # Partial at zone 3
        if pos["pnl"] > 0.40 and action == "hold":
            action = "partial"
            reason = f"Zone 3: take 30% at {pos['pnl']:.2f}%"

        decision.position_actions.append({
            "symbol": pos["symbol"],
            "action": action,
            "reason": reason,
            "new_stop": new_stop,
        })

    return decision


# Convenience import
from app.signals.base import get_sector
