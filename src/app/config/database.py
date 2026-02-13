"""Database connection management."""

import json
from typing import Optional

import asyncpg
import structlog

from .settings import get_settings

logger = structlog.get_logger()

_pool: Optional[asyncpg.Pool] = None


def _json_encoder(value):
    """Encode Python objects to JSON string for PostgreSQL json/jsonb columns.

    Backward-compatible: if value is already a JSON string (from existing
    json.dumps() calls), returns it as-is. Otherwise serializes with default=str
    to handle datetime and UUID objects.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _json_decoder(value):
    """Decode JSON string from PostgreSQL to Python objects."""
    if isinstance(value, str):
        return json.loads(value)
    return value


async def _init_connection(conn: asyncpg.Connection):
    """Initialize each connection with custom JSON/JSONB codecs.

    By default asyncpg returns json/jsonb as raw strings. This registers
    codecs so they're automatically decoded to Python dicts/lists.
    """
    await conn.set_type_codec(
        "json",
        encoder=_json_encoder,
        decoder=_json_decoder,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "jsonb",
        encoder=_json_encoder,
        decoder=_json_decoder,
        schema="pg_catalog",
    )


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
            init=_init_connection,
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
