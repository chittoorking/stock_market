"""
Autonomous pipeline control endpoints.
Start/stop the bot, view learning status, adjust strategies.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.autonomous_pipeline import (
    start_autonomous_pipeline, stop_autonomous_pipeline, get_pipeline, AutoConfig,
)
from app.ai_services.reinforcement_engine import rl_engine
from app.ai_services.strategy_engine import strategy_engine

logger = logging.getLogger(__name__)
router = APIRouter()


class StartRequest(BaseModel):
    session_id: str = "auto"
    markets: Optional[list] = None
    timeframes: Optional[list] = None
    min_confidence: Optional[float] = None


@router.post("/start")
async def start_pipeline(req: StartRequest):
    """Start the autonomous trading pipeline."""
    if req.markets:
        AutoConfig.ACTIVE_MARKETS = req.markets
    if req.timeframes:
        AutoConfig.ACTIVE_TIMEFRAMES = req.timeframes
    if req.min_confidence:
        AutoConfig.MIN_CONFIDENCE_TO_TRADE = req.min_confidence

    await start_autonomous_pipeline(req.session_id)

    return {
        "status": "started",
        "session_id": req.session_id,
        "markets": AutoConfig.ACTIVE_MARKETS,
        "timeframes": AutoConfig.ACTIVE_TIMEFRAMES,
        "min_confidence": AutoConfig.MIN_CONFIDENCE_TO_TRADE,
    }


@router.post("/stop")
async def stop_pipeline():
    """Stop the autonomous trading pipeline."""
    await stop_autonomous_pipeline()
    return {"status": "stopped"}


@router.get("/status")
async def pipeline_status():
    """Get pipeline status."""
    pipeline = get_pipeline()
    if not pipeline or not pipeline.running:
        return {"status": "stopped"}

    return {
        "status": "running",
        "session_id": pipeline.session_id,
        "trades_today": pipeline._trades_today,
        "pnl_today": pipeline._pnl_today,
        "markets": AutoConfig.ACTIVE_MARKETS,
        "timeframes": AutoConfig.ACTIVE_TIMEFRAMES,
        "position_size_pct": AutoConfig.BASE_POSITION_PCT,
        "min_confidence": AutoConfig.MIN_CONFIDENCE_TO_TRADE,
    }


# ─── RL Engine endpoints ───

@router.get("/learning/{session_id}")
async def learning_status(session_id: str):
    """Get the RL engine's current learning status."""
    return await rl_engine.get_learning_status(session_id)


@router.post("/learning/{session_id}/meta-review")
async def trigger_meta_review(session_id: str):
    """Manually trigger a meta-review."""
    result = await rl_engine.run_meta_review(session_id)
    return result


@router.get("/learning/{session_id}/weights")
async def get_weights(session_id: str, regime: str = "normal"):
    """Get current RL-adjusted indicator weights."""
    weights = await rl_engine.get_current_weights(session_id, regime)
    return {"weights": weights, "regime": regime}


# ─── Strategy endpoints ───

@router.get("/strategies")
async def list_strategies():
    """Get all available strategies."""
    return {"strategies": strategy_engine.get_all_strategies()}


@router.post("/strategies/{name}/activate")
async def activate_strategy(name: str):
    """Activate a strategy."""
    strategy_engine.activate_strategy(name)
    return {"status": "activated", "active": strategy_engine.active_strategies}


@router.post("/strategies/{name}/deactivate")
async def deactivate_strategy(name: str):
    """Deactivate a strategy."""
    strategy_engine.deactivate_strategy(name)
    return {"status": "deactivated", "active": strategy_engine.active_strategies}


# ─── Backtest endpoint ───

class BacktestRequest(BaseModel):
    session_id: str = "backtest"
    data_dir: str = ""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initial_capital: float = 1_000_000
    min_confidence: float = 0.55
    enable_rl: bool = True


@router.post("/backtest")
async def run_backtest(req: BacktestRequest):
    """Run a full pipeline backtest on historical data."""
    from app.services.backtester import Backtester, BacktestConfig
    from dataclasses import asdict

    data_dir = req.data_dir
    if not data_dir:
        # Default: look for data in the stock research folder
        import os
        candidates = [
            "C:/Users/HP/Downloads/Temp/Food Ordering All/stock/Stock Market Trading/data/5min",
            "C:/Users/HP/Downloads/Temp/Food Ordering All/stock/Stock Market Trading/data/1min",
            "./data/5min",
        ]
        for c in candidates:
            if os.path.isdir(c):
                data_dir = c
                break

    if not data_dir:
        return {"error": "No data directory found. Set data_dir in request."}

    config = BacktestConfig(
        initial_capital=req.initial_capital,
        min_confidence=req.min_confidence,
        enable_rl_learning=req.enable_rl,
    )

    backtester = Backtester(session_id=req.session_id, config=config)
    result = await backtester.run(
        data_dir=data_dir,
        start_date=req.start_date,
        end_date=req.end_date,
    )

    # Return summary (full daily_results can be huge)
    return {
        "summary": {
            "total_trades": result.total_trades,
            "wins": result.wins,
            "losses": result.losses,
            "win_rate": f"{result.win_rate:.1%}",
            "total_pnl": result.total_pnl,
            "total_pnl_pct": f"{result.total_pnl_pct:.2f}%",
            "max_drawdown": f"{result.max_drawdown_pct:.2f}%",
            "sharpe_ratio": result.sharpe_ratio,
            "profit_factor": result.profit_factor,
            "total_signals": result.total_signals,
            "total_commission": result.total_commission,
            "trading_days": result.trading_days,
        },
        "strategy_breakdown": result.strategy_breakdown,
        "weight_evolution": result.weight_evolution,
        "initial_weights": result.initial_weights,
        "final_weights": result.final_weights,
        "equity_curve": result.equity_curve[-50:],  # Last 50 points
    }
