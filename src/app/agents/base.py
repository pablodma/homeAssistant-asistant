"""Base agent class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from ..config import get_settings
from ..services.prompt_loader import PromptLoader

logger = structlog.get_logger()


@dataclass
class AgentResult:
    """Result from agent processing."""

    response: str
    agent_used: str
    sub_agent_used: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    """Base class for all agents."""

    name: str = "base"

    def __init__(self):
        """Initialize the agent."""
        self.settings = get_settings()
        self.prompt_loader = PromptLoader()
        self._prompt: Optional[str] = None

    async def get_prompt(self, tenant_id: str) -> str:
        """Get the prompt for this agent.

        Args:
            tenant_id: The tenant ID.

        Returns:
            The prompt content.
        """
        if self._prompt is None:
            self._prompt = await self.prompt_loader.get_prompt(self.name, tenant_id)
        return self._prompt

    @abstractmethod
    async def process(
        self,
        message: str,
        phone: str,
        tenant_id: str,
        history: list,
        **kwargs,
    ) -> AgentResult:
        """Process a message and return a result.

        Args:
            message: The user's message.
            phone: The user's phone number.
            tenant_id: The tenant ID.
            history: Conversation history.
            **kwargs: Additional arguments.

        Returns:
            The agent's response.
        """
        pass

    def _format_history(self, history: list) -> list[dict[str, str]]:
        """Format conversation history for LLM.

        Args:
            history: List of Message objects.

        Returns:
            List of dicts with role and content.
        """
        return [{"role": msg.role, "content": msg.content} for msg in history]
