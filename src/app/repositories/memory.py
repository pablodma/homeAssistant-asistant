"""Chat memory repository."""

import json
from datetime import datetime
from typing import Any, Optional

import structlog

from ..config.database import get_pool

logger = structlog.get_logger()


class MemoryRepository:
    """Repository for chat memory stored in PostgreSQL."""

    async def get_session(self, session_key: str) -> Optional[dict]:
        """Get a session by key."""
        try:
            pool = await get_pool()

            query = """
                SELECT session_key, created_at
                FROM chat_sessions
                WHERE session_key = $1
            """

            row = await pool.fetchrow(query, session_key)
            if row:
                return {
                    "session_key": row["session_key"],
                    "created_at": row["created_at"],
                }
            return None

        except Exception as e:
            logger.warning(
                "Failed to get session, table might not exist yet",
                session_key=session_key,
                error=str(e),
            )
            return None

    async def create_session(
        self,
        session_key: str,
        tenant_id: str,
        phone: str,
    ) -> dict:
        """Create a new session."""
        try:
            pool = await get_pool()

            query = """
                INSERT INTO chat_sessions (session_key, tenant_id, phone)
                VALUES ($1, $2, $3)
                ON CONFLICT (session_key) DO UPDATE SET updated_at = NOW()
                RETURNING session_key, created_at
            """

            row = await pool.fetchrow(query, session_key, tenant_id, phone)
            return {
                "session_key": row["session_key"],
                "created_at": row["created_at"],
            }

        except Exception as e:
            logger.warning(
                "Failed to create session, table might not exist yet",
                session_key=session_key,
                error=str(e),
            )
            # Return a mock session for now
            return {
                "session_key": session_key,
                "created_at": datetime.now(),
            }

    async def get_messages(
        self,
        session_key: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get messages for a session."""
        try:
            pool = await get_pool()

            query = """
                SELECT role, content, created_at as timestamp
                FROM chat_messages
                WHERE session_key = $1
                ORDER BY created_at DESC
                LIMIT $2
            """

            rows = await pool.fetch(query, session_key, limit)
            # Reverse to get chronological order
            return [
                {
                    "role": row["role"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                }
                for row in reversed(rows)
            ]

        except Exception as e:
            logger.warning(
                "Failed to get messages, table might not exist yet",
                session_key=session_key,
                error=str(e),
            )
            return []

    async def add_message(
        self,
        session_key: str,
        role: str,
        content: str,
    ) -> None:
        """Add a message to a session."""
        try:
            pool = await get_pool()

            query = """
                INSERT INTO chat_messages (session_key, role, content)
                VALUES ($1, $2, $3)
            """

            await pool.execute(query, session_key, role, content)

        except Exception as e:
            logger.warning(
                "Failed to add message, table might not exist yet",
                session_key=session_key,
                error=str(e),
            )

    async def clear_messages(self, session_key: str) -> None:
        """Clear all messages for a session."""
        try:
            pool = await get_pool()

            query = """
                DELETE FROM chat_messages
                WHERE session_key = $1
            """

            await pool.execute(query, session_key)

        except Exception as e:
            logger.warning(
                "Failed to clear messages",
                session_key=session_key,
                error=str(e),
            )
