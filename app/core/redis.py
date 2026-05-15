"""
Redis connection manager — async + sync pools, health checks.
Async and sync connection pools with periodic health checks.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import redis.asyncio as aioredis
import redis as sync_redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_async_pool: Optional[aioredis.Redis] = None
_sync_pool: Optional[sync_redis.Redis] = None
_health_task: Optional[asyncio.Task] = None


async def get_redis_client() -> aioredis.Redis:
    """Get the async Redis client, creating the pool if needed."""
    global _async_pool
    if _async_pool is None:
        _async_pool = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
        logger.info("Async Redis pool created: %s", settings.REDIS_URL)
    return _async_pool


def get_sync_redis_client() -> sync_redis.Redis:
    """Get the sync Redis client (for use inside synchronous tool threads)."""
    global _sync_pool
    if _sync_pool is None:
        _sync_pool = sync_redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
        logger.info("Sync Redis pool created: %s", settings.REDIS_URL)
    return _sync_pool


async def redis_health_check():
    """Periodic health check — runs every 30s."""
    while True:
        try:
            client = await get_redis_client()
            await client.ping()
        except Exception as e:
            logger.error("Redis health check failed: %s", e)
        await asyncio.sleep(30)


async def start_health_monitor():
    """Start the background health check task."""
    global _health_task
    if _health_task is None:
        _health_task = asyncio.create_task(redis_health_check())


async def close_redis():
    """Cleanup on shutdown."""
    global _async_pool, _sync_pool, _health_task
    if _health_task:
        _health_task.cancel()
        _health_task = None
    if _async_pool:
        await _async_pool.close()
        _async_pool = None
    if _sync_pool:
        _sync_pool.close()
        _sync_pool = None
    logger.info("Redis connections closed")


# ─── Convenience helpers ───

async def redis_get(key: str) -> Optional[str]:
    client = await get_redis_client()
    return await client.get(key)


async def redis_set(key: str, value: str, ttl: Optional[int] = None):
    client = await get_redis_client()
    if ttl:
        await client.setex(key, ttl, value)
    else:
        await client.set(key, value)


async def redis_delete(key: str):
    client = await get_redis_client()
    await client.delete(key)


async def redis_hgetall(key: str) -> dict:
    client = await get_redis_client()
    return await client.hgetall(key)


async def redis_hset(key: str, mapping: dict, ttl: Optional[int] = None):
    client = await get_redis_client()
    await client.hset(key, mapping=mapping)
    if ttl:
        await client.expire(key, ttl)
