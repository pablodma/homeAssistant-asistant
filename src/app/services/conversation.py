"""Conversation memory service."""

from datetime import datetime
from typing import Optional

import structlog

from ..config.database import get_pool
from ..repositories.memory import MemoryRepository

logger = structlog.get_logger()


class Message:
    """A conversation message."""

    def __init__(self, role: str, content: str, timestamp: Optional[datetime] = None):
        self.role = role
        self.content = content
        self.timestamp = timestamp or datetime.now()

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
        }


class ConversationService:
    """Service for managing conversation history."""

    def __init__(self):
        self.repo = MemoryRepository()

    async def get_or_create(
        self,
        phone: str,
        tenant_id: str,
    ) -> dict:
        """Get or create a conversation session."""
        session_key = f"{tenant_id}_{phone}"

        # Check if session exists
        session = await self.repo.get_session(session_key)
        if session:
            return session

        # Create new session
        return await self.repo.create_session(session_key, tenant_id, phone)

    async def get_history(
        self,
        phone: str,
        tenant_id: str,
        limit: int = 10,
    ) -> list[Message]:
        """Get conversation history for a user."""
        session_key = f"{tenant_id}_{phone}"
        messages = await self.repo.get_messages(session_key, limit)

        return [
            Message(
                role=msg["role"],
                content=msg["content"],
                timestamp=msg.get("timestamp"),
            )
            for msg in messages
        ]

    async def add_message(
        self,
        phone: str,
        tenant_id: str,
        role: str,
        content: str,
    ) -> None:
        """Add a message to the conversation history."""
        session_key = f"{tenant_id}_{phone}"
        await self.repo.add_message(session_key, role, content)

    async def clear_history(
        self,
        phone: str,
        tenant_id: str,
    ) -> None:
        """Clear conversation history for a user."""
        session_key = f"{tenant_id}_{phone}"
        await self.repo.clear_messages(session_key)
