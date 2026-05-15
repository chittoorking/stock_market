"""
Backtesting Harness — replay historical data through the FULL pipeline.

This is NOT a simple "if RSI < 30 buy" backtester. It runs the ENTIRE system:

    Historical bars → Proven strategy scanners → Signal aggregator (with anti-bias)
    → RL engine scoring → Risk gates → Paper broker execution
    → Position monitoring → SL/TP/reversal exits → RL learns from outcomes
    → Context graph tracks everything → Weights evolve over time

The backtester simulates TIME. It feeds bars one at a time, as if the bot
were running live. The RL engine learns during the backtest, so by the end
you see how the bot would have EVOLVED its strategy.

Usage:
    backtester = Backtester(session_id="backtest-001")
    results = await backtester.run(
        data_dir="C:/Users/HP/Downloads/Temp/Food Ordering All/stock/Stock Market Trading/data/5min",
        start_date="2026-01-01",
        end_date="2026-03-31",
    )

Data format: CSV files per symbol with columns: timestamp, open, high, low, close, volume, oi
"""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from pathlib import Path

from app.signals.proven_strategies import (
    GapAndGoSignal, ORBSignal, CascadeSignal,
    LenzSignal, AftershockSignal, GapDecaySignal, SecondWaveSignal,
)
from app.signals.aggregator import signal_aggregator, SignalAggregator
from app.signals.base import StrategySignal, SECTOR_MAP, get_sector
from app.ai_services.reinforcement_engine import rl_engine, TradeOutcome
from app.core.context_graph import ContextGraph

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Backtester configuration."""
    initial_capital: float = 1_000_000.0
    commission_pct: float = 0.03        # 0.03% per side (Upstox)
    max_positions: int = 5
    max_trades_per_day: int = 3
    max_losses_per_day: int = 1
    position_size_pct: float = 0.05     # 5% per trade
    min_confidence: float = 0.55        # Aggregator score threshold
    morning_only: bool = True           # Only trade 9:30-11:00 IST
    stop_pct: float = 0.30
    target_rr: float = 2.5
    max_bars_hold: int = 36
    bail_bars: int = 12
    bail_min_profit_pct: float = 0.10
    enable_rl_learning: bool = True     # Let RL adjust weights during backtest
    enable_context_graph: bool = True


@dataclass
class BacktestTrade:
    """Record of a single backtest trade."""
    trade_id: str = ""
    symbol: str = ""
    strategy: str = ""
    direction: str = ""
    entry_price: float = 0.0
    entry_bar: int = 0
    entry_time: str = ""
    exit_price: float = 0.0
    exit_bar: int = 0
    exit_time: str = ""
    exit_reason: str = ""  # "target", "stop", "bail", "time", "eod", "reversal"
    quantity: int = 0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    commission: float = 0.0
    signal_confidence: float = 0.0
    aggregator_score: float = 0.0
    regime: str = ""
    confluence_score: float = 0.0


@dataclass
class BacktestDay:
    """Results for a single trading day."""
    date: str = ""
    trades: List[BacktestTrade] = field(default_factory=list)
    signals_generated: int = 0
    signals_taken: int = 0
    signals_skipped: int = 0
    pnl: float = 0.0
    wins: int = 0
    losses: int = 0


@dataclass
class BacktestResult:
    """Full backtest results."""
    config: Dict = field(default_factory=dict)
    start_date: str = ""
    end_date: str = ""
    trading_days: int = 0
    total_trades: int = 0
    total_signals: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    total_commission: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    # Per-strategy breakdown
    strategy_breakdown: Dict[str, Dict] = field(default_factory=dict)
    # Per-day breakdown
    daily_results: List[BacktestDay] = field(default_factory=list)
    # RL evolution
    initial_weights: Dict = field(default_factory=dict)
    final_weights: Dict = field(default_factory=dict)
    weight_evolution: List[Dict] = field(default_factory=list)
    # Equity curve
    equity_curve: List[Dict] = field(default_factory=list)


class Backtester:
    """
    Full pipeline backtester. Replays historical data bar-by-bar
    through the entire system.
    """

    def __init__(self, session_id: str = "backtest", config: Optional[BacktestConfig] = None):
        self.session_id = session_id
        self.config = config or BacktestConfig()
        self._capital = self.config.initial_capital
        self._equity = self.config.initial_capital
        self._peak_equity = self.config.initial_capital
        self._positions: Dict[str, Dict] = {}
        self._all_trades: List[BacktestTrade] = []
        self._daily_results: List[BacktestDay] = []
        self._equity_curve: List[Dict] = []
        self._weight_snapshots: List[Dict] = []
        self._cw4 = SecondWaveSignal()  # Stateful, needs instance

    async def run(
        self,
        data_dir: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> BacktestResult:
        """
        Run the full backtest.

        data_dir: path to CSV files (e.g., data/5min/)
        start_date/end_date: filter dates (YYYY-MM-DD)
        """
        logger.info("Backtest starting: capital=%.0f, dir=%s", self._capital, data_dir)

        # Record initial RL weights
        initial_weights = await rl_engine.get_current_weights(self.session_id)

        # Load all data
        all_data = self._load_data(data_dir, start_date, end_date)
        if not all_data:
            logger.error("No data loaded from %s", data_dir)
            return BacktestResult()

        # Get unique trading days
        trading_days = sorted(set(
            bar["timestamp"][:10] for bars in all_data.values() for bar in bars
        ))

        logger.info("Loaded %d symbols, %d trading days", len(all_data), len(trading_days))

        # Context graph for this backtest
        if self.config.enable_context_graph:
            graph = ContextGraph(self.session_id)

        # Aggregator with fresh trust scores
        aggregator = SignalAggregator()

        # ─── Replay day by day ───
        for day_idx, date_str in enumerate(trading_days):
            day_result = BacktestDay(date=date_str)
            daily_pnl = 0.0
            daily_trades = 0
            daily_losses = 0

            self._cw4.reset_daily()

            # Get bars for this day, per symbol
            day_data: Dict[str, List[Dict]] = {}
            prev_close: Dict[str, float] = {}

            for symbol, bars in all_data.items():
                day_bars = [b for b in bars if b["timestamp"][:10] == date_str]
                if day_bars:
                    day_data[symbol] = day_bars

                # Previous day close
                prev_bars = [b for b in bars if b["timestamp"][:10] < date_str]
                if prev_bars:
                    prev_close[symbol] = prev_bars[-1]["close"]

            if not day_data:
                continue

            # ─── Bar-by-bar replay ───
            max_bars = max(len(bars) for bars in day_data.values())

            for bar_idx in range(max_bars):
                # 1. Check existing positions for exits
                await self._check_exits(day_data, bar_idx, day_result)

                # 2. Time filter (morning only)
                if self.config.morning_only and bar_idx >= 18:  # 18 bars × 5min = 90min = 11:00
                    continue

                # 3. Check daily limits
                if daily_trades >= self.config.max_trades_per_day:
                    continue
                if daily_losses >= self.config.max_losses_per_day:
                    continue
                if len(self._positions) >= self.config.max_positions:
                    continue

                # 4. Run strategy scanners on each symbol
                signals = []
                for symbol, bars in day_data.items():
                    if bar_idx >= len(bars):
                        continue
                    bars_so_far = bars[:bar_idx + 1]
                    pc = prev_close.get(symbol)

                    # Skip if already have position in this symbol
                    if symbol in self._positions:
                        continue

                    sigs = self._scan_strategies(symbol, bars_so_far, pc, bar_idx)
                    signals.extend(sigs)

                day_result.signals_generated += len(signals)

                if not signals:
                    continue

                # 5. Aggregate and score signals
                regime = self._detect_simple_regime(day_data, bar_idx)
                scored = await aggregator.aggregate(
                    self.session_id, signals, regime, self._positions,
                )

                # 6. Execute top signals above threshold
                for scored_signal in scored:
                    if daily_trades >= self.config.max_trades_per_day:
                        break
                    if len(self._positions) >= self.config.max_positions:
                        break

                    if scored_signal.final_score < self.config.min_confidence:
                        day_result.signals_skipped += 1
                        if self.config.enable_rl_learning:
                            await aggregator.record_skip(
                                self.session_id, scored_signal.signal,
                                f"Below threshold: {scored_signal.final_score:.0%}",
                            )
                        continue

                    # Execute trade
                    sig = scored_signal.signal
                    trade = self._open_position(
                        sig, bar_idx, scored_signal.final_score,
                        scored_signal.confluence_score, regime,
                    )

                    if trade:
                        daily_trades += 1
                        day_result.signals_taken += 1

            # ─── End of day: force close all positions ───
            for symbol in list(self._positions.keys()):
                last_bar = day_data.get(symbol, [{}])[-1]
                if last_bar:
                    trade = self._close_position(
                        symbol, last_bar.get("close", 0),
                        len(day_data.get(symbol, [])) - 1,
                        last_bar.get("timestamp", ""), "eod",
                    )
                    if trade:
                        day_result.trades.append(trade)
                        if trade.pnl > 0:
                            day_result.wins += 1
                        else:
                            day_result.losses += 1

            # Day summary
            day_result.pnl = sum(t.pnl for t in day_result.trades)
            self._daily_results.append(day_result)

            # Equity curve
            self._equity_curve.append({
                "date": date_str,
                "equity": self._equity,
                "pnl": day_result.pnl,
                "trades": len(day_result.trades),
                "drawdown": ((self._peak_equity - self._equity) / self._peak_equity * 100) if self._peak_equity > 0 else 0,
            })

            # RL weight snapshot (weekly)
            if self.config.enable_rl_learning and day_idx % 5 == 0:
                weights = await rl_engine.get_current_weights(self.session_id)
                self._weight_snapshots.append({"day": day_idx, "date": date_str, "weights": weights})

            if day_idx % 10 == 0:
                logger.info(
                    "Backtest day %d/%d (%s): equity=%.0f pnl=%.0f trades=%d",
                    day_idx + 1, len(trading_days), date_str,
                    self._equity, day_result.pnl, len(day_result.trades),
                )

        # ─── Build results ───
        final_weights = await rl_engine.get_current_weights(self.session_id)
        return self._build_results(initial_weights, final_weights)

    def _scan_strategies(
        self, symbol: str, bars: List[Dict], prev_close: Optional[float], bar_idx: int,
    ) -> List[StrategySignal]:
        """Run all proven strategy scanners on a symbol."""
        signals = []

        try:
            # GG8
            if prev_close and len(bars) >= 2:
                sig = GapAndGoSignal.scan(symbol, bars, prev_close)
                if sig:
                    signals.append(sig)

            # ORB
            if len(bars) >= 4:
                sig = ORBSignal.scan(symbol, bars)
                if sig:
                    signals.append(sig)

            # LNZ3
            if len(bars) >= 2:
                sig = LenzSignal.scan(symbol, bars)
                if sig:
                    signals.append(sig)

            # AFT7
            if len(bars) >= 2:
                sig = AftershockSignal.scan(symbol, bars)
                if sig:
                    signals.append(sig)

            # GDR4
            if prev_close and len(bars) >= 7:
                sig = GapDecaySignal.scan(symbol, bars, prev_close)
                if sig:
                    signals.append(sig)

        except Exception as e:
            logger.debug("Strategy scan error for %s: %s", symbol, e)

        return signals

    def _open_position(
        self, signal: StrategySignal, bar_idx: int,
        aggregator_score: float, confluence: float, regime: str,
    ) -> Optional[BacktestTrade]:
        """Open a backtest position."""
        entry = signal.suggested_entry
        if entry <= 0:
            return None

        # Position sizing
        position_value = self._equity * self.config.position_size_pct
        quantity = int(position_value / entry)
        if quantity <= 0:
            return None

        commission = entry * quantity * self.config.commission_pct / 100

        self._positions[signal.symbol] = {
            "signal": signal,
            "entry_price": entry,
            "stop": signal.suggested_stop,
            "target": signal.suggested_target,
            "quantity": quantity,
            "direction": signal.direction,
            "entry_bar": bar_idx,
            "entry_time": signal.triggered_at,
            "strategy": signal.strategy_name,
            "aggregator_score": aggregator_score,
            "confluence": confluence,
            "regime": regime,
            "max_favorable": 0.0,
            "trail_state": 0,
            "commission": commission,
        }

        self._capital -= commission
        return None  # Trade not complete until closed

    def _close_position(
        self, symbol: str, exit_price: float, bar_idx: int, exit_time: str, reason: str,
    ) -> Optional[BacktestTrade]:
        """Close a backtest position and record the trade."""
        pos = self._positions.pop(symbol, None)
        if not pos:
            return None

        entry = pos["entry_price"]
        qty = pos["quantity"]
        direction = pos["direction"]
        commission = exit_price * qty * self.config.commission_pct / 100

        if direction == "LONG":
            pnl = (exit_price - entry) * qty - pos["commission"] - commission
        else:
            pnl = (entry - exit_price) * qty - pos["commission"] - commission

        pnl_pct = (pnl / (entry * qty)) * 100

        self._capital -= commission
        self._equity += pnl
        self._peak_equity = max(self._peak_equity, self._equity)

        trade = BacktestTrade(
            trade_id=f"BT-{len(self._all_trades) + 1:04d}",
            symbol=symbol,
            strategy=pos["strategy"],
            direction=direction,
            entry_price=entry,
            entry_bar=pos["entry_bar"],
            entry_time=pos["entry_time"],
            exit_price=exit_price,
            exit_bar=bar_idx,
            exit_time=exit_time,
            exit_reason=reason,
            quantity=qty,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 4),
            commission=round(pos["commission"] + commission, 2),
            signal_confidence=pos["signal"].raw_confidence if hasattr(pos.get("signal", ""), "raw_confidence") else 0,
            aggregator_score=pos["aggregator_score"],
            regime=pos["regime"],
            confluence_score=pos["confluence"],
        )

        self._all_trades.append(trade)

        # Feed RL engine if learning enabled
        if self.config.enable_rl_learning:
            asyncio.get_event_loop().create_task(
                self._feed_rl(trade, pos)
            )

        return trade

    async def _check_exits(self, day_data: Dict[str, List[Dict]], bar_idx: int, day_result: BacktestDay):
        """Check all open positions for exit conditions."""
        for symbol in list(self._positions.keys()):
            pos = self._positions[symbol]
            bars = day_data.get(symbol, [])

            if bar_idx >= len(bars):
                continue

            bar = bars[bar_idx]
            entry = pos["entry_price"]
            stop = pos["stop"]
            target = pos["target"]
            direction = pos["direction"]
            bars_held = bar_idx - pos["entry_bar"]

            # Check exit conditions FIRST using the stop from the PREVIOUS bar
            # (prevents same-bar whipsaw: high triggers trail, low triggers new stop)
            exit_reason = None
            exit_price = bar["close"]

            if direction == "LONG":
                if bar["low"] <= stop:
                    exit_reason = "stop"
                    exit_price = stop
                elif bar["high"] >= target:
                    exit_reason = "target"
                    exit_price = target
            else:
                if bar["high"] >= stop:
                    exit_reason = "stop"
                    exit_price = stop
                elif bar["low"] <= target:
                    exit_reason = "target"
                    exit_price = target

            # Bail check
            if not exit_reason and bars_held >= self.config.bail_bars:
                current_pnl_pct = ((bar["close"] - entry) / entry * 100) if direction == "LONG" else ((entry - bar["close"]) / entry * 100)
                if current_pnl_pct < self.config.bail_min_profit_pct:
                    exit_reason = "bail"
                    exit_price = bar["close"]

            # Max bars
            if not exit_reason and bars_held >= self.config.max_bars_hold:
                exit_reason = "time"
                exit_price = bar["close"]

            if exit_reason:
                trade = self._close_position(
                    symbol, exit_price, bar_idx,
                    bar.get("timestamp", ""), exit_reason,
                )
                if trade:
                    day_result.trades.append(trade)
                    if trade.pnl > 0:
                        day_result.wins += 1
                    else:
                        day_result.losses += 1
                continue  # Position closed, skip trailing update

            # NOW update trailing stop for NEXT bar (after exit check passed)
            if direction == "LONG":
                favorable = bar["high"] - entry
            else:
                favorable = entry - bar["low"]
            pos["max_favorable"] = max(pos["max_favorable"], favorable)

            initial_risk = abs(entry - stop)
            if initial_risk > 0:
                trail_levels = (0.15, 0.30, 0.50)
                if pos["trail_state"] < 3 and pos["max_favorable"] >= initial_risk * trail_levels[2]:
                    if direction == "LONG":
                        pos["stop"] = entry + initial_risk * trail_levels[1]
                    else:
                        pos["stop"] = entry - initial_risk * trail_levels[1]
                    pos["trail_state"] = 3
                elif pos["trail_state"] < 2 and pos["max_favorable"] >= initial_risk * trail_levels[1]:
                    if direction == "LONG":
                        pos["stop"] = entry + initial_risk * trail_levels[0]
                    else:
                        pos["stop"] = entry - initial_risk * trail_levels[0]
                    pos["trail_state"] = 2
                elif pos["trail_state"] < 1 and pos["max_favorable"] >= initial_risk * trail_levels[0]:
                    pos["stop"] = entry
                    pos["trail_state"] = 1

    async def _feed_rl(self, trade: BacktestTrade, pos: Dict):
        """Feed trade outcome to RL engine."""
        try:
            signal = pos.get("signal")
            indicators = signal.to_dict().get("indicators", {}) if hasattr(signal, "to_dict") else {}

            outcome = TradeOutcome(
                trade_id=trade.trade_id,
                symbol=trade.symbol,
                side=trade.direction,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                pnl=trade.pnl,
                pnl_pct=trade.pnl_pct,
                duration_minutes=(trade.exit_bar - trade.entry_bar) * 5,
                timeframe="5m",
                signal_indicators=indicators,
                signal_strength=trade.direction,
                signal_confidence=trade.signal_confidence,
                market_regime=trade.regime,
                volume_regime="normal",
            )
            await rl_engine.record_outcome(self.session_id, outcome)

            # Update trust
            await signal_aggregator.update_trust(
                self.session_id, trade.strategy, trade.pnl > 0, trade.pnl_pct,
            )
        except Exception as e:
            logger.debug("RL feed error: %s", e)

    def _detect_simple_regime(self, day_data: Dict[str, List[Dict]], bar_idx: int) -> str:
        """Simple regime detection from NIFTY50 bars."""
        nifty_bars = day_data.get("NIFTY50", day_data.get("NIFTY_50", []))
        if not nifty_bars or bar_idx >= len(nifty_bars):
            return "unknown"

        # Use last 10 bars for trend
        start = max(0, bar_idx - 10)
        recent = nifty_bars[start:bar_idx + 1]
        if len(recent) < 3:
            return "unknown"

        first_close = recent[0]["close"]
        last_close = recent[-1]["close"]
        change_pct = ((last_close - first_close) / first_close) * 100

        # Volatility (range of closes)
        closes = [b["close"] for b in recent]
        if closes:
            vol = (max(closes) - min(closes)) / first_close * 100
        else:
            vol = 0

        if vol > 1.5:
            return "volatile"
        elif change_pct > 0.3:
            return "trending_up"
        elif change_pct < -0.3:
            return "trending_down"
        else:
            return "ranging"

    def _load_data(
        self, data_dir: str, start_date: Optional[str], end_date: Optional[str],
    ) -> Dict[str, List[Dict]]:
        """Load CSV data files into memory."""
        all_data: Dict[str, List[Dict]] = {}
        data_path = Path(data_dir)

        if not data_path.exists():
            logger.error("Data directory not found: %s", data_dir)
            return all_data

        for csv_file in sorted(data_path.glob("*.csv")):
            symbol = csv_file.stem.split("_")[0].upper()

            try:
                with open(csv_file, "r") as f:
                    reader = csv.DictReader(f)
                    bars = []
                    for row in reader:
                        ts = row.get("timestamp", "")
                        date = ts[:10]

                        if start_date and date < start_date:
                            continue
                        if end_date and date > end_date:
                            continue

                        try:
                            bars.append({
                                "timestamp": ts,
                                "open": float(row.get("open", 0)),
                                "high": float(row.get("high", 0)),
                                "low": float(row.get("low", 0)),
                                "close": float(row.get("close", 0)),
                                "volume": int(float(row.get("volume", 0))),
                            })
                        except (ValueError, TypeError):
                            continue

                    if bars:
                        all_data[symbol] = bars
                        logger.debug("Loaded %s: %d bars", symbol, len(bars))

            except Exception as e:
                logger.warning("Failed to load %s: %s", csv_file, e)

        return all_data

    def _build_results(self, initial_weights: Dict, final_weights: Dict) -> BacktestResult:
        """Compile all results into BacktestResult."""
        trades = self._all_trades
        total = len(trades)

        if total == 0:
            return BacktestResult(
                config=asdict(self.config),
                total_trades=0,
                equity_curve=self._equity_curve,
            )

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        win_pnls = [t.pnl_pct for t in wins]
        loss_pnls = [t.pnl_pct for t in losses]

        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))

        # Consecutive tracking
        max_consec_wins = 0
        max_consec_losses = 0
        current_streak = 0
        for t in trades:
            if t.pnl > 0:
                current_streak = current_streak + 1 if current_streak > 0 else 1
                max_consec_wins = max(max_consec_wins, current_streak)
            else:
                current_streak = current_streak - 1 if current_streak < 0 else -1
                max_consec_losses = max(max_consec_losses, abs(current_streak))

        # Max drawdown from equity curve
        max_dd = 0
        peak = self.config.initial_capital
        for point in self._equity_curve:
            eq = point.get("equity", peak)
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100
            max_dd = max(max_dd, dd)

        # Sharpe (annualized from daily returns)
        daily_returns = [d.pnl / self.config.initial_capital for d in self._daily_results if d.trades]
        if daily_returns and len(daily_returns) > 1:
            import statistics
            avg_ret = statistics.mean(daily_returns)
            std_ret = statistics.stdev(daily_returns)
            sharpe = (avg_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0
        else:
            sharpe = 0

        # Per-strategy breakdown
        strategy_breakdown = {}
        for t in trades:
            s = t.strategy
            if s not in strategy_breakdown:
                strategy_breakdown[s] = {
                    "trades": 0, "wins": 0, "pnl": 0, "avg_pnl_pct": 0,
                    "best": 0, "worst": 0, "pnl_pcts": [],
                }
            strategy_breakdown[s]["trades"] += 1
            strategy_breakdown[s]["pnl"] += t.pnl
            strategy_breakdown[s]["pnl_pcts"].append(t.pnl_pct)
            if t.pnl > 0:
                strategy_breakdown[s]["wins"] += 1
            strategy_breakdown[s]["best"] = max(strategy_breakdown[s]["best"], t.pnl_pct)
            strategy_breakdown[s]["worst"] = min(strategy_breakdown[s]["worst"], t.pnl_pct)

        for s, data in strategy_breakdown.items():
            data["win_rate"] = data["wins"] / data["trades"] if data["trades"] > 0 else 0
            data["avg_pnl_pct"] = sum(data["pnl_pcts"]) / len(data["pnl_pcts"]) if data["pnl_pcts"] else 0
            del data["pnl_pcts"]  # Don't include raw list

        total_pnl = self._equity - self.config.initial_capital

        return BacktestResult(
            config=asdict(self.config),
            start_date=self._daily_results[0].date if self._daily_results else "",
            end_date=self._daily_results[-1].date if self._daily_results else "",
            trading_days=len(self._daily_results),
            total_trades=total,
            total_signals=sum(d.signals_generated for d in self._daily_results),
            wins=len(wins),
            losses=len(losses),
            win_rate=len(wins) / total,
            total_pnl=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl / self.config.initial_capital * 100, 2),
            total_commission=round(sum(t.commission for t in trades), 2),
            max_drawdown_pct=round(max_dd, 2),
            sharpe_ratio=round(sharpe, 2),
            profit_factor=round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999,
            avg_win_pct=round(sum(win_pnls) / len(win_pnls), 4) if win_pnls else 0,
            avg_loss_pct=round(sum(loss_pnls) / len(loss_pnls), 4) if loss_pnls else 0,
            best_trade=round(max(t.pnl_pct for t in trades), 4),
            worst_trade=round(min(t.pnl_pct for t in trades), 4),
            max_consecutive_wins=max_consec_wins,
            max_consecutive_losses=max_consec_losses,
            strategy_breakdown=strategy_breakdown,
            daily_results=self._daily_results,
            initial_weights=initial_weights,
            final_weights=final_weights,
            weight_evolution=self._weight_snapshots,
            equity_curve=self._equity_curve,
        )
