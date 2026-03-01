"""Base agent class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from ..config import get_settings
from ..services.backend_client import get_backend_client
from ..services.prompt_loader import PromptLoader

logger = structlog.get_logger()

FIRST_TIME_AGENTS: set[str] = {"finance", "agenda", "shopping", "vehicle"}

FIRST_TIME_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "completar_configuracion_inicial",
        "description": (
            "Marca la configuración inicial del agente como completada. "
            "Usar cuando el usuario terminó el setup de primera vez."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


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

    async def check_first_time(self, phone: str) -> bool:
        """Check if this is the user's first time using this agent.

        Args:
            phone: The user's phone number.

        Returns:
            True if first time, False if already onboarded.
        """
        if self.name not in FIRST_TIME_AGENTS:
            return False

        try:
            backend = get_backend_client()
            resp = await backend.get(
                "/api/v1/agent-onboarding/status",
                timeout=10.0,
                params={"phone": phone, "agent": self.name},
            )
            if resp.status_code == 200:
                return resp.json().get("is_first_time", False)
            logger.warning(
                "First-time check failed",
                status=resp.status_code,
                agent=self.name,
            )
            return False
        except Exception:
            logger.exception("Error checking first-time status", agent=self.name)
            return False

    async def complete_first_time(self, phone: str) -> str:
        """Mark first-time onboarding as complete for this agent.

        Args:
            phone: The user's phone number.

        Returns:
            Confirmation message for the LLM tool result.
        """
        try:
            backend = get_backend_client()
            resp = await backend.post(
                "/api/v1/agent-onboarding/complete",
                timeout=10.0,
                json={"phone": phone, "agent_name": self.name},
            )
            if resp.status_code == 200:
                return "Configuración inicial completada exitosamente."
            logger.warning(
                "First-time complete failed",
                status=resp.status_code,
                agent=self.name,
            )
            return "No se pudo marcar la configuración como completada."
        except Exception:
            logger.exception("Error completing first-time onboarding", agent=self.name)
            return "Error al completar la configuración inicial."

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
