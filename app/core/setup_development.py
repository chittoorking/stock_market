"""
Setup Development Tracker — traces how a trade setup develops bar by bar.

Like predicting a storm: you don't look at one reading. You watch
multiple independent signals CONVERGE over time.

For each bar from 0 to entry_bar, we compute 5 dimensions:
1. Volume conviction (building or dying?)
2. Sector flow (joining or diverging?)
3. Market breadth (growing or shrinking?)
4. Price structure (getting cleaner or choppier?)
5. Candlestick sentiment (bullish patterns forming?)

Each dimension has an EVIDENCE TIER (like a courtroom):
- PRIMARY EVIDENCE: Proven from data to strongly differentiate W vs L.
  Volume consistency + Market breadth. When these are against = high danger.
- SUPPORTING EVIDENCE: Moderate differentiator.
  Sector flow + Price structure. Add weight when confirming primary.
- CIRCUMSTANTIAL EVIDENCE: Weak differentiator on its own.
  Candlestick patterns. Only meaningful when primary + supporting already confirm.

The tiers are DATA for the LLM, not hardcoded rules.
The LLM sees the tier and weighs the evidence accordingly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# Evidence tiers — computed from historical data, presented AS DATA to LLM.
# The LLM reads these labels and weighs evidence accordingly.
EVIDENCE_TIERS = {
    "volume":  {"tier": "PRIMARY",        "label": "Volume [PRIMARY]",
                "why": "Volume dying throughout = trap. 70% of losers had vol issues vs 52% winners."},
    "breadth": {"tier": "PRIMARY",        "label": "Market Breadth [PRIMARY]",
                "why": "Breadth <35% = trading against the market. 30% of losers had this, 8% of winners."},
    "sector":  {"tier": "SUPPORTING",     "label": "Sector Flow [SUPPORTING]",
                "why": "Sector confirms direction in 62% of winners vs 55% of losers."},
    "price":   {"tier": "SUPPORTING",     "label": "Price Structure [SUPPORTING]",
                "why": "Choppy price (noise>3) caught 30% of losers."},
    "pattern": {"tier": "CIRCUMSTANTIAL", "label": "Candle Patterns [CIRCUMSTANTIAL]",
                "why": "Patterns alone don't differentiate. Only meaningful when primary evidence confirms."},
}


@dataclass
class BarReading:
    """One convergence reading at a specific bar."""
    bar_index: int
    timestamp: str = ""

    # 5 independent dimensions — each is +1 (confirms), 0 (neutral), -1 (against)
    volume_signal: int = 0        # Is volume building with direction?
    sector_signal: int = 0        # Is sector flow confirming?
    breadth_signal: int = 0       # Is market breadth supporting?
    price_signal: int = 0         # Is price structure clean?
    pattern_signal: int = 0       # Are candlestick patterns aligned?

    # Raw data behind each signal
    volume_detail: str = ""
    sector_detail: str = ""
    breadth_detail: str = ""
    price_detail: str = ""
    pattern_detail: str = ""

    @property
    def convergence_score(self) -> int:
        """How many dimensions agree? Range: -5 to +5."""
        return self.volume_signal + self.sector_signal + self.breadth_signal + \
               self.price_signal + self.pattern_signal

    @property
    def primary_score(self) -> int:
        """Score from PRIMARY evidence only (volume + breadth)."""
        return self.volume_signal + self.breadth_signal

    @property
    def supporting_score(self) -> int:
        """Score from SUPPORTING evidence (sector + price)."""
        return self.sector_signal + self.price_signal

    @property
    def confirming_count(self) -> int:
        """How many dimensions are +1 (confirming)?"""
        return sum(1 for s in [self.volume_signal, self.sector_signal,
                               self.breadth_signal, self.price_signal,
                               self.pattern_signal] if s > 0)

    @property
    def against_count(self) -> int:
        """How many dimensions are -1 (against)?"""
        return sum(1 for s in [self.volume_signal, self.sector_signal,
                               self.breadth_signal, self.price_signal,
                               self.pattern_signal] if s < 0)


@dataclass
class SetupDevelopment:
    """Full development trajectory of a trade setup."""
    symbol: str
    direction: str
    strategy: str
    readings: List[BarReading] = field(default_factory=list)

    def add_reading(self, reading: BarReading):
        self.readings.append(reading)

    @property
    def trajectory(self) -> List[int]:
        """Convergence score at each bar."""
        return [r.convergence_score for r in self.readings]

    @property
    def is_building(self) -> bool:
        """Is convergence BUILDING over time? (last 3 readings trending up)"""
        t = self.trajectory
        if len(t) < 3:
            return t[-1] > 0 if t else False
        return t[-1] >= t[-2] >= t[-3] and t[-1] >= 2

    @property
    def is_decaying(self) -> bool:
        """Is convergence DECAYING? (last 3 readings trending down)"""
        t = self.trajectory
        if len(t) < 3:
            return t[-1] < 0 if t else False
        return t[-1] <= t[-2] <= t[-3] or t[-1] <= 0

    @property
    def peak_convergence(self) -> int:
        """Highest convergence score reached."""
        return max(self.trajectory) if self.trajectory else 0

    @property
    def final_convergence(self) -> int:
        """Convergence score at entry bar."""
        return self.trajectory[-1] if self.trajectory else 0

    @property
    def convergence_stability(self) -> float:
        """How stable is convergence? Low variance = stable setup."""
        t = self.trajectory
        if len(t) < 2:
            return 0
        avg = sum(t) / len(t)
        variance = sum((x - avg) ** 2 for x in t) / len(t)
        return variance

    def format_for_llm(self) -> str:
        """Full trajectory table — for debugging only, not for LLM input."""
        lines = []
        lines.append(f"SETUP DEVELOPMENT: {self.direction} {self.symbol} [{self.strategy}]")
        for r in self.readings:
            s = lambda v: "+" if v > 0 else "-" if v < 0 else "."
            lines.append(f"  bar {r.bar_index}: Vol={s(r.volume_signal)} Mkt={s(r.breadth_signal)} "
                         f"Sect={s(r.sector_signal)} Price={s(r.price_signal)} Pat={s(r.pattern_signal)} "
                         f"| primary={r.primary_score:+d} total={r.convergence_score:+d}")
        return "\n".join(lines)

    def verification_line(self) -> str:
        """One-line verification for the LLM.
        This is what the signal was doing BEFORE entry — did the setup develop
        consistently (storm forming) or was it erratic (no storm)?"""
        if not self.readings:
            return "SETUP VERIFICATION: No data"

        primary_traj = [r.primary_score for r in self.readings]
        final_primary = primary_traj[-1] if primary_traj else 0
        final_total = self.final_convergence

        # Volume behavior across the development
        vol_readings = [(r.volume_signal, r.volume_detail) for r in self.readings]
        vol_dying = sum(1 for v, _ in vol_readings if v < 0)
        vol_confirmed = sum(1 for v, _ in vol_readings if v > 0)
        vol_total = len(vol_readings)

        if vol_dying > vol_total * 0.5:
            vol_story = f"VOLUME DYING ({vol_dying}/{vol_total} bars)"
        elif vol_confirmed > vol_total * 0.5:
            vol_story = f"Volume sustained ({vol_confirmed}/{vol_total} bars)"
        else:
            vol_story = "Volume mixed"

        # Breadth behavior
        breadth_readings = [r.breadth_signal for r in self.readings]
        breadth_against = sum(1 for b in breadth_readings if b < 0)
        breadth_with = sum(1 for b in breadth_readings if b > 0)

        if breadth_against > len(breadth_readings) * 0.4:
            breadth_story = f"Market against ({breadth_against}/{len(breadth_readings)} bars)"
        elif breadth_with > len(breadth_readings) * 0.5:
            breadth_story = f"Market with ({breadth_with}/{len(breadth_readings)} bars)"
        else:
            breadth_story = "Market neutral"

        # Primary trajectory direction
        if len(primary_traj) >= 3:
            if primary_traj[-1] > primary_traj[0]:
                primary_dir = "PRIMARY BUILDING"
            elif primary_traj[-1] < primary_traj[0]:
                primary_dir = "PRIMARY FADING"
            else:
                primary_dir = "PRIMARY FLAT"
        else:
            primary_dir = "PRIMARY UNKNOWN"

        # Storm verdict
        if final_primary >= 2:
            verdict = "VERIFIED: storm forming, primary evidence strong"
        elif final_primary >= 1 and final_total >= 1:
            verdict = "PARTIAL: one primary confirms, setup developing"
        elif vol_dying > vol_total * 0.5 and final_total >= 2:
            verdict = "WARNING: looks converged but volume dying throughout = possible trap"
        elif final_primary <= -1:
            verdict = "REJECTED: primary evidence against this trade"
        elif final_total <= -1:
            verdict = "WEAK: overall convergence negative"
        else:
            verdict = "NEUTRAL: insufficient evidence to verify or reject"

        return (
            f"SETUP CHECK [{self.symbol}]: {verdict} | "
            f"{vol_story} | {breadth_story} | {primary_dir} | "
            f"primary={final_primary:+d} total={final_total:+d}"
        )


def observe_setup_to_graph(
    symbol: str,
    direction: str,
    strategy: str,
    all_data: Dict[str, list],
    date: str,
    entry_bar: int,
    trade_graph,  # TradeDevGraph — writes events here
):
    """
    Observe how a signal's setup developed and write events to the graph.
    Called BEFORE entry decision. System observes, writes facts. Agent reads later.

    Writes simple events like:
      "volume sustained 4 of 6 bars"
      "sector confirmed from bar 2 onward"
      "market breadth dropped below 35% at bar 4"
      "price got choppy at bar 3"
    """
    setup = track_setup_development(symbol, direction, strategy, all_data, date, entry_bar)

    if not setup.readings:
        return

    total_bars = len(setup.readings)

    # Volume story — count both dying (-1) and fading (0 with "fading" detail)
    vol_dying = sum(1 for r in setup.readings if r.volume_signal < 0)
    vol_fading = sum(1 for r in setup.readings if "fading" in r.volume_detail or "dying" in r.volume_detail)
    vol_confirmed = sum(1 for r in setup.readings if r.volume_signal > 0)
    last_vol = setup.readings[-1]
    if vol_fading > total_bars * 0.5:
        trade_graph.write_signal_event(symbol, entry_bar,
            f"volume fading/dying {vol_fading} of {total_bars} bars — participants leaving")
    elif vol_confirmed > total_bars * 0.4:
        trade_graph.write_signal_event(symbol, entry_bar,
            f"volume confirmed {vol_confirmed} of {total_bars} bars — conviction sustained")

    # Sector story
    sect_with = sum(1 for r in setup.readings if r.sector_signal > 0)
    sect_against = sum(1 for r in setup.readings if r.sector_signal < 0)
    if sect_with > total_bars * 0.5:
        trade_graph.write_signal_event(symbol, entry_bar,
            f"sector confirmed {sect_with} of {total_bars} bars")
    elif sect_against > sect_with and sect_against >= 2:
        trade_graph.write_signal_event(symbol, entry_bar,
            f"sector turned against — {sect_against} of {total_bars} bars against")

    # Breadth story
    breadth_against = sum(1 for r in setup.readings if r.breadth_signal < 0)
    breadth_with = sum(1 for r in setup.readings if r.breadth_signal > 0)
    if breadth_against >= 2:
        trade_graph.write_signal_event(symbol, entry_bar,
            f"market breadth against {breadth_against} of {total_bars} bars — trading against the crowd")
    elif breadth_with > total_bars * 0.5:
        trade_graph.write_signal_event(symbol, entry_bar,
            f"broad market supporting {breadth_with} of {total_bars} bars")

    # Price story
    choppy_bars = sum(1 for r in setup.readings if r.price_signal < 0)
    clean_bars = sum(1 for r in setup.readings if r.price_signal > 0)
    if choppy_bars > total_bars * 0.4:
        trade_graph.write_signal_event(symbol, entry_bar,
            f"price action choppy {choppy_bars} of {total_bars} bars — whipsaw risk")
    elif clean_bars > total_bars * 0.5:
        trade_graph.write_signal_event(symbol, entry_bar,
            f"price action clean {clean_bars} of {total_bars} bars — efficient moves")

    # Overall convergence trend
    traj = setup.trajectory
    if len(traj) >= 3:
        if traj[-1] > traj[0] and traj[-1] >= 2:
            trade_graph.write_signal_event(symbol, entry_bar,
                f"convergence building: {traj[0]:+d} to {traj[-1]:+d} — setup strengthening")
        elif traj[-1] < traj[0] and traj[-1] <= 0:
            trade_graph.write_signal_event(symbol, entry_bar,
                f"convergence decaying: {traj[0]:+d} to {traj[-1]:+d} — setup falling apart")


def track_setup_development(
    symbol: str,
    direction: str,
    strategy: str,
    all_data: Dict[str, list],
    date: str,
    entry_bar: int,
) -> SetupDevelopment:
    """
    Trace how a setup develops from bar 0 to entry_bar.
    At each bar, compute 5 independent convergence dimensions.
    """
    from app.signals.base import get_sector, SECTOR_MAP

    setup = SetupDevelopment(symbol=symbol, direction=direction, strategy=strategy)

    bars = [b for b in all_data.get(symbol, []) if b['timestamp'][:10] == date]
    prev_bars = [b for b in all_data.get(symbol, []) if b['timestamp'][:10] < date]
    prev_close = prev_bars[-1]['close'] if prev_bars else None

    if not bars or len(bars) <= entry_bar:
        return setup

    sector = get_sector(symbol)
    sec_info = SECTOR_MAP.get(sector, {})
    leader = sec_info.get('leader', '')
    laggards = list(sec_info.get('laggards', []))
    sector_stocks = [s for s in [leader] + laggards if s and s != symbol]

    # Sample every 2 bars (don't need every single bar)
    sample_bars = list(range(0, entry_bar + 1, max(1, entry_bar // 5)))
    if entry_bar not in sample_bars:
        sample_bars.append(entry_bar)

    for bi in sample_bars:
        if bi >= len(bars):
            break

        reading = BarReading(
            bar_index=bi,
            timestamp=bars[bi].get('timestamp', '').split(' ')[1][:5] if 'timestamp' in bars[bi] else '',
        )

        # === 1. VOLUME CONVICTION ===
        if bi >= 2:
            # Compare recent volume to earlier volume
            recent_vol = sum(bars[j]['volume'] for j in range(max(0, bi - 1), bi + 1))
            early_vol = sum(bars[j]['volume'] for j in range(0, min(2, bi)))
            if early_vol > 0:
                vol_ratio = recent_vol / early_vol
                if vol_ratio > 0.8:
                    reading.volume_signal = 1
                    reading.volume_detail = f"sustained {vol_ratio:.1f}x"
                elif vol_ratio < 0.4:
                    reading.volume_signal = -1
                    reading.volume_detail = f"dying {vol_ratio:.1f}x"
                else:
                    reading.volume_detail = f"fading {vol_ratio:.1f}x"
        elif bi == 0:
            # First bar: check RVOL vs historical
            if prev_bars and len(prev_bars) >= 5:
                avg_vol = sum(b['volume'] for b in prev_bars[-5:]) / 5
                if avg_vol > 0:
                    rvol = bars[0]['volume'] / avg_vol
                    if rvol > 1.5:
                        reading.volume_signal = 1
                        reading.volume_detail = f"RVOL {rvol:.1f}x"
                    elif rvol < 0.5:
                        reading.volume_signal = -1
                        reading.volume_detail = f"low RVOL {rvol:.1f}x"

        # === 2. SECTOR FLOW ===
        if sector_stocks:
            sect_with = 0; sect_against = 0
            for ss in sector_stocks:
                ss_bars = [b for b in all_data.get(ss, []) if b['timestamp'][:10] == date]
                if ss_bars and len(ss_bars) > bi:
                    ss_move = (ss_bars[bi]['close'] - ss_bars[0]['open']) / ss_bars[0]['open'] * 100
                    if (direction == 'LONG' and ss_move > 0.1) or (direction == 'SHORT' and ss_move < -0.1):
                        sect_with += 1
                    elif (direction == 'LONG' and ss_move < -0.1) or (direction == 'SHORT' and ss_move > 0.1):
                        sect_against += 1

            total = len(sector_stocks)
            if total > 0:
                pct = sect_with / total
                if pct >= 0.67:
                    reading.sector_signal = 1
                    reading.sector_detail = f"{sect_with}/{total} confirm"
                elif sect_against > sect_with:
                    reading.sector_signal = -1
                    reading.sector_detail = f"{sect_against}/{total} against"
                else:
                    reading.sector_detail = f"{sect_with}/{total} mixed"

        # === 3. MARKET BREADTH ===
        mkt_with = 0; mkt_total = 0
        for ms, mbars in all_data.items():
            if ms == symbol: continue
            mdb = [b for b in mbars if b['timestamp'][:10] == date]
            if not mdb or len(mdb) <= bi: continue
            mkt_total += 1
            mm = (mdb[bi]['close'] - mdb[0]['open']) / mdb[0]['open'] * 100
            if (direction == 'LONG' and mm > 0) or (direction == 'SHORT' and mm < 0):
                mkt_with += 1

        if mkt_total > 0:
            breadth_pct = mkt_with / mkt_total * 100
            if breadth_pct >= 60:
                reading.breadth_signal = 1
                reading.breadth_detail = f"{breadth_pct:.0f}% with"
            elif breadth_pct <= 35:
                reading.breadth_signal = -1
                reading.breadth_detail = f"{breadth_pct:.0f}% against"
            else:
                reading.breadth_detail = f"{breadth_pct:.0f}% neutral"

        # === 4. PRICE STRUCTURE ===
        if bi >= 2:
            # Noise ratio: are bars moving efficiently or whipsawing?
            window = min(6, bi + 1)
            ranges = [bars[j]['high'] - bars[j]['low'] for j in range(max(0, bi - window + 1), bi + 1)]
            moves = [abs(bars[j]['close'] - bars[j - 1]['close'])
                     for j in range(max(1, bi - window + 2), bi + 1)]

            if moves and sum(moves) > 0:
                noise = sum(ranges) / sum(moves)
                if noise < 2.2:
                    reading.price_signal = 1
                    reading.price_detail = f"clean {noise:.1f}"
                elif noise > 3.0:
                    reading.price_signal = -1
                    reading.price_detail = f"choppy {noise:.1f}"
                else:
                    reading.price_detail = f"moderate {noise:.1f}"

            # Also check: is bar body ratio healthy?
            body = abs(bars[bi]['close'] - bars[bi]['open'])
            rng = bars[bi]['high'] - bars[bi]['low']
            if rng > 0:
                body_pct = body / rng * 100
                if body_pct < 25:
                    reading.price_signal = min(reading.price_signal, -1)
                    reading.price_detail += f" body={body_pct:.0f}%"

        elif bi == 0 and prev_close:
            # Gap alignment
            gap = (bars[0]['open'] - prev_close) / prev_close * 100
            gap_aligned = (direction == 'LONG' and gap > 0.3) or (direction == 'SHORT' and gap < -0.3)
            if gap_aligned:
                reading.price_signal = 1
                reading.price_detail = f"gap {gap:+.2f}% aligned"
            elif abs(gap) > 0.5 and not gap_aligned:
                reading.price_signal = -1
                reading.price_detail = f"gap {gap:+.2f}% AGAINST"
            else:
                reading.price_detail = f"gap {gap:+.2f}% small"

        # === 5. CANDLESTICK PATTERNS ===
        try:
            from app.agents.candle_patterns import detect_patterns
            pats = detect_patterns(bars, bi)
            bullish = 0; bearish = 0
            for p in pats:
                if p.direction == 'bullish':
                    bullish += 1
                elif p.direction == 'bearish':
                    bearish += 1

            if direction == 'LONG':
                if bullish > bearish and bullish >= 1:
                    reading.pattern_signal = 1
                    names = [p.name for p in pats if p.direction == 'bullish'][:2]
                    reading.pattern_detail = f"bullish: {','.join(names)}"
                elif bearish > bullish and bearish >= 1:
                    reading.pattern_signal = -1
                    names = [p.name for p in pats if p.direction == 'bearish'][:2]
                    reading.pattern_detail = f"bearish: {','.join(names)}"
            else:  # SHORT
                if bearish > bullish and bearish >= 1:
                    reading.pattern_signal = 1
                    names = [p.name for p in pats if p.direction == 'bearish'][:2]
                    reading.pattern_detail = f"bearish: {','.join(names)}"
                elif bullish > bearish and bullish >= 1:
                    reading.pattern_signal = -1
                    names = [p.name for p in pats if p.direction == 'bullish'][:2]
                    reading.pattern_detail = f"bullish: {','.join(names)}"
        except Exception:
            pass

        setup.add_reading(reading)

    return setup
