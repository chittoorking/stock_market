"""
Autonomous Trading Pipeline — the Level 3 engine.

This is the heartbeat of the bot. It runs continuously and does EVERYTHING:

    ┌──────────────────────────────────────────────────────────┐
    │                    AUTONOMOUS LOOP                        │
    │                                                           │
    │   SCAN ──→ ANALYZE ──→ DECIDE ──→ EXECUTE ──→ MONITOR   │
    │     ↑                                            │        │
    │     │         LEARN ←── REVIEW ←── CLOSE ←───────┘        │
    │     │           │                                         │
    │     └───────────┘  (weights adjust, loop continues)       │
    └──────────────────────────────────────────────────────────┘

No human in the loop. The bot decides:
- WHAT to trade (scans universe, picks best signals)
- WHEN to trade (only when RL confidence is high enough)
- HOW MUCH to trade (Kelly criterion position sizing)
- WHEN to exit (SL/TP or regime change or signal reversal)
- WHAT TO LEARN (post-trade review feeds back into weights)
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from app.core.config import settings
from app.core.conversation_state import transition_state, get_state, TradingState
from app.core.session_context import (
    push_signal, get_active_positions, update_position_cache,
    get_risk_state, update_risk_state,
)
from app.core.agui_events.emit import (
    emit_signal, emit_trade_executed, emit_risk_alert,
    emit_text, emit_activity, emit_portfolio,
)
from app.ai_services.signal_analyzer import signal_analyzer
from app.ai_services.reinforcement_engine import rl_engine, TradeOutcome
from app.services.market_data_service import market_data_service
from app.services.broker_service import broker_service
from app.services.risk_service import risk_service
from app.services.notification_service import notify_all

logger = logging.getLogger(__name__)


# ─── Configuration ───

class AutoConfig:
    """Autonomous pipeline configuration."""
    SCAN_INTERVAL_SECONDS = 300       # Scan every 5 minutes
    MONITOR_INTERVAL_SECONDS = 30     # Check positions every 30 seconds
    REVIEW_INTERVAL_SECONDS = 3600    # Meta-review every hour
    META_REVIEW_INTERVAL = 86400      # Full meta-review daily

    MIN_CONFIDENCE_TO_TRADE = 0.65    # RL engine must be this confident
    MAX_SIGNALS_PER_SCAN = 3          # Don't flood with signals
    COOLDOWN_AFTER_LOSS = 300         # 5 min cooldown after a loss

    # Scan universe — what symbols to watch
    SCAN_UNIVERSES = {
        "indian": [
            "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
            "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
            "LT", "AXISBANK", "MARUTI", "TITAN", "WIPRO",
            "BAJFINANCE", "HCLTECH", "TATAMOTORS", "SUNPHARMA", "ADANIENT",
        ],
        "us": [
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
            "META", "TSLA", "JPM", "V", "WMT",
        ],
        "crypto": [
            "BTC", "ETH", "SOL", "BNB", "XRP",
        ],
    }

    # Which markets to scan (configurable per session)
    ACTIVE_MARKETS = ["indian"]
    ACTIVE_TIMEFRAMES = ["1h", "1d"]

    # Position sizing (Kelly criterion simplified)
    BASE_POSITION_PCT = 0.05   # 5% of portfolio per trade (default)
    MAX_POSITION_PCT = 0.10    # Never more than 10%
    MIN_POSITION_PCT = 0.02    # At least 2% to be meaningful


class AutonomousPipeline:
    """
    The fully autonomous trading loop.
    Starts as a background task, runs forever.
    """

    def __init__(self, session_id: str = "auto"):
        self.session_id = session_id
        self.running = False
        self._last_loss_at: Optional[datetime] = None
        self._trades_today: int = 0
        self._pnl_today: float = 0.0

    async def start(self):
        """Start the autonomous pipeline. Spawns 3 concurrent loops."""
        self.running = True
        logger.info("Autonomous pipeline starting for session=%s", self.session_id)

        await notify_all(
            f"🤖 Autonomous trading started.\n"
            f"Markets: {', '.join(AutoConfig.ACTIVE_MARKETS)}\n"
            f"Timeframes: {', '.join(AutoConfig.ACTIVE_TIMEFRAMES)}\n"
            f"Min confidence: {AutoConfig.MIN_CONFIDENCE_TO_TRADE:.0%}",
            level="info",
        )

        # Run 3 loops concurrently
        await asyncio.gather(
            self._scan_loop(),
            self._monitor_loop(),
            self._review_loop(),
        )

    async def stop(self):
        """Graceful stop."""
        self.running = False
        logger.info("Autonomous pipeline stopping")
        await notify_all("🛑 Autonomous trading stopped.", level="warning")

    # ─── Loop 1: SCAN → ANALYZE → DECIDE → EXECUTE ───

    async def _scan_loop(self):
        """
        Main scan loop. Every N minutes:
        1. Scan all configured markets
        2. Run technical analysis on interesting movers
        3. Generate signals
        4. If RL engine approves → auto-execute
        """
        while self.running:
            try:
                await self._run_scan_cycle()
            except Exception as e:
                logger.error("Scan cycle failed: %s", e, exc_info=True)

            await asyncio.sleep(AutoConfig.SCAN_INTERVAL_SECONDS)

    async def _run_scan_cycle(self):
        """
        Single scan cycle — uses PROVEN STRATEGIES as signal sources.
        The agent reasons about signals, not raw indicators.
        """
        await emit_activity(self.session_id, "scanning_markets", True)

        # Check cooldown after loss
        if self._last_loss_at:
            elapsed = (datetime.now(timezone.utc) - self._last_loss_at).total_seconds()
            if elapsed < AutoConfig.COOLDOWN_AFTER_LOSS:
                logger.info("Cooldown active (%.0fs remaining)", AutoConfig.COOLDOWN_AFTER_LOSS - elapsed)
                return

        # ─── Step 1: Collect signals from ALL proven strategies ───
        from app.signals.proven_strategies import (
            GapAndGoSignal, ORBSignal, CascadeSignal,
            LenzSignal, AftershockSignal, GapDecaySignal,
        )
        from app.signals.aggregator import signal_aggregator
        from app.signals.base import SECTOR_MAP

        all_signals = []

        for market in AutoConfig.ACTIVE_MARKETS:
            symbols = AutoConfig.SCAN_UNIVERSES.get(market, [])

            for symbol in symbols:
                try:
                    bars = await self._get_bars(symbol, "5m")
                    if not bars or len(bars) < 7:
                        continue

                    prev_close = await self._get_prev_close(symbol)

                    # Run each strategy's scanner
                    # GG8 — Gap & Go
                    if prev_close:
                        sig = GapAndGoSignal.scan(symbol, bars, prev_close)
                        if sig:
                            all_signals.append(sig)

                    # ORB — Opening Range Breakout
                    sig = ORBSignal.scan(symbol, bars)
                    if sig:
                        all_signals.append(sig)

                    # LNZ3 — Lenz Back-EMF
                    sig = LenzSignal.scan(symbol, bars)
                    if sig:
                        all_signals.append(sig)

                    # AFT7 — Aftershock
                    sig = AftershockSignal.scan(symbol, bars)
                    if sig:
                        all_signals.append(sig)

                    # GDR4 — Gap Decay Rate
                    if prev_close:
                        sig = GapDecaySignal.scan(symbol, bars, prev_close)
                        if sig:
                            all_signals.append(sig)

                except Exception as e:
                    logger.debug("Strategy scan failed for %s: %s", symbol, e)
                    continue

            # Cascade strategies need sector-level scanning
            for sector_name, sector_info in SECTOR_MAP.items():
                try:
                    leader = sector_info["leader"]
                    leader_bars = await self._get_bars(leader, "5m")
                    if not leader_bars:
                        continue

                    laggard_bars = {}
                    for lag in sector_info["laggards"]:
                        lb = await self._get_bars(lag, "5m")
                        if lb:
                            laggard_bars[lag] = lb

                    cascade_sigs = CascadeSignal.scan(leader_bars, laggard_bars, sector_name)
                    all_signals.extend(cascade_sigs)
                except Exception as e:
                    logger.debug("Cascade scan failed for %s: %s", sector_name, e)

        # ─── Step 2: Aggregate, score, and rank signals ───
        regime = "unknown"
        if all_signals:
            # Detect regime from first available data
            try:
                first_symbol = all_signals[0].symbol
                data = await market_data_service.get_quote(first_symbol, "1h", include_indicators=True)
                if data:
                    regime = await rl_engine.detect_regime(first_symbol, data)
            except Exception:
                pass

        open_positions = {}
        try:
            positions = await broker_service.get_positions()
            open_positions = {p["symbol"]: p for p in positions}
        except Exception:
            pass

        scored_signals = await signal_aggregator.aggregate(
            self.session_id, all_signals, regime, open_positions,
        )

        # ─── Step 3: Execute top signals that pass the threshold ───
        executed = 0
        for scored in scored_signals:
            if executed >= AutoConfig.MAX_SIGNALS_PER_SCAN:
                break

            if scored.final_score < AutoConfig.MIN_CONFIDENCE_TO_TRADE:
                # Record skip for anti-bias tracking
                await signal_aggregator.record_skip(
                    self.session_id, scored.signal,
                    f"Below threshold: {scored.final_score:.0%} < {AutoConfig.MIN_CONFIDENCE_TO_TRADE:.0%}"
                )
                continue

            # Execute
            trade = await self._auto_execute(
                symbol=scored.signal.symbol,
                side=scored.signal.direction,
                quantity=await self._calculate_position_size(scored.signal),
                stop_loss=scored.signal.suggested_stop,
                take_profit=scored.signal.suggested_target,
                signal=scored.signal.to_dict(),
            )

            if trade:
                executed += 1
                scored.decision = "taken"
                logger.info(
                    "Signal taken: [%s] %s %s (score=%.0f%%, %s)",
                    scored.signal.strategy_name, scored.signal.direction,
                    scored.signal.symbol, scored.final_score * 100,
                    scored.reasoning,
                )

        await emit_activity(self.session_id, "scanning_markets", False)
        logger.info(
            "Scan cycle: %d signals found, %d scored above threshold, %d executed (regime=%s)",
            len(all_signals), sum(1 for s in scored_signals if s.final_score >= AutoConfig.MIN_CONFIDENCE_TO_TRADE),
            executed, regime,
        )

    async def _get_bars(self, symbol: str, timeframe: str) -> List[Dict]:
        """Fetch recent bars for strategy scanning."""
        try:
            data = await market_data_service.get_historical(symbol, "5d")
            return data[-50:] if data else []  # Last 50 bars
        except Exception:
            return []

    async def _get_prev_close(self, symbol: str) -> Optional[float]:
        """Get previous day's closing price."""
        try:
            data = await market_data_service.get_historical(symbol, "5d")
            if data and len(data) >= 2:
                return data[-2]["close"]
        except Exception:
            pass
        return None

    async def _analyze_and_decide(self, symbol: str, timeframe: str) -> Optional[Dict]:
        """
        Analyze a single symbol and decide whether to trade.
        Returns the signal dict if a trade was executed, None otherwise.
        """
        # 1. Get market data with indicators
        data = await market_data_service.get_quote(symbol, timeframe, include_indicators=True)
        if not data:
            return None

        # 2. Detect market regime
        regime = await rl_engine.detect_regime(symbol, data)

        # 3. Get RL-adjusted weights for this regime
        weights = await rl_engine.get_current_weights(self.session_id, regime)

        # 4. Run signal analyzer with learned weights
        # Temporarily override the analyzer's weights
        original_weights = signal_analyzer.indicator_weights
        signal_analyzer.indicator_weights = weights
        signal = await signal_analyzer.analyze(symbol, data, timeframe)
        signal_analyzer.indicator_weights = original_weights

        # 5. Skip neutral signals
        if signal["strength"] == "NEUTRAL":
            return None

        # 6. Ask RL engine: should we trade this?
        should_trade, reason = await rl_engine.should_trade(self.session_id, signal)

        if not should_trade:
            logger.debug("Signal rejected for %s: %s", symbol, reason)
            return None

        # 7. Signal is approved — emit and execute
        signal["regime"] = regime
        signal["rl_reason"] = reason
        await emit_signal(self.session_id, signal)
        await push_signal(self.session_id, signal)

        logger.info("Signal approved: %s %s (confidence=%.2f, reason=%s)",
                     symbol, signal["strength"], signal["confidence"], reason)

        # 8. Calculate position size (Kelly-inspired)
        position_size = await self._calculate_position_size(signal)

        # 9. Auto-execute
        side = "BUY" if "BUY" in signal["strength"] else "SELL"
        trade_result = await self._auto_execute(
            symbol=symbol,
            side=side,
            quantity=position_size,
            stop_loss=signal.get("sl_suggested"),
            take_profit=signal.get("tp_suggested"),
            signal=signal,
        )

        if trade_result:
            self._trades_today += 1
            return signal

        return None

    async def _calculate_position_size(self, signal: Dict) -> float:
        """
        Position sizing based on confidence and Kelly criterion.
        Higher confidence → larger position (within limits).
        """
        balance = await broker_service.get_balance()
        portfolio_value = balance.get("balance", balance.get("cash", 1_000_000))

        confidence = signal.get("confidence", 0.5)
        entry_price = signal.get("entry_suggested", 100)

        if entry_price <= 0:
            return 0

        # Kelly-inspired: position_pct = confidence * base_pct
        # Capped between MIN and MAX
        position_pct = confidence * AutoConfig.BASE_POSITION_PCT
        position_pct = max(AutoConfig.MIN_POSITION_PCT, min(AutoConfig.MAX_POSITION_PCT, position_pct))

        position_value = portfolio_value * position_pct
        quantity = int(position_value / entry_price)

        return max(1, quantity)  # At least 1 unit

    async def _auto_execute(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_loss: Optional[float],
        take_profit: Optional[float],
        signal: Dict,
    ) -> Optional[Dict]:
        """
        Execute a trade automatically with full risk checks.
        Returns trade result or None if blocked.
        """
        # Pre-trade risk gate (non-negotiable, even in auto mode)
        risk_check = await risk_service.check_before_trade(
            session_id=self.session_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
        )

        if not risk_check["approved"]:
            logger.warning("Auto-trade blocked by risk: %s — %s", symbol, risk_check["reason"])
            await emit_risk_alert(self.session_id, {
                "event_type": risk_check.get("violation", "RISK_CHECK"),
                "symbol": symbol,
                "details": f"Auto-trade blocked: {risk_check['reason']}",
                "severity": "warning",
            })
            return None

        # Execute
        try:
            result = await broker_service.place_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

            if result.get("status") in ("FILLED", "PLACED"):
                trade_data = {
                    "id": result.get("order_id", str(uuid.uuid4())[:12]),
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "entry_price": result.get("fill_price", 0),
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "status": result["status"],
                    "signal_id": signal.get("id"),
                    "signal_indicators": signal.get("indicators", {}),
                    "regime": signal.get("regime", "unknown"),
                    "timeframe": signal.get("timeframe", "1h"),
                }

                await emit_trade_executed(self.session_id, trade_data)
                await risk_service.record_trade(self.session_id, trade_data)

                # Store trade metadata for RL feedback
                from app.core.redis import redis_set
                await redis_set(
                    f"trade_meta:{trade_data['id']}",
                    __import__("json").dumps(trade_data),
                    ttl=86400 * 30,
                )

                await notify_all(
                    f"{'📈' if side == 'BUY' else '📉'} AUTO-TRADE: {side} {quantity} {symbol}\n"
                    f"Price: ₹{result.get('fill_price', 0):.2f}\n"
                    f"SL: ₹{stop_loss:.2f} | TP: ₹{take_profit:.2f}\n"
                    f"Confidence: {signal.get('confidence', 0):.0%} | Regime: {signal.get('regime', '?')}",
                    level="info",
                )

                logger.info(
                    "AUTO-TRADE executed: %s %s %s @ %.2f (confidence=%.2f)",
                    side, quantity, symbol, result.get("fill_price", 0), signal.get("confidence", 0),
                )
                return trade_data
            else:
                logger.warning("Auto-trade rejected by broker: %s", result)
                return None

        except Exception as e:
            logger.error("Auto-trade execution failed: %s", e)
            return None

    # ─── Loop 2: MONITOR → CLOSE → LEARN ───

    async def _monitor_loop(self):
        """
        Position monitoring loop. Every 30 seconds:
        1. Check all open positions
        2. Auto-close on SL/TP hit
        3. Check for signal reversal (indicator-based exit)
        4. Feed closed trades to RL engine
        """
        while self.running:
            try:
                await self._run_monitor_cycle()
            except Exception as e:
                logger.error("Monitor cycle failed: %s", e, exc_info=True)

            await asyncio.sleep(AutoConfig.MONITOR_INTERVAL_SECONDS)

    async def _run_monitor_cycle(self):
        """Single monitor cycle."""
        positions = await broker_service.get_positions()
        if not positions:
            return

        for pos in positions:
            symbol = pos.get("symbol")
            sl = pos.get("stop_loss")
            tp = pos.get("take_profit")

            # Get live price
            live = await market_data_service.get_live_price(symbol)
            price = live.get("price", 0)
            if not price:
                continue

            # Update position cache with live price
            pos["current_price"] = price
            if pos.get("side") == "BUY":
                pos["unrealized_pnl"] = (price - pos.get("avg_entry_price", price)) * pos.get("quantity", 0)
            else:
                pos["unrealized_pnl"] = (pos.get("avg_entry_price", price) - price) * pos.get("quantity", 0)

            # ─── Check SL/TP ───
            close_reason = None
            if pos.get("side") == "BUY":
                if sl and price <= sl:
                    close_reason = "stop_loss_hit"
                elif tp and price >= tp:
                    close_reason = "take_profit_hit"
            elif pos.get("side") == "SELL":
                if sl and price >= sl:
                    close_reason = "stop_loss_hit"
                elif tp and price <= tp:
                    close_reason = "take_profit_hit"

            # ─── Check signal reversal ───
            if not close_reason:
                close_reason = await self._check_signal_reversal(symbol, pos)

            # ─── Auto-close ───
            if close_reason:
                await self._auto_close(symbol, pos, close_reason, price)

    async def _check_signal_reversal(self, symbol: str, position: Dict) -> Optional[str]:
        """
        Check if the original signal has reversed.
        If we entered on a BUY signal and indicators now say SELL → exit.
        """
        try:
            data = await market_data_service.get_quote(symbol, "1h", include_indicators=True)
            if not data:
                return None

            regime = await rl_engine.detect_regime(symbol, data)
            weights = await rl_engine.get_current_weights(self.session_id, regime)

            original_weights = signal_analyzer.indicator_weights
            signal_analyzer.indicator_weights = weights
            signal = await signal_analyzer.analyze(symbol, data, "1h")
            signal_analyzer.indicator_weights = original_weights

            # If we're long and signal says SELL/STRONG_SELL → reversal
            if position.get("side") == "BUY" and signal["strength"] in ("SELL", "STRONG_SELL"):
                if signal["confidence"] > 0.6:
                    return "signal_reversal"

            # If we're short and signal says BUY/STRONG_BUY → reversal
            if position.get("side") == "SELL" and signal["strength"] in ("BUY", "STRONG_BUY"):
                if signal["confidence"] > 0.6:
                    return "signal_reversal"

        except Exception as e:
            logger.debug("Signal reversal check failed for %s: %s", symbol, e)

        return None

    async def _auto_close(self, symbol: str, position: Dict, reason: str, current_price: float):
        """Close a position and feed the outcome to the RL engine."""
        try:
            result = await broker_service.close_position(symbol)
            pnl = result.get("pnl", 0)
            pnl_pct = result.get("pnl_pct", 0)

            self._pnl_today += pnl

            # Set cooldown on loss
            if pnl < 0:
                self._last_loss_at = datetime.now(timezone.utc)

            # Notify
            emoji = "✅" if pnl >= 0 else "❌"
            await notify_all(
                f"{emoji} AUTO-CLOSE: {symbol} ({reason.replace('_', ' ')})\n"
                f"P&L: ₹{pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
                f"Exit: ₹{current_price:.2f}\n"
                f"Today: ₹{self._pnl_today:+,.2f} | Trades: {self._trades_today}",
                level="info" if pnl >= 0 else "warning",
            )

            await emit_risk_alert(self.session_id, {
                "event_type": reason.upper(),
                "symbol": symbol,
                "details": f"Auto-closed: P&L ₹{pnl:+,.2f} ({pnl_pct:+.2f}%)",
                "severity": "info" if pnl >= 0 else "warning",
            })

            # ─── FEED TO RL ENGINE (the learning step) ───
            await self._record_for_learning(symbol, position, result, reason)

            # Update risk state
            await risk_service.record_close(self.session_id, symbol, pnl)

            logger.info(
                "AUTO-CLOSE: %s reason=%s pnl=%.2f (%.2f%%)",
                symbol, reason, pnl, pnl_pct,
            )

        except Exception as e:
            logger.error("Auto-close failed for %s: %s", symbol, e)

    async def _record_for_learning(self, symbol: str, position: Dict, close_result: Dict, reason: str):
        """
        Convert a closed trade into a TradeOutcome and feed it to the RL engine.
        This is where the loop closes: execution → outcome → learning → better execution.
        """
        import json as json_mod
        from app.core.redis import redis_get

        # Try to load the original trade metadata (signal indicators, regime, etc.)
        trade_id = close_result.get("trade_id", "")
        raw_meta = await redis_get(f"trade_meta:{trade_id}")
        meta = json_mod.loads(raw_meta) if raw_meta else {}

        entry_price = position.get("avg_entry_price", 0)
        exit_price = close_result.get("exit_price", position.get("current_price", 0))
        pnl = close_result.get("pnl", 0)
        pnl_pct = close_result.get("pnl_pct", 0)

        # Build TradeOutcome
        outcome = TradeOutcome(
            trade_id=trade_id,
            symbol=symbol,
            side=position.get("side", "BUY"),
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            duration_minutes=0,  # TODO: calculate from timestamps
            timeframe=meta.get("timeframe", "1h"),
            signal_indicators=meta.get("signal_indicators", {}),
            signal_strength=meta.get("strength", "NEUTRAL"),
            signal_confidence=meta.get("confidence", 0.5),
            market_regime=meta.get("regime", "unknown"),
            volume_regime="normal",
        )

        # Feed to RL engine — this adjusts weights
        await rl_engine.record_outcome(self.session_id, outcome)

    # ─── Loop 3: PERIODIC REVIEW ───

    async def _review_loop(self):
        """
        Periodic review loop:
        - Every hour: check overall performance, adjust risk limits dynamically
        - Every day: run full meta-review across all trade history
        """
        cycles = 0
        while self.running:
            try:
                cycles += 1

                # Hourly review
                await self._hourly_review()

                # Daily meta-review (every ~24 cycles at 1h interval)
                if cycles % 24 == 0:
                    review = await rl_engine.run_meta_review(self.session_id)
                    if review.get("adjustments"):
                        adj_summary = ", ".join(
                            f"{a['indicator']}: {a['direction']} ({a['drift']:+.1%})"
                            for a in review["adjustments"]
                        )
                        await notify_all(
                            f"🧠 META-REVIEW complete ({review['total_trades']} trades analyzed)\n"
                            f"Adjustments: {adj_summary}",
                            level="info",
                        )
                    if review.get("warning"):
                        await notify_all(f"⚠️ {review['warning']}", level="warning")

            except Exception as e:
                logger.error("Review cycle failed: %s", e, exc_info=True)

            await asyncio.sleep(AutoConfig.REVIEW_INTERVAL_SECONDS)

    async def _hourly_review(self):
        """Hourly performance check and dynamic risk adjustment."""
        risk_state = await risk_service.get_risk_summary(self.session_id)
        daily_pnl = risk_state.get("daily_pnl", 0)

        # Dynamic risk adjustment: if losing badly, reduce position sizes
        if daily_pnl < -settings.MAX_DAILY_LOSS * 0.5:
            AutoConfig.BASE_POSITION_PCT = max(0.02, AutoConfig.BASE_POSITION_PCT * 0.8)
            await notify_all(
                f"⚠️ Position sizes reduced to {AutoConfig.BASE_POSITION_PCT:.0%} "
                f"(daily P&L: ₹{daily_pnl:+,.2f})",
                level="warning",
            )
        elif daily_pnl > 0 and AutoConfig.BASE_POSITION_PCT < 0.05:
            # Recovering — gradually increase back
            AutoConfig.BASE_POSITION_PCT = min(0.05, AutoConfig.BASE_POSITION_PCT * 1.1)

        # Learning status
        status = await rl_engine.get_learning_status(self.session_id)
        logger.info(
            "Hourly review: daily_pnl=%.2f trades=%d position_pct=%.1f%% rl_outcomes=%d",
            daily_pnl, self._trades_today, AutoConfig.BASE_POSITION_PCT * 100,
            status.get("total_outcomes", 0),
        )


# ─── Factory ───

_pipeline: Optional[AutonomousPipeline] = None


async def start_autonomous_pipeline(session_id: str = "auto"):
    """Start the autonomous pipeline as a background task."""
    global _pipeline
    if _pipeline and _pipeline.running:
        logger.warning("Pipeline already running")
        return

    _pipeline = AutonomousPipeline(session_id)
    asyncio.create_task(_pipeline.start())
    return _pipeline


async def stop_autonomous_pipeline():
    """Stop the pipeline."""
    global _pipeline
    if _pipeline:
        await _pipeline.stop()
        _pipeline = None


def get_pipeline() -> Optional[AutonomousPipeline]:
    return _pipeline
