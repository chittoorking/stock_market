"""
Signal Enrichment — system computes data quality flags BEFORE LLMs see signals.

This is what the entity graph should capture about each signal:
- Volume health (confirmed, diverging, exhausted)
- Sector alignment (aligned, neutral, against)
- Price structure quality (clean setup vs noisy)
- Historical context (this setup worked/failed before)

The enrichment data gets written to the graph. LLMs query it.
No flag = no opinion. Red flag = data says "be careful". Green flag = data says "looks good".

This is the SYSTEM being smart so the LLM doesn't have to be.
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from app.agents.data_providers import SectorAnalyzer, VolumeProfiler, PriceStructure
from app.signals.base import get_sector, SECTOR_MAP


@dataclass
class SignalEnrichment:
    """Enrichment data attached to a signal before LLMs see it."""
    symbol: str = ""
    strategy: str = ""
    direction: str = ""

    # Flags
    flags: List[str] = field(default_factory=list)  # "GREEN:...", "RED:...", "YELLOW:..."

    # Computed data
    volume_health: str = "unknown"       # "confirmed", "diverging", "exhausted", "low"
    sector_alignment: str = "unknown"    # "strong_aligned", "aligned", "neutral", "against"
    price_quality: str = "unknown"       # "clean", "noisy", "exhausted"
    historical_edge: str = "unknown"     # "positive", "neutral", "negative"
    rvol: float = 0
    atr_pct: float = 0

    # Summary for LLM
    def summary(self) -> str:
        red = [f for f in self.flags if f.startswith("RED")]
        green = [f for f in self.flags if f.startswith("GREEN")]
        yellow = [f for f in self.flags if f.startswith("YELLOW")]
        parts = []
        if red: parts.append(f"RISKS: {'; '.join(r.split(':',1)[1] for r in red)}")
        if green: parts.append(f"SUPPORTS: {'; '.join(g.split(':',1)[1] for g in green)}")
        if yellow: parts.append(f"CAUTION: {'; '.join(y.split(':',1)[1] for y in yellow)}")
        return " | ".join(parts) if parts else "No flags"

    @property
    def red_flag_count(self) -> int:
        return sum(1 for f in self.flags if f.startswith("RED"))

    @property
    def green_flag_count(self) -> int:
        return sum(1 for f in self.flags if f.startswith("GREEN"))


def enrich_signal(
    symbol: str,
    direction: str,
    strategy: str,
    all_data: Dict[str, List[Dict]],
    date: str,
    bar_idx: int,
    memory=None,  # MarketMemory
) -> SignalEnrichment:
    """
    Compute ALL data quality flags for a signal.
    The system does this BEFORE any LLM sees the signal.
    """
    enrichment = SignalEnrichment(symbol=symbol, strategy=strategy, direction=direction)
    sector = get_sector(symbol)

    sym_bars = [b for b in all_data.get(symbol, []) if b["timestamp"][:10] == date]
    prev_bars = [b for b in all_data.get(symbol, []) if b["timestamp"][:10] < date]
    prev_close = prev_bars[-1]["close"] if prev_bars else None

    if not sym_bars or bar_idx >= len(sym_bars):
        enrichment.flags.append("RED:No data available")
        return enrichment

    # ─── 1. Volume Health ───
    vol = VolumeProfiler.compute(sym_bars, bar_idx)
    enrichment.rvol = vol.get("rvol_opening", 0)

    if "BEARISH" in vol.get("divergence", ""):
        enrichment.volume_health = "diverging"
        enrichment.flags.append("RED:Volume divergence — price up but volume dropping, smart money exiting")
    elif "exhaustion" in vol.get("divergence", ""):
        enrichment.volume_health = "exhausted"
        enrichment.flags.append("YELLOW:Selling exhaustion — volume drying up on down move")
    elif "confirmed" in vol.get("divergence", ""):
        enrichment.volume_health = "confirmed"
        enrichment.flags.append("GREEN:Volume confirms price direction")
    elif enrichment.rvol < 1.5:
        enrichment.volume_health = "low"
        enrichment.flags.append("YELLOW:Low RVOL ({:.1f}x) — weak conviction".format(enrichment.rvol))
    else:
        enrichment.volume_health = "neutral"

    # ─── 2. Sector Alignment ───
    sector_data = SectorAnalyzer.compute(all_data, date, bar_idx)
    sec_info = sector_data.get(sector, {})

    if sec_info:
        leader_chg = sec_info.get("leader_change_pct", 0)
        strength = sec_info.get("strength", 0)

        aligned = (direction == "LONG" and leader_chg > 0.3 and strength >= 0.67) or \
                  (direction == "SHORT" and leader_chg < -0.3 and strength >= 0.67)
        strong_aligned = aligned and abs(leader_chg) > 1.0
        against = (direction == "LONG" and leader_chg < -0.3) or \
                  (direction == "SHORT" and leader_chg > 0.3)

        if strong_aligned:
            enrichment.sector_alignment = "strong_aligned"
            enrichment.flags.append("GREEN:Strong sector flow ({} {:+.2f}%, {}% following)".format(
                sec_info.get("leader",""), leader_chg, int(strength*100)))
        elif aligned:
            enrichment.sector_alignment = "aligned"
            enrichment.flags.append("GREEN:Sector aligned ({:+.2f}%)".format(leader_chg))
        elif against:
            enrichment.sector_alignment = "against"
            enrichment.flags.append("RED:Sector AGAINST — leader {:+.2f}%, trading {} is counter-trend".format(
                leader_chg, direction))
        else:
            enrichment.sector_alignment = "neutral"
            enrichment.flags.append("YELLOW:Sector neutral — no strong flow")
    else:
        enrichment.sector_alignment = "unknown"
        enrichment.flags.append("RED:Unknown sector — no sector leader to confirm direction, no laggard flow data. 3/9 stubborn losses were unknown sector.")

    # ─── 3. Price Structure ───
    price = PriceStructure.compute(sym_bars, bar_idx, prev_close)
    enrichment.atr_pct = price.get("atr_pct", 0)

    if price.get("exhaustion", "none") != "none":
        enrichment.price_quality = "exhausted"
        enrichment.flags.append("YELLOW:Opening drive exhaustion detected — {}".format(price["exhaustion"]))
    elif enrichment.atr_pct > 0.5:
        enrichment.price_quality = "volatile"
        enrichment.flags.append("YELLOW:High ATR ({:.2f}%) — stock is volatile, needs wider stops".format(enrichment.atr_pct))
    else:
        enrichment.price_quality = "clean"

    # ─── 3b. VWAP Overextension ───
    vwap = price.get("vwap", 0)
    current_price = price.get("price", 0)
    if vwap > 0 and current_price > 0:
        vwap_dist_pct = (current_price - vwap) / vwap * 100
        if direction == "LONG" and vwap_dist_pct > 1.0:
            enrichment.flags.append("YELLOW:VWAP overextended +{:.2f}% — mean reversion risk on longs".format(vwap_dist_pct))
        elif direction == "SHORT" and vwap_dist_pct < -1.0:
            enrichment.flags.append("YELLOW:VWAP overextended {:.2f}% — mean reversion risk on shorts".format(vwap_dist_pct))
        elif direction == "LONG" and vwap_dist_pct < -0.3:
            enrichment.flags.append("GREEN:Price below VWAP — buying at discount for LONG")
        elif direction == "SHORT" and vwap_dist_pct > 0.3:
            enrichment.flags.append("GREEN:Price above VWAP — shorting at premium")

    # ─── 3c. Entry Bar Volume Spike (institutional dump detection) ───
    if bar_idx >= 1:
        entry_bar_vol = sym_bars[bar_idx]["volume"]
        avg_vol = sum(b["volume"] for b in sym_bars[:bar_idx]) / bar_idx if bar_idx > 0 else entry_bar_vol
        vol_spike = entry_bar_vol / avg_vol if avg_vol > 0 else 1

        if vol_spike > 10:
            enrichment.flags.append("RED:Entry bar volume spike {:.0f}x avg — single institution dump, not organic flow".format(vol_spike))
        elif vol_spike > 5:
            enrichment.flags.append("YELLOW:Entry bar volume {:.0f}x avg — unusual activity, may be block deal".format(vol_spike))

    # ─── 3d. Opening Bar Strength (predicts day direction) ───
    bar0 = sym_bars[0]
    bar0_move = (bar0["close"] - bar0["open"]) / bar0["open"] * 100
    bar0_body = abs(bar0["close"] - bar0["open"])
    bar0_range = bar0["high"] - bar0["low"]
    bar0_body_ratio = bar0_body / bar0_range * 100 if bar0_range > 0 else 0

    if direction == "LONG" and bar0_move > 0.8 and bar0_body_ratio > 60:
        enrichment.flags.append("GREEN:Strong opening bar +{:.2f}% (body {:.0f}%) — momentum day".format(bar0_move, bar0_body_ratio))
    elif direction == "SHORT" and bar0_move < -0.8 and bar0_body_ratio > 60:
        enrichment.flags.append("GREEN:Strong opening bar {:.2f}% (body {:.0f}%) — sell pressure day".format(bar0_move, bar0_body_ratio))
    elif direction == "LONG" and bar0_move < -0.5:
        enrichment.flags.append("YELLOW:Opening bar was bearish {:.2f}% — trading against open direction".format(bar0_move))
    elif direction == "SHORT" and bar0_move > 0.5:
        enrichment.flags.append("YELLOW:Opening bar was bullish +{:.2f}% — shorting against open direction".format(bar0_move))

    # ─── 3e. Consecutive Direction Bars (momentum vs exhaustion) ───
    consec = 0
    for i in range(bar_idx, 0, -1):
        bar_dir = sym_bars[i]["close"] > sym_bars[i]["open"]
        if (direction == "LONG" and bar_dir) or (direction == "SHORT" and not bar_dir):
            consec += 1
        else:
            break

    if consec >= 5:
        enrichment.flags.append("YELLOW:Move exhaustion — {0} consecutive {1} bars before entry, pullback likely".format(consec, direction.lower()))
    elif consec >= 3:
        enrichment.flags.append("GREEN:Momentum — {0} consecutive {1} bars, trend intact".format(consec, direction.lower()))

    # ─── 3f. Noise Ratio (clean vs choppy price action) ───
    if bar_idx >= 3:
        ranges = [sym_bars[i]["high"] - sym_bars[i]["low"] for i in range(max(0, bar_idx-5), bar_idx+1)]
        moves = [abs(sym_bars[i]["close"] - sym_bars[i-1]["close"]) for i in range(max(1, bar_idx-4), bar_idx+1)]
        noise = sum(ranges) / sum(moves) if moves and sum(moves) > 0 else 5
        if noise > 3.5:
            enrichment.flags.append("RED:Choppy price action (noise ratio {:.1f}) — bars wide but net move small, whipsaw risk".format(noise))
        elif noise < 1.8:
            enrichment.flags.append("GREEN:Clean trend (noise ratio {:.1f}) — bars move efficiently".format(noise))

    # ─── 3g. Opening Bar QUALITY (body ratio) — from data analysis ───
    # Stocks with bar0 body ratio < 40% are INDECISIVE openers → weak days
    # Winners have >50% body ratio. GRASIM lost with 10% body ratio.
    if bar0_body_ratio < 30:
        enrichment.flags.append("RED:Indecisive opening bar — body only {:.0f}% of range (wicks dominate), direction unclear".format(bar0_body_ratio))
    elif bar0_body_ratio < 45:
        enrichment.flags.append("YELLOW:Weak opening conviction — body {:.0f}% of range".format(bar0_body_ratio))
    elif bar0_body_ratio > 70:
        enrichment.flags.append("GREEN:Decisive opening bar — body {:.0f}% of range, strong conviction".format(bar0_body_ratio))

    # ─── 3h. Gap-Direction Alignment ───
    # NIFTY gapped DOWN -3.34% but signal was LONG → trading against the gap = dangerous
    if prev_close:
        gap_pct = (sym_bars[0]["open"] - prev_close) / prev_close * 100
        if direction == "LONG" and gap_pct < -1.0:
            enrichment.flags.append("RED:Trading LONG against a {:.2f}% gap DOWN — counter-gap trades rarely work intraday".format(gap_pct))
        elif direction == "SHORT" and gap_pct > 1.0:
            enrichment.flags.append("RED:Trading SHORT against a +{:.2f}% gap UP — counter-gap trades rarely work intraday".format(gap_pct))
        elif direction == "LONG" and gap_pct > 0.5:
            enrichment.flags.append("GREEN:Gap UP +{:.2f}% aligns with LONG direction".format(gap_pct))
        elif direction == "SHORT" and gap_pct < -0.5:
            enrichment.flags.append("GREEN:Gap DOWN {:.2f}% aligns with SHORT direction".format(gap_pct))

    # ─── 3i. Stock vs Sector Leader Performance ───
    # From data: losers lag their sector leader by -1.21% avg, winners only -0.16%
    # If a stock is underperforming its sector leader by >1%, it's weak for a reason.
    sec_info = SECTOR_MAP.get(sector, {})
    leader_sym = sec_info.get("leader", "")
    if leader_sym and leader_sym != symbol:
        leader_bars = [b for b in all_data.get(leader_sym, []) if b["timestamp"][:10] == date]
        if leader_bars and len(leader_bars) > bar_idx:
            leader_move = (leader_bars[bar_idx]["close"] - leader_bars[0]["open"]) / leader_bars[0]["open"] * 100
            stock_move = (sym_bars[bar_idx]["close"] - sym_bars[0]["open"]) / sym_bars[0]["open"] * 100
            vs_leader = stock_move - leader_move

            if direction == "LONG" and vs_leader < -1.5:
                enrichment.flags.append(
                    "RED:Stock lagging sector leader by {:.2f}% — {} is at {:+.2f}% but {} at {:+.2f}%. Something is wrong with this stock.".format(
                        abs(vs_leader), leader_sym, leader_move, symbol, stock_move))
            elif direction == "LONG" and vs_leader < -0.8:
                enrichment.flags.append(
                    "YELLOW:Stock underperforming leader by {:.2f}% — may catch up or may be weak.".format(abs(vs_leader)))
            elif direction == "SHORT" and vs_leader > 1.5:
                enrichment.flags.append(
                    "RED:Stock outperforming leader by +{:.2f}% but trying to SHORT — stock has relative strength.".format(vs_leader))
            elif direction == "LONG" and vs_leader > 0.5:
                enrichment.flags.append(
                    "GREEN:Stock outperforming sector leader by +{:.2f}% — relative strength confirms LONG.".format(vs_leader))
            elif direction == "SHORT" and vs_leader < -0.5:
                enrichment.flags.append(
                    "GREEN:Stock underperforming leader by {:.2f}% — relative weakness confirms SHORT.".format(abs(vs_leader)))

    # ─── 3j. Volume Sustainability (second half vs first half) ───
    # From data: losers have vol_ratio 0.54 (dying), winners 0.67 (sustained)
    if bar_idx >= 5:
        vol_first = sum(sym_bars[i]["volume"] for i in range(3))
        vol_second = sum(sym_bars[i]["volume"] for i in range(3, min(6, bar_idx+1)))
        vol_sustain = vol_second / vol_first if vol_first > 0 else 0
        if vol_sustain < 0.35:
            enrichment.flags.append(
                "RED:Volume collapsing — second half {:.0f}% of first half. Buyers/sellers have left.".format(vol_sustain * 100))
        elif vol_sustain < 0.50:
            enrichment.flags.append(
                "YELLOW:Volume fading — second half {:.0f}% of first half.".format(vol_sustain * 100))
        elif vol_sustain > 0.80:
            enrichment.flags.append(
                "GREEN:Volume sustained — second half {:.0f}% of first half. Conviction holding.".format(vol_sustain * 100))

    # ─── 3k. Entry Bar Volume Spike (the #1 loser predictor) ───
    # From data: ALL 5 losers had >50% volume spike on entry bar.
    # ZERO winners had >50% spike. This is a single institution's exit order
    # spiking the price — no follow-through after.
    if bar_idx >= 1:
        prev_bar_vol = sym_bars[bar_idx - 1]["volume"]
        entry_bar_vol = sym_bars[bar_idx]["volume"]
        if prev_bar_vol > 0:
            entry_vol_spike = (entry_bar_vol - prev_bar_vol) / prev_bar_vol * 100
            if entry_vol_spike > 100:
                enrichment.flags.append(
                    "RED:ENTRY BAR VOLUME SPIKE +{:.0f}% — single institution order, NOT organic flow. Price will revert.".format(entry_vol_spike))
            elif entry_vol_spike > 50:
                enrichment.flags.append(
                    "RED:Entry bar volume jumped +{:.0f}% vs previous bar — likely a block order, weak follow-through expected.".format(entry_vol_spike))
            elif entry_vol_spike < -30:
                enrichment.flags.append(
                    "GREEN:Volume declining on entry bar ({:+.0f}%) — no spike, organic price action.".format(entry_vol_spike))

    # ─── 3l. Sector Breadth (how many sector stocks confirm this direction?) ───
    # From data: losers had 0-2 sector stocks confirming. Winners had 3-4.
    sector_stocks = list(sec_info.get("laggards", []))
    if leader_sym: sector_stocks.append(leader_sym)
    sector_stocks = [s for s in sector_stocks if s != symbol]

    if sector_stocks:
        sector_with = 0
        sector_against = 0
        for ss in sector_stocks:
            ss_bars = [b for b in all_data.get(ss, []) if b["timestamp"][:10] == date]
            if ss_bars and len(ss_bars) > bar_idx:
                ss_move = (ss_bars[bar_idx]["close"] - ss_bars[0]["open"]) / ss_bars[0]["open"] * 100
                if (direction == "LONG" and ss_move > 0.1) or (direction == "SHORT" and ss_move < -0.1):
                    sector_with += 1
                elif (direction == "LONG" and ss_move < -0.1) or (direction == "SHORT" and ss_move > 0.1):
                    sector_against += 1

        total_sector = len(sector_stocks)
        breadth = sector_with / total_sector if total_sector > 0 else 0

        if breadth >= 0.75:
            enrichment.flags.append("GREEN:Strong sector breadth — {}/{} sector stocks confirm {} direction.".format(
                sector_with, total_sector, direction))
        elif breadth <= 0.25 and total_sector >= 3:
            enrichment.flags.append("RED:Weak sector breadth — only {}/{} stocks confirm. This stock is ALONE in its direction.".format(
                sector_with, total_sector))
        elif sector_against > sector_with:
            enrichment.flags.append("YELLOW:More sector stocks moving AGAINST ({}) than WITH ({}) this direction.".format(
                sector_against, sector_with))

    # ─── 3m. Broader Market Alignment ───
    # Check what % of the entire market is moving with this direction
    market_with = 0
    market_total = 0
    for ms, mbars_all in all_data.items():
        if ms in ('NIFTY_50', 'NIFTY_BANK', symbol): continue
        mdb = [b for b in mbars_all if b["timestamp"][:10] == date]
        if not mdb or len(mdb) <= bar_idx: continue
        market_total += 1
        mm = (mdb[bar_idx]["close"] - mdb[0]["open"]) / mdb[0]["open"] * 100
        if (direction == "LONG" and mm > 0) or (direction == "SHORT" and mm < 0):
            market_with += 1

    if market_total > 0:
        market_pct = market_with / market_total * 100
        if market_pct >= 70:
            enrichment.flags.append("GREEN:Broad market confirms — {:.0f}% of stocks moving {}.".format(market_pct, direction))
        elif market_pct <= 30:
            enrichment.flags.append("RED:Trading AGAINST the broad market — only {:.0f}% of stocks moving {}.".format(market_pct, direction))

    # ─── 3n. Low-Energy Check ───
    if abs(bar0_move) < 0.3 and vol.get("volume_trend_6bar", 0) < -30:
        enrichment.flags.append("RED:Low-energy stock — weak opening ({:+.2f}%) + declining volume = not enough movement to be profitable".format(bar0_move))

    # ─── 3o. WINDOW 1 LEARNINGS (stateless, proven from 18 loss analysis) ───
    # From walk-forward Window 1: 0% of winners had these, 33-50% of losers did.
    # These are HARD disqualifiers — the data is unanimous.

    # Entry bar body ratio: winners ALWAYS >30%, losers often <30%
    entry_b = sym_bars[bar_idx]
    eb_body = abs(entry_b["close"] - entry_b["open"])
    eb_range = entry_b["high"] - entry_b["low"]
    eb_body_ratio = eb_body / eb_range * 100 if eb_range > 0 else 0

    if eb_body_ratio < 20:
        enrichment.flags.append("RED:Entry bar body only {:.0f}% of range — extreme indecision, 0% of winners had this.".format(eb_body_ratio))
    elif eb_body_ratio < 35:
        enrichment.flags.append("RED:Entry bar body {:.0f}% — weak conviction candle. Window 1: 0% winners below 30%.".format(eb_body_ratio))

    # Sector breadth: winners ALWAYS >33%, losers often <=33%
    # (This complements the earlier sector breadth check but uses the proven threshold)
    if sector_stocks:
        sect_with = 0
        for ss in sector_stocks:
            ss_bars = [b for b in all_data.get(ss, []) if b["timestamp"][:10] == date]
            if ss_bars and len(ss_bars) > bar_idx:
                ss_move = (ss_bars[bar_idx]["close"] - ss_bars[0]["open"]) / ss_bars[0]["open"] * 100
                if (direction == "LONG" and ss_move > 0.1) or (direction == "SHORT" and ss_move < -0.1):
                    sect_with += 1
        sect_pct = sect_with / len(sector_stocks) * 100 if sector_stocks else 0
        if sect_pct <= 25:
            enrichment.flags.append("RED:Sector breadth {:.0f}% — almost no stocks confirm. Window 1: 0% winners below 33%.".format(sect_pct))

    # ─── 3p. INDICATOR CONTEXT (VWAP, EMA, RSI — data for LLM, not signals) ───
    closes = [b["close"] for b in sym_bars[:bar_idx + 1]]

    # VWAP position
    tp_vol = sum((b["high"]+b["low"]+b["close"])/3 * b["volume"] for b in sym_bars[:bar_idx+1])
    cum_vol = sum(b["volume"] for b in sym_bars[:bar_idx+1])
    vwap_val = tp_vol / cum_vol if cum_vol > 0 else sym_bars[bar_idx]["close"]
    vwap_dist = (sym_bars[bar_idx]["close"] - vwap_val) / vwap_val * 100

    if direction == "LONG" and vwap_dist > 0.1:
        enrichment.flags.append("GREEN:Price above VWAP by {:.2f}% — institutional buyers' zone.".format(vwap_dist))
    elif direction == "LONG" and vwap_dist < -0.3:
        enrichment.flags.append("YELLOW:Price {:.2f}% below VWAP — buying below institutional average, may need to wait for VWAP reclaim.".format(abs(vwap_dist)))
    elif direction == "SHORT" and vwap_dist < -0.1:
        enrichment.flags.append("GREEN:Price below VWAP by {:.2f}% — institutional sellers' zone.".format(abs(vwap_dist)))
    elif direction == "SHORT" and vwap_dist > 0.3:
        enrichment.flags.append("YELLOW:Price +{:.2f}% above VWAP — shorting above institutional average, risky.".format(vwap_dist))

    # EMA trend alignment
    if len(closes) >= 21:
        def _ema_calc(data, period):
            mult = 2 / (period + 1)
            val = sum(data[:period]) / period
            for p in data[period:]: val = (p - val) * mult + val
            return val
        ema9 = _ema_calc(closes, 9)
        ema21 = _ema_calc(closes, 21)

        if direction == "LONG" and ema9 > ema21:
            enrichment.flags.append("GREEN:EMA trend bullish — EMA9 ({:.2f}) > EMA21 ({:.2f}), short-term momentum supports LONG.".format(ema9, ema21))
        elif direction == "LONG" and ema9 < ema21:
            enrichment.flags.append("RED:EMA trend bearish — EMA9 ({:.2f}) < EMA21 ({:.2f}), shorting-term momentum AGAINST LONG.".format(ema9, ema21))
        elif direction == "SHORT" and ema9 < ema21:
            enrichment.flags.append("GREEN:EMA trend bearish — EMA9 ({:.2f}) < EMA21 ({:.2f}), momentum supports SHORT.".format(ema9, ema21))
        elif direction == "SHORT" and ema9 > ema21:
            enrichment.flags.append("RED:EMA trend bullish — EMA9 ({:.2f}) > EMA21 ({:.2f}), momentum AGAINST SHORT.".format(ema9, ema21))

    # RSI context
    if len(closes) >= 15:
        gains = [max(0, closes[i]-closes[i-1]) for i in range(1, len(closes))]
        loss_l = [max(0, closes[i-1]-closes[i]) for i in range(1, len(closes))]
        ag = sum(gains[-14:])/14; al = sum(loss_l[-14:])/14
        rsi_val = 100 - 100/(1+ag/al) if al > 0 else 100

        if direction == "LONG" and rsi_val < 35:
            enrichment.flags.append("GREEN:RSI {:.0f} oversold — supports LONG, bounce likely.".format(rsi_val))
        elif direction == "LONG" and rsi_val > 70:
            enrichment.flags.append("RED:RSI {:.0f} overbought — risky LONG, buying at the top.".format(rsi_val))
        elif direction == "SHORT" and rsi_val > 65:
            enrichment.flags.append("GREEN:RSI {:.0f} overbought — supports SHORT, reversal likely.".format(rsi_val))
        elif direction == "SHORT" and rsi_val < 30:
            enrichment.flags.append("RED:RSI {:.0f} oversold — risky SHORT, selling at the bottom.".format(rsi_val))
        else:
            enrichment.flags.append("YELLOW:RSI {:.0f} neutral.".format(rsi_val))

    # Morning alignment (100% of target-hit winners had this)
    morning_move = (sym_bars[bar_idx]["close"] - sym_bars[0]["open"]) / sym_bars[0]["open"] * 100
    morning_aligned = (direction == "LONG" and morning_move > 0) or (direction == "SHORT" and morning_move < 0)
    if morning_aligned:
        enrichment.flags.append("GREEN:Morning move {:+.2f}% aligns with {} — 100% of winners had this.".format(morning_move, direction))
    else:
        enrichment.flags.append("RED:Morning move {:+.2f}% AGAINST {} — 0% of winners traded against morning.".format(morning_move, direction))

    # ─── 3q. WINDOW 2 LEARNINGS ───

    # Gap-direction conflict: ANY gap against direction = problem
    # Window 2: 0% WR on trades against the gap
    if prev_close:
        gap_pct_w2 = (sym_bars[0]["open"] - prev_close) / prev_close * 100
        if direction == "LONG" and gap_pct_w2 < -0.05:
            enrichment.flags.append("RED:Gap down {:.2f}% but going LONG — Window 2: 0% WR against gap.".format(gap_pct_w2))
        elif direction == "SHORT" and gap_pct_w2 > 0.05:
            enrichment.flags.append("RED:Gap up +{:.2f}% but going SHORT — Window 2: 0% WR against gap.".format(gap_pct_w2))

    # Pre-entry momentum: if majority of last 6 bars are AGAINST direction, skip
    if bar_idx >= 5:
        with_count = 0; against_count = 0
        for i in range(max(0, bar_idx-5), bar_idx+1):
            bar_green = sym_bars[i]["close"] > sym_bars[i]["open"]
            if (direction == "LONG" and bar_green) or (direction == "SHORT" and not bar_green):
                with_count += 1
            else:
                against_count += 1
        if against_count > with_count + 1:
            enrichment.flags.append("RED:Pre-entry momentum AGAINST — {0} of last 6 bars move against {1} direction.".format(against_count, direction))

    # Volume collapse on entry (not spike, but DEATH)
    if bar_idx >= 1:
        prev_v = sym_bars[bar_idx - 1]["volume"]
        curr_v = sym_bars[bar_idx]["volume"]
        if prev_v > 0:
            vol_change_w2 = (curr_v - prev_v) / prev_v * 100
            if vol_change_w2 < -40:
                enrichment.flags.append("RED:Volume COLLAPSED {:.0f}% on entry bar — no participants, no follow-through.".format(vol_change_w2))

    # ─── 3q. Candlestick Patterns at Entry (35+ textbook patterns) ───
    try:
        from app.agents.candle_patterns import detect_patterns
        candle_pats = detect_patterns(sym_bars, bar_idx)
        for cp in candle_pats:
            if cp.reliability == "high":
                if (cp.direction == "bullish" and direction == "LONG") or (cp.direction == "bearish" and direction == "SHORT"):
                    enrichment.flags.append(f"GREEN:{cp.name} ({cp.reliability}) — {cp.meaning[:70]}")
                elif (cp.direction == "bullish" and direction == "SHORT") or (cp.direction == "bearish" and direction == "LONG"):
                    enrichment.flags.append(f"RED:{cp.name} AGAINST direction — {cp.meaning[:70]}")
            elif cp.reliability == "moderate":
                if (cp.direction == "bullish" and direction == "LONG") or (cp.direction == "bearish" and direction == "SHORT"):
                    enrichment.flags.append(f"GREEN:{cp.name} — {cp.meaning[:70]}")
                elif cp.direction == "neutral":
                    enrichment.flags.append(f"YELLOW:{cp.name} — {cp.meaning[:70]}")
    except Exception:
        pass

    # ─── 3q. Technical Indicators at Entry ───
    try:
        from app.agents.technical_data import TechnicalDataProvider
    except ImportError:
        TechnicalDataProvider = None
    if TechnicalDataProvider is None:
        return enrichment
    tech = TechnicalDataProvider.compute(sym_bars, bar_idx)
    if tech.get("patterns"):
        for p in tech["patterns"][:2]:  # Top 2 patterns
            if any(word in p for word in ["bullish", "hammer", "soldiers", "golden"]):
                if direction == "LONG":
                    enrichment.flags.append(f"GREEN:Chart pattern supports LONG — {p}")
                else:
                    enrichment.flags.append(f"YELLOW:Bullish chart pattern but signal is SHORT — {p}")
            elif any(word in p for word in ["bearish", "shooting", "crows", "death"]):
                if direction == "SHORT":
                    enrichment.flags.append(f"GREEN:Chart pattern supports SHORT — {p}")
                else:
                    enrichment.flags.append(f"YELLOW:Bearish chart pattern but signal is LONG — {p}")
            elif "doji" in p or "inside" in p or "narrow" in p:
                enrichment.flags.append(f"YELLOW:Indecision pattern — {p}")

    if tech.get("indicators", {}).get("rsi"):
        rsi = tech["indicators"]["rsi"]
        if direction == "LONG" and rsi < 35:
            enrichment.flags.append(f"GREEN:RSI {rsi:.0f} oversold — supports LONG entry, bounce likely")
        elif direction == "SHORT" and rsi > 65:
            enrichment.flags.append(f"GREEN:RSI {rsi:.0f} overbought — supports SHORT entry")
        elif direction == "LONG" and rsi > 70:
            enrichment.flags.append(f"RED:RSI {rsi:.0f} overbought — LONG at top, risky")
        elif direction == "SHORT" and rsi < 30:
            enrichment.flags.append(f"RED:RSI {rsi:.0f} oversold — SHORT at bottom, risky")

    if tech.get("indicators", {}).get("moving_averages"):
        for ma in tech["indicators"]["moving_averages"]:
            if "GOLDEN CROSS" in ma and direction == "LONG":
                enrichment.flags.append(f"GREEN:{ma}")
            elif "DEATH CROSS" in ma and direction == "SHORT":
                enrichment.flags.append(f"GREEN:{ma}")
            elif "GOLDEN CROSS" in ma and direction == "SHORT":
                enrichment.flags.append(f"RED:Golden cross forming but trying to SHORT")
            elif "DEATH CROSS" in ma and direction == "LONG":
                enrichment.flags.append(f"RED:Death cross forming but trying to LONG")

    # ─── 4. Historical Edge (from memory graph) ───
    if memory:
        # Query memory for this exact setup
        similar = memory.query_similar_conditions(sector, direction, strategy)
        if "0% WR" in similar or "P&L=-" in similar:
            enrichment.historical_edge = "negative"
            enrichment.flags.append("RED:Similar setup lost before — " + similar[:60])
        elif "100% WR" in similar or "WR, P&L=+" in similar:
            enrichment.historical_edge = "positive"
            enrichment.flags.append("GREEN:Similar setup won before — " + similar[:60])
        else:
            enrichment.historical_edge = "neutral"

        # Sector history
        sec_hist = memory.query_sector_history(sector)
        if "P&L=-" in sec_hist:
            enrichment.flags.append("YELLOW:Sector has negative history — " + sec_hist[:50])

    return enrichment


def enrich_all_signals(
    signals: list,
    all_data: Dict,
    date: str,
    bar_idx: int,
    memory=None,
) -> Dict[str, SignalEnrichment]:
    """Enrich all signals. Returns {signal_id: enrichment}."""
    enrichments = {}
    for sig in signals:
        e = enrich_signal(
            sig.symbol, sig.direction, sig.strategy_name,
            all_data, date, bar_idx, memory,
        )
        enrichments[sig.id] = e
    return enrichments


def format_enriched_signals(signals: list, enrichments: Dict[str, SignalEnrichment]) -> str:
    """Format signals with their enrichment flags for LLM consumption."""
    lines = []
    for sig in signals:
        e = enrichments.get(sig.id)
        if not e:
            lines.append(f"[{sig.strategy_name}] {sig.direction} {sig.symbol} conf={sig.raw_confidence:.0%}")
            continue

        red_count = e.red_flag_count
        green_count = e.green_flag_count

        flag_str = ""
        if red_count >= 2:
            flag_str = " [BLOCKED: multiple red flags]"
        elif red_count == 1:
            flag_str = " [CAUTION: 1 red flag]"
        elif green_count >= 2:
            flag_str = " [STRONG: multiple green flags]"

        lines.append(
            f"[{sig.strategy_name}] {sig.direction} {sig.symbol} ({e.sector_alignment}) "
            f"conf={sig.raw_confidence:.0%}{flag_str}\n"
            f"  {e.summary()}"
        )

    return "\n".join(lines)
