"""Database connection management."""

from typing import Optional

import asyncpg
import structlog

from .settings import get_settings

logger = structlog.get_logger()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create database connection pool."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
        logger.info("Database pool created")
    return _pool


async def close_pool() -> None:
    """Close database connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")


async def get_connection() -> asyncpg.Connection:
    """Get a database connection from the pool."""
    pool = await get_pool()
    return await pool.acquire()
