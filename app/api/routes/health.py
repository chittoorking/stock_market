"""
Health check endpoints.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter

from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health_check():
    """Basic health check."""
    checks = {"status": "healthy", "app": settings.APP_NAME}

    # Redis
    try:
        from app.core.redis import get_redis_client
        client = await get_redis_client()
        await client.ping()
        checks["redis"] = "connected"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        checks["status"] = "degraded"

    # Database
    try:
        from app.core.database import async_session_factory
        async with async_session_factory() as db:
            await db.execute("SELECT 1")
        checks["database"] = "connected"
    except Exception as e:
        checks["database"] = f"error: {e}"
        checks["status"] = "degraded"

    # LLM
    from app.ai_services.llm_manager import llm_manager
    llm_status = llm_manager.get_status()
    checks["llm_accounts"] = len(llm_status)
    checks["llm_available"] = sum(1 for s in llm_status if s["can_handle"])

    # Broker
    checks["broker"] = settings.get_active_broker()

    return checks


@router.get("/health/llm")
async def llm_status():
    """Detailed LLM account status."""
    from app.ai_services.llm_manager import llm_manager
    return {"accounts": llm_manager.get_status()}
