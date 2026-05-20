"""
Mixture of Experts Scanner — 5 domain LLM experts + 1 aggregator.

Each expert sees ONLY today's raw data for their domain. No history. No bias.
Each expert reasons independently — no cross-contamination.
The aggregator reads all 5 expert reports and picks the best 1-3 signals.

Experts:
1. Volume Expert     — RVOL, volume profile, entry bar flow, institutional vs retail
2. Sector Expert     — sector rotation, breadth, leader/laggard, cross-stock correlation
3. Price Action Expert — candle structure, gap, VWAP, noise ratio, support/resistance
4. Momentum Expert   — morning alignment, trend direction, consecutive bars, EMA
5. Market Regime Expert — broad market breadth, gap type, trend quality, participation

All LLMs. All stateless. Only today's data.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Any
from dataclasses import dataclass, field

from crewai import Agent, Task, Crew, Process
from crewai.llm import LLM

from app.agents.data_providers import SectorAnalyzer, VolumeProfiler, PriceStructure
from app.signals.base import get_sector, SECTOR_MAP

logger = logging.getLogger(__name__)


@dataclass
class MoETrace:
    """Captures all expert reasoning."""
    date: str = ""
    bar: int = 0
    signals_count: int = 0
    volume_expert: str = ""
    sector_expert: str = ""
    price_expert: str = ""
    momentum_expert: str = ""
    market_expert: str = ""
    aggregator: str = ""
    ranked: List[Dict] = field(default_factory=list)


def _get_llm(api_key: str) -> LLM:
    return LLM(
        model="openai/gpt-4o-mini",
        api_key=api_key,
        temperature=0,
        max_tokens=800,
    )


# ═══════════════════════════════════════════════════════════════
# DATA EXTRACTORS — compute raw domain data from today's bars
# ═══════════════════════════════════════════════════════════════

def _extract_volume_data(symbol: str, all_data: Dict, date: str, bar_idx: int) -> str:
    """Raw volume data for this stock today."""
    bars = [b for b in all_data.get(symbol, []) if b['timestamp'][:10] == date]
    prev_bars = [b for b in all_data.get(symbol, []) if b['timestamp'][:10] < date]
    if not bars or len(bars) <= bar_idx:
        return "No data"

    lines = []
    # Bar-by-bar volume
    for i in range(min(bar_idx + 1, len(bars))):
        b = bars[i]
        lines.append(f"  bar{i}: vol={b['volume']:,} close={b['close']:.2f}")

    # RVOL vs recent days
    prev_dates = sorted(set(b['timestamp'][:10] for b in prev_bars))[-5:]
    prev_avg_vols = []
    for pd in prev_dates:
        pd_bars = [b for b in prev_bars if b['timestamp'][:10] == pd]
        if len(pd_bars) > bar_idx:
            prev_avg_vols.append(sum(b['volume'] for b in pd_bars[:bar_idx + 1]))
    today_vol = sum(b['volume'] for b in bars[:bar_idx + 1])
    if prev_avg_vols:
        avg_prev = sum(prev_avg_vols) / len(prev_avg_vols)
        rvol = today_vol / avg_prev if avg_prev > 0 else 0
        lines.append(f"  RVOL: {rvol:.2f}x (today {today_vol:,} vs avg {avg_prev:,.0f})")

    # Volume sustainability (first half vs second half)
    if bar_idx >= 5:
        v1 = sum(bars[i]['volume'] for i in range(3))
        v2 = sum(bars[i]['volume'] for i in range(3, min(6, bar_idx + 1)))
        ratio = v2 / v1 if v1 > 0 else 0
        lines.append(f"  Volume sustain: {ratio:.0%} (first 3 bars vs next 3)")

    # Entry bar vs previous bar
    if bar_idx >= 1:
        prev_v = bars[bar_idx - 1]['volume']
        curr_v = bars[bar_idx]['volume']
        change = (curr_v - prev_v) / prev_v * 100 if prev_v > 0 else 0
        lines.append(f"  Entry bar volume change: {change:+.0f}% vs previous bar")

    return "\n".join(lines)


def _extract_sector_data(symbol: str, all_data: Dict, date: str, bar_idx: int) -> str:
    """Raw sector data — all stocks in this sector today."""
    sector = get_sector(symbol)
    sec_info = SECTOR_MAP.get(sector, {})
    if not sec_info:
        return f"Unknown sector for {symbol}. No sector data available."

    leader = sec_info.get("leader", "")
    laggards = sec_info.get("laggards", [])
    all_sec = ([leader] if leader else []) + list(laggards)

    lines = [f"Sector: {sector} | Leader: {leader}"]
    for ss in all_sec:
        ss_bars = [b for b in all_data.get(ss, []) if b['timestamp'][:10] == date]
        if not ss_bars or len(ss_bars) <= bar_idx:
            continue
        move = (ss_bars[bar_idx]['close'] - ss_bars[0]['open']) / ss_bars[0]['open'] * 100
        # Early vs now direction
        early_move = (ss_bars[min(2, len(ss_bars)-1)]['close'] - ss_bars[0]['open']) / ss_bars[0]['open'] * 100 if len(ss_bars) > 2 else move
        tag = " ← THIS STOCK" if ss == symbol else (" (leader)" if ss == leader else "")
        lines.append(f"  {ss}{tag}: now {move:+.2f}% | at bar 2: {early_move:+.2f}%")

    # Sector breadth
    with_dir = sum(1 for ss in all_sec
                   if any(b['timestamp'][:10] == date for b in all_data.get(ss, []))
                   and len([b for b in all_data.get(ss, []) if b['timestamp'][:10] == date]) > bar_idx
                   and ((ss_bars := [b for b in all_data.get(ss, []) if b['timestamp'][:10] == date]) and True)
                   and (ss_bars[bar_idx]['close'] - ss_bars[0]['open']) / ss_bars[0]['open'] * 100 > 0.1)
    lines.append(f"  Sector breadth: {with_dir}/{len(all_sec)} stocks moving UP")

    return "\n".join(lines)


def _extract_price_data(symbol: str, all_data: Dict, date: str, bar_idx: int) -> str:
    """Raw price structure — OHLCV bars, candle quality, gap, VWAP."""
    bars = [b for b in all_data.get(symbol, []) if b['timestamp'][:10] == date]
    prev_bars = [b for b in all_data.get(symbol, []) if b['timestamp'][:10] < date]
    if not bars or len(bars) <= bar_idx:
        return "No data"

    lines = []
    # Bar-by-bar OHLCV
    for i in range(min(bar_idx + 1, len(bars))):
        b = bars[i]
        body = abs(b['close'] - b['open'])
        rng = b['high'] - b['low']
        body_pct = body / rng * 100 if rng > 0 else 0
        direction = "GREEN" if b['close'] >= b['open'] else "RED"
        lines.append(f"  bar{i}: O={b['open']:.2f} H={b['high']:.2f} L={b['low']:.2f} C={b['close']:.2f} | {direction} body={body_pct:.0f}%")

    # Gap
    if prev_bars:
        prev_close = prev_bars[-1]['close']
        gap_pct = (bars[0]['open'] - prev_close) / prev_close * 100
        lines.append(f"  Gap: {gap_pct:+.2f}% (prev close {prev_close:.2f} → open {bars[0]['open']:.2f})")

    # VWAP
    tp_vol = sum((b['high']+b['low']+b['close'])/3 * b['volume'] for b in bars[:bar_idx+1])
    cum_vol = sum(b['volume'] for b in bars[:bar_idx+1])
    vwap = tp_vol / cum_vol if cum_vol > 0 else bars[bar_idx]['close']
    vwap_dist = (bars[bar_idx]['close'] - vwap) / vwap * 100
    lines.append(f"  VWAP: {vwap:.2f} | Price {vwap_dist:+.2f}% from VWAP")

    # Noise ratio
    if bar_idx >= 3:
        ranges = [bars[i]['high'] - bars[i]['low'] for i in range(max(0, bar_idx-5), bar_idx+1)]
        moves = [abs(bars[i]['close'] - bars[i-1]['close']) for i in range(max(1, bar_idx-4), bar_idx+1)]
        noise = sum(ranges) / sum(moves) if moves and sum(moves) > 0 else 5
        lines.append(f"  Noise ratio: {noise:.1f} (lower = cleaner trend)")

    # ATR
    if bar_idx >= 2:
        trs = []
        for i in range(1, min(bar_idx+1, len(bars))):
            tr = max(bars[i]['high'] - bars[i]['low'],
                     abs(bars[i]['high'] - bars[i-1]['close']),
                     abs(bars[i]['low'] - bars[i-1]['close']))
            trs.append(tr)
        atr = sum(trs) / len(trs) if trs else 0
        atr_pct = atr / bars[bar_idx]['close'] * 100 if bars[bar_idx]['close'] > 0 else 0
        lines.append(f"  ATR: {atr:.2f} ({atr_pct:.2f}%)")

    # Entry bar quality
    eb = bars[bar_idx]
    eb_body = abs(eb['close'] - eb['open'])
    eb_range = eb['high'] - eb['low']
    eb_body_ratio = eb_body / eb_range * 100 if eb_range > 0 else 0
    lines.append(f"  Entry bar body ratio: {eb_body_ratio:.0f}%")

    # Candle patterns
    try:
        from app.agents.candle_patterns import detect_patterns
        pats = detect_patterns(bars, bar_idx)
        if pats:
            for p in pats[:3]:
                lines.append(f"  Pattern: {p.name} ({p.direction}, {p.reliability}) — {p.meaning[:60]}")
    except Exception:
        pass

    return "\n".join(lines)


def _extract_momentum_data(symbol: str, all_data: Dict, date: str, bar_idx: int) -> str:
    """Raw momentum data — morning move, consecutive direction, EMA."""
    bars = [b for b in all_data.get(symbol, []) if b['timestamp'][:10] == date]
    if not bars or len(bars) <= bar_idx:
        return "No data"

    lines = []

    # Morning move (open to current)
    morning_move = (bars[bar_idx]['close'] - bars[0]['open']) / bars[0]['open'] * 100
    lines.append(f"  Morning move (open→now): {morning_move:+.2f}%")

    # Bar 0 strength
    bar0 = bars[0]
    bar0_move = (bar0['close'] - bar0['open']) / bar0['open'] * 100
    bar0_body = abs(bar0['close'] - bar0['open'])
    bar0_range = bar0['high'] - bar0['low']
    bar0_body_pct = bar0_body / bar0_range * 100 if bar0_range > 0 else 0
    lines.append(f"  Opening bar: {bar0_move:+.2f}% move, {bar0_body_pct:.0f}% body ratio")

    # Consecutive direction bars before entry
    consec_long = 0
    consec_short = 0
    for i in range(bar_idx, 0, -1):
        if bars[i]['close'] > bars[i]['open']:
            consec_long += 1
        else:
            break
    for i in range(bar_idx, 0, -1):
        if bars[i]['close'] < bars[i]['open']:
            consec_short += 1
        else:
            break
    lines.append(f"  Consecutive green bars: {consec_long} | red bars: {consec_short}")

    # Pre-entry bar direction (last 6 bars)
    if bar_idx >= 5:
        green = sum(1 for i in range(max(0, bar_idx-5), bar_idx+1) if bars[i]['close'] > bars[i]['open'])
        red = bar_idx + 1 - max(0, bar_idx - 5) - green
        lines.append(f"  Last 6 bars: {green} green, {red} red")

    # EMA
    closes = [b['close'] for b in bars[:bar_idx + 1]]
    if len(closes) >= 9:
        def _ema(data, period):
            mult = 2 / (period + 1)
            val = sum(data[:period]) / period
            for p in data[period:]:
                val = (p - val) * mult + val
            return val
        ema9 = _ema(closes, 9) if len(closes) >= 9 else closes[-1]
        lines.append(f"  EMA9: {ema9:.2f} | Price: {closes[-1]:.2f} | {'above' if closes[-1] > ema9 else 'below'} EMA9")

    return "\n".join(lines)


def _extract_market_data(all_data: Dict, date: str, bar_idx: int) -> str:
    """Broad market state — breadth, gap type, trend quality. Computed once per scan."""
    up = 0; down = 0; flat = 0; total = 0
    gap_ups = 0; gap_downs = 0; total_gap = 0
    consistent = 0; choppy = 0

    for sym, bars_all in all_data.items():
        if sym in ('NIFTY_50', 'NIFTY_BANK'):
            continue
        db = [b for b in bars_all if b['timestamp'][:10] == date]
        pb = [b for b in bars_all if b['timestamp'][:10] < date]
        if not db or len(db) <= bar_idx:
            continue

        total += 1
        move = (db[bar_idx]['close'] - db[0]['open']) / db[0]['open'] * 100
        if move > 0.15: up += 1
        elif move < -0.15: down += 1
        else: flat += 1

        if pb:
            gap = (db[0]['open'] - pb[-1]['close']) / pb[-1]['close'] * 100
            total_gap += 1
            if gap > 0.3: gap_ups += 1
            elif gap < -0.3: gap_downs += 1

        # Trend consistency
        if bar_idx >= 3:
            overall = 1 if db[bar_idx]['close'] > db[0]['open'] else -1
            same = sum(1 for i in range(1, min(bar_idx+1, len(db)))
                       if (1 if db[i]['close'] > db[i-1]['close'] else -1) == overall)
            if same / bar_idx > 0.6: consistent += 1
            elif same / bar_idx < 0.4: choppy += 1

    if total == 0:
        return "No market data"

    lines = []
    lines.append(f"  Breadth: {up} up ({up/total*100:.0f}%), {down} down ({down/total*100:.0f}%), {flat} flat | Total: {total} stocks")
    if total_gap > 0:
        lines.append(f"  Gaps: {gap_ups} up, {gap_downs} down out of {total_gap}")
    lines.append(f"  Trend: {consistent} trending cleanly, {choppy} choppy out of {total}")

    # Regime label
    up_pct = up / total * 100
    down_pct = down / total * 100
    if up_pct > 65: regime = "STRONG BULLISH"
    elif up_pct > 55: regime = "BULLISH LEAN"
    elif down_pct > 65: regime = "STRONG BEARISH"
    elif down_pct > 55: regime = "BEARISH LEAN"
    elif flat > total * 0.4: regime = "FLAT/CHOPPY"
    else: regime = "MIXED"
    lines.append(f"  Regime: {regime}")

    if choppy > total * 0.5:
        lines.append(f"  WARNING: Choppy day — high whipsaw risk")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# EXPERT AGENTS — each runs independently, sees only their data
# ═══════════════════════════════════════════════════════════════

def _run_expert(
    role: str,
    goal: str,
    backstory: str,
    data: str,
    signals_text: str,
    api_key: str,
) -> str:
    """Run a single expert agent. Returns their analysis text."""
    llm = _get_llm(api_key)
    agent = Agent(role=role, goal=goal, backstory=backstory, verbose=True, llm=llm)
    task = Task(
        description=(
            f"Analyze these trading signals from YOUR domain expertise.\n\n"
            f"SIGNALS:\n{signals_text}\n\n"
            f"YOUR DOMAIN DATA (today only):\n{data}\n\n"
            f"For each signal, give your expert assessment:\n"
            f"- Signal symbol + direction\n"
            f"- Your domain score: 1-10 (10 = strongest from your perspective)\n"
            f"- Key finding from your data (be specific with numbers)\n"
            f"- Conviction: HIGH / MEDIUM / LOW\n"
            f"Focus on top 5 signals only. Be concise."
        ),
        expected_output="Expert assessment of each signal with domain-specific scores and findings",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=True)
    try:
        result = crew.kickoff()
        return str(result)
    except Exception as e:
        logger.error(f"{role} failed: {e}")
        return f"ERROR: {e}"


def moe_scanner(
    signals_text: str,
    sector_context: str,
    all_data: Dict,
    date: str,
    bar_idx: int,
    api_key: str,
    active_trades: Dict = None,
) -> tuple[List[Dict], MoETrace]:
    """
    Mixture of Experts scanner.

    5 independent domain experts analyze signals from their specialty.
    1 aggregator reads all 5 reports and picks the best 1-3.

    All stateless. Only today's data. No history bias.
    """
    os.environ["OPENAI_API_KEY"] = api_key
    trace = MoETrace(date=date, bar=bar_idx, signals_count=len(signals_text.split('\n')))

    # Extract signal symbols for domain data lookup
    signal_symbols = []
    for line in signals_text.split('\n'):
        for direction in ['LONG ', 'SHORT ']:
            if direction in line:
                parts = line.split(direction)
                if len(parts) > 1:
                    sym = parts[1].split()[0].strip('(')
                    if sym and sym.isalpha():
                        signal_symbols.append(sym)

    # ─── Compute domain data for each signal ───
    # Volume data (per signal)
    vol_data_parts = []
    for sym in signal_symbols[:10]:  # Top 10 to keep token count sane
        vol_data_parts.append(f"\n{sym}:\n{_extract_volume_data(sym, all_data, date, bar_idx)}")
    vol_data = "\n".join(vol_data_parts) if vol_data_parts else "No volume data"

    # Sector data (per signal)
    sec_data_parts = []
    seen_sectors = set()
    for sym in signal_symbols[:10]:
        sector = get_sector(sym)
        if sector not in seen_sectors:
            seen_sectors.add(sector)
            sec_data_parts.append(f"\n{sym} ({sector}):\n{_extract_sector_data(sym, all_data, date, bar_idx)}")
    sec_data = "\n".join(sec_data_parts) if sec_data_parts else "No sector data"

    # Price data (per signal)
    price_data_parts = []
    for sym in signal_symbols[:10]:
        price_data_parts.append(f"\n{sym}:\n{_extract_price_data(sym, all_data, date, bar_idx)}")
    price_data = "\n".join(price_data_parts) if price_data_parts else "No price data"

    # Momentum data (per signal)
    mom_data_parts = []
    for sym in signal_symbols[:10]:
        mom_data_parts.append(f"\n{sym}:\n{_extract_momentum_data(sym, all_data, date, bar_idx)}")
    mom_data = "\n".join(mom_data_parts) if mom_data_parts else "No momentum data"

    # Market data (computed once)
    mkt_data = _extract_market_data(all_data, date, bar_idx)

    # ─── Run 5 experts independently ───
    print("V", end="", flush=True)
    trace.volume_expert = _run_expert(
        role="Volume Expert",
        goal="Score each signal based on volume quality — is there real institutional participation?",
        backstory=(
            "You are an expert in reading volume. You know that:\n"
            "- RVOL > 1.5x means institutional interest, < 1.0x means retail noise\n"
            "- Volume should SUSTAIN through the morning, not collapse after bar 1\n"
            "- Entry bar volume spikes (>50% jump) often mean block deals that won't follow through\n"
            "- Volume confirming price direction is bullish; divergence is bearish\n"
            "You only analyze VOLUME. Ignore price patterns, sector flows, momentum."
        ),
        data=vol_data,
        signals_text=signals_text,
        api_key=api_key,
    )

    print("S", end="", flush=True)
    trace.sector_expert = _run_expert(
        role="Sector Flow Expert",
        goal="Score each signal based on sector alignment — is the sector confirming this trade?",
        backstory=(
            "You are an expert in sector rotation and cross-stock correlation. You know that:\n"
            "- When the sector leader moves, laggards follow within 15-30 minutes\n"
            "- Trading AGAINST the sector leader is counter-trend — low probability\n"
            "- High sector breadth (>75% stocks confirming) is strong confirmation\n"
            "- Sectors can ROTATE intraday — early direction may reverse by bar 6\n"
            "- Unknown sector (no leader/laggard data) = flying blind\n"
            "You only analyze SECTOR DATA. Ignore volume, price patterns, momentum."
        ),
        data=sec_data,
        signals_text=signals_text,
        api_key=api_key,
    )

    print("P", end="", flush=True)
    trace.price_expert = _run_expert(
        role="Price Action Expert",
        goal="Score each signal based on candle structure — is the entry clean or messy?",
        backstory=(
            "You are an expert in candlestick analysis and price structure. You know that:\n"
            "- Entry bar body ratio > 60% = strong conviction candle; < 20% = indecision\n"
            "- Noise ratio < 1.8 = clean trend; > 3.5 = choppy whipsaw territory\n"
            "- Gap aligned with direction is bullish; counter-gap trades rarely work\n"
            "- Price above VWAP = institutional buyers zone; below = sellers zone\n"
            "- Candlestick patterns (engulfing, three soldiers, etc.) at key levels are meaningful\n"
            "You only analyze PRICE STRUCTURE. Ignore volume, sector, momentum."
        ),
        data=price_data,
        signals_text=signals_text,
        api_key=api_key,
    )

    print("M", end="", flush=True)
    trace.momentum_expert = _run_expert(
        role="Momentum Expert",
        goal="Score each signal based on directional momentum — is the move real or exhausted?",
        backstory=(
            "You are an expert in momentum and trend analysis. You know that:\n"
            "- Morning move aligned with direction = #1 predictor of winners\n"
            "- 3+ consecutive bars in direction = trend intact; 5+ = exhaustion risk\n"
            "- Pre-entry bars against direction = momentum divergence, setup may fail\n"
            "- Strong opening bar (>0.8% move, >60% body) predicts day direction\n"
            "- Price above EMA9 confirms short-term bullish momentum\n"
            "You only analyze MOMENTUM. Ignore volume, sector, price structure."
        ),
        data=mom_data,
        signals_text=signals_text,
        api_key=api_key,
    )

    print("R", end="", flush=True)
    trace.market_expert = _run_expert(
        role="Market Regime Expert",
        goal="Assess if today's broad market supports taking trades at all, and which direction",
        backstory=(
            "You are an expert in reading broad market regime. You know that:\n"
            "- >65% stocks moving one direction = trending day, trade WITH the trend\n"
            "- <40% either way + high flat count = choppy day, tight stops get whipsawed\n"
            "- Broad gap up/down that HOLDS = continuation likely; that FADES = reversal day\n"
            "- Choppy days destroy intraday strategies — fewer trades, wider stops\n"
            "- More than 50% stocks trending consistently = clean day for setups\n"
            "You assess the OVERALL MARKET. Score the market 1-10 for trading conditions."
        ),
        data=mkt_data,
        signals_text=signals_text,
        api_key=api_key,
    )

    # ─── Aggregator: reads all 5 reports, picks best 1-3 ───
    print("A", end="", flush=True)
    llm = _get_llm(api_key)
    aggregator = Agent(
        role="Trading Aggregator",
        goal="Read all 5 expert reports and pick the best 1-3 signals to trade",
        backstory=(
            "You are the final decision maker. You read 5 independent expert assessments:\n"
            "Volume, Sector, Price Action, Momentum, Market Regime.\n"
            "RULES:\n"
            "1. A signal needs at least 3 experts scoring it 7+ to be worth taking\n"
            "2. If Market Regime expert says choppy/dangerous, reduce position count\n"
            "3. Volume + Momentum alignment is the strongest combination\n"
            "4. One expert scoring 10 doesn't override three experts scoring 3\n"
            "5. Max 3 signals. One per sector. Quality over quantity.\n"
            "6. Output ONLY valid JSON."
        ),
        verbose=True,
        llm=llm,
    )

    agg_task = Task(
        description=(
            f"Read these 5 expert assessments and decide which signals to trade.\n\n"
            f"VOLUME EXPERT:\n{trace.volume_expert}\n\n"
            f"SECTOR EXPERT:\n{trace.sector_expert}\n\n"
            f"PRICE ACTION EXPERT:\n{trace.price_expert}\n\n"
            f"MOMENTUM EXPERT:\n{trace.momentum_expert}\n\n"
            f"MARKET REGIME EXPERT:\n{trace.market_expert}\n\n"
            f"Output ONLY valid JSON (no markdown, no explanation outside JSON):\n"
            f"{{\"ranked\": [{{\"symbol\": \"X\", \"direction\": \"LONG\", \"strategy\": \"lnz3\", "
            f"\"score\": 8, \"reason\": \"Vol: [X]. Sector: [X]. Price: [X]. Mom: [X]. Market: [X]. "
            f"Decision: [why]\"}}]}}\n\n"
            f"Only include score >= 6. Max 3 signals. One per sector."
        ),
        expected_output='Valid JSON with ranked signals referencing all 5 expert assessments',
        agent=aggregator,
    )

    crew = Crew(agents=[aggregator], tasks=[agg_task], process=Process.sequential, verbose=True)

    try:
        result = crew.kickoff()
        raw_output = str(result)
        trace.aggregator = raw_output

        start = raw_output.find('{')
        end = raw_output.rfind('}') + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw_output[start:end])
            ranked = parsed.get("ranked", [])
        else:
            ranked = []

        # Clean symbol names
        for r in ranked:
            sym = r.get("symbol", "")
            for prefix in ("LONG ", "SHORT "):
                if sym.startswith(prefix):
                    sym = sym[len(prefix):]
            r["symbol"] = sym.strip()

        trace.ranked = ranked
        return ranked, trace

    except Exception as e:
        logger.error("MoE aggregator failed: %s", e)
        trace.aggregator = f"ERROR: {e}"
        return [], trace
