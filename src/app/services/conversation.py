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

    # Prefix for onboarding sessions (users without tenant_id)
    ONBOARDING_PREFIX = "onboarding"
    # Sentinel UUID for onboarding sessions (chat_sessions.tenant_id is UUID NOT NULL)
    ONBOARDING_TENANT_UUID = "00000000-0000-0000-0000-000000000000"

    def __init__(self):
        self.repo = MemoryRepository()

    def _session_key(self, phone: str, tenant_id: str) -> str:
        """Build the session key.

        For registered users: "{tenant_id}_{phone}"
        For onboarding (no tenant): "onboarding_{phone}"
        """
        if tenant_id:
            return f"{tenant_id}_{phone}"
        return f"{self.ONBOARDING_PREFIX}_{phone}"

    async def get_or_create(
        self,
        phone: str,
        tenant_id: str = "",
    ) -> dict:
        """Get or create a conversation session."""
        session_key = self._session_key(phone, tenant_id)
        effective_tenant = tenant_id or self.ONBOARDING_TENANT_UUID

        session = await self.repo.get_session(session_key)
        if session:
            return session

        return await self.repo.create_session(session_key, effective_tenant, phone)

    async def get_history(
        self,
        phone: str,
        tenant_id: str = "",
        limit: int = 10,
    ) -> list[Message]:
        """Get conversation history for a user."""
        session_key = self._session_key(phone, tenant_id)
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
        tenant_id: str = "",
        role: str = "user",
        content: str = "",
    ) -> None:
        """Add a message to the conversation history."""
        session_key = self._session_key(phone, tenant_id)
        await self.repo.add_message(session_key, role, content)

    async def clear_history(
        self,
        phone: str,
        tenant_id: str = "",
    ) -> None:
        """Clear conversation history for a user."""
        session_key = self._session_key(phone, tenant_id)
        await self.repo.clear_messages(session_key)

    async def migrate_onboarding_session(
        self,
        phone: str,
        tenant_id: str,
    ) -> None:
        """Clean up onboarding session after registration completes.

        After a user registers, we clear their onboarding session
        so they start fresh with the regular bot.
        """
        onboarding_key = f"{self.ONBOARDING_PREFIX}_{phone}"
        await self.repo.clear_messages(onboarding_key)
