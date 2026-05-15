"""
FastAPI application — main entry point for the Autonomous Trading Agent.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    # ─── Startup ───
    logger.info("Starting %s...", settings.APP_NAME)

    # Initialize database
    from app.core.database import init_db
    await init_db()

    # Start Redis health monitor
    from app.core.redis import start_health_monitor
    await start_health_monitor()

    # Initialize LLM pool
    from app.ai_services.llm_manager import llm_manager
    await llm_manager.initialize()

    # Register event handlers (event-driven architecture)
    from app.core.events.handlers import register_all_handlers
    register_all_handlers()

    # Start event bus consumer (Redis Streams)
    from app.core.events.event_bus import event_bus
    await event_bus.start_consumer()

    # Start background position monitor (SL/TP enforcement)
    import asyncio
    from app.services.position_monitor import start_position_monitor
    asyncio.create_task(start_position_monitor())

    # Start live market feed (Upstox WebSocket or REST fallback)
    from app.services.live_feed import start_live_feed
    await start_live_feed("auto")
    logger.info("Live feed started")

    # Auto-start autonomous pipeline if configured
    if settings.ENVIRONMENT == "production":
        from app.services.autonomous_pipeline import start_autonomous_pipeline
        asyncio.create_task(start_autonomous_pipeline("auto"))
        logger.info("Autonomous pipeline auto-started")

    logger.info("%s started successfully", settings.APP_NAME)

    yield

    # ─── Shutdown ───
    logger.info("Shutting down %s...", settings.APP_NAME)

    from app.services.autonomous_pipeline import stop_autonomous_pipeline
    await stop_autonomous_pipeline()

    await event_bus.stop_consumer()

    from app.core.redis import close_redis
    await close_redis()

    from app.core.database import close_db
    await close_db()

    logger.info("%s shut down cleanly", settings.APP_NAME)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "Autonomous AI Trading Agent — event-driven, self-learning, self-executing. "
            "Proven strategies generate signals, context graph tracks market state, "
            "RL engine evolves weights, risk gates enforce limits. Full audit trail."
        ),
        version="3.0.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    from app.api.routes.trade import router as trade_router
    from app.api.routes.signals import router as signals_router
    from app.api.routes.portfolio import router as portfolio_router
    from app.api.routes.health import router as health_router
    from app.api.routes.stream import router as stream_router
    from app.api.routes.autonomous import router as auto_router
    from app.api.routes.events import router as events_router
    from app.api.routes.dashboard import router as dashboard_router

    app.include_router(dashboard_router, tags=["Dashboard"])
    app.include_router(trade_router, tags=["Trading"])
    app.include_router(signals_router, prefix="/signals", tags=["Signals"])
    app.include_router(portfolio_router, prefix="/portfolio", tags=["Portfolio"])
    app.include_router(auto_router, prefix="/auto", tags=["Autonomous"])
    app.include_router(events_router, prefix="/events", tags=["Events & Audit"])
    app.include_router(health_router, tags=["Health"])
    app.include_router(stream_router, tags=["Stream"])

    return app


app = create_app()
