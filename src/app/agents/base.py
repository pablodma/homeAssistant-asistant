"""Base agent class."""

from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from ..config import get_settings
from ..config import get_settings as _get_settings  # alias used by _init_langfuse
from ..services.backend_client import get_backend_client
from ..services.prompt_loader import PromptLoader

logger = structlog.get_logger()

# Langfuse globals — initialized once at module load
_langfuse_client: Any = None
_current_langfuse_trace: ContextVar[Any] = ContextVar("langfuse_trace", default=None)


def _init_langfuse() -> bool:
    """Initialize Langfuse if configured. Returns True if enabled."""
    global _langfuse_client
    settings = _get_settings()
    if not settings.langfuse_enabled or not settings.langfuse_secret_key:
        return False
    try:
        from langfuse import Langfuse
        _langfuse_client = Langfuse(
            secret_key=settings.langfuse_secret_key,
            public_key=settings.langfuse_public_key,
            host=settings.langfuse_host,
        )
        logger.info("Langfuse initialized successfully")
        return True
    except Exception:
        logger.warning("Langfuse initialization failed")
        return False


_LANGFUSE_ENABLED = _init_langfuse()

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

FIRST_TIME_TOOL_DEFINITION_ANTHROPIC: dict[str, Any] = {
    "name": "completar_configuracion_inicial",
    "description": (
        "Marca la configuración inicial del agente como completada. "
        "Usar cuando el usuario terminó el setup de primera vez."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


def openai_tool_to_anthropic(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAI-format tool definition to Anthropic format."""
    func = tool["function"]
    return {
        "name": func["name"],
        "description": func["description"],
        "input_schema": func["parameters"],
    }


@dataclass
class ToolOutput:
    """Structured output from a domain tool execution.

    Used by the supervisor pattern: sub-agents return data, the supervisor
    formulates the user-facing response.
    """

    success: bool
    domain: str                                    # "finance", "agenda", etc.
    tool_name: str                                 # "registrar_gasto"
    tool_args: dict = field(default_factory=dict)  # args passed to the tool
    data: dict = field(default_factory=dict)       # raw result from backend/DB
    formatted_text: str = ""                       # backward-compat formatted text
    quick_actions: Optional[dict[str, Any]] = None
    error: Optional[str] = None


@dataclass
class AgentResult:
    """Result from agent processing."""

    response: str
    agent_used: str
    sub_agent_used: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    metadata: Optional[dict[str, Any]] = field(default_factory=dict)
    response_type: Optional[str] = None
    risk_level: Optional[str] = None
    requires_orchestrator_final: bool = False
    tool_output: Optional[ToolOutput] = None


class BaseAgent(ABC):
    """Base class for all agents."""

    name: str = "base"

    def __init__(self):
        """Initialize the agent."""
        self.settings = get_settings()
        self.prompt_loader = PromptLoader()
        self._prompt: Optional[str] = None
        self._langfuse_enabled = _LANGFUSE_ENABLED

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
            # Prefer onboarding guidance when status cannot be verified.
            return True
        except Exception:
            logger.exception("Error checking first-time status", agent=self.name)
            # Fail open to first-time flow on transient backend/network errors.
            return True

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

    # --- Langfuse tracing ---

    def _start_trace(
        self,
        *,
        name: str = "",
        user_id: str = "",
        session_id: str = "",
        input_data: Any = None,
        metadata: Optional[dict] = None,
    ) -> Any:
        """Start a Langfuse trace. Returns the trace object (or None)."""
        if not self._langfuse_enabled or not _langfuse_client:
            return None
        try:
            trace = _langfuse_client.trace(
                name=name or f"{self.name}-process",
                user_id=user_id or None,
                session_id=session_id or None,
                input=input_data,
                metadata=metadata,
            )
            _current_langfuse_trace.set(trace)
            return trace
        except Exception:
            logger.debug("Langfuse trace creation failed")
            return None

    def _end_trace(self, output: Any = None) -> None:
        """Finalize the current Langfuse trace with output."""
        trace = _current_langfuse_trace.get(None)
        if trace is None:
            return
        try:
            trace.update(output=output)
            _langfuse_client.flush()
        except Exception:
            logger.debug("Langfuse trace finalization failed")
        finally:
            _current_langfuse_trace.set(None)

    @staticmethod
    def _safe_serialize(obj: Any) -> Any:
        """Convert Anthropic/OpenAI objects to JSON-safe dicts."""
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, dict):
            return {k: BaseAgent._safe_serialize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [BaseAgent._safe_serialize(i) for i in obj]
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "__dict__"):
            return {k: str(v)[:500] for k, v in obj.__dict__.items() if not k.startswith("_")}
        return str(obj)[:500]

    def _log_generation(
        self,
        *,
        name: str = "",
        model: str = "",
        input_msgs: Any = None,
        output_content: Any = None,
        usage_in: int = 0,
        usage_out: int = 0,
        metadata: Optional[dict] = None,
    ) -> None:
        """Log an LLM generation to Langfuse, nested under current trace."""
        if not self._langfuse_enabled or not _langfuse_client:
            return
        try:
            parent = _current_langfuse_trace.get(None) or _langfuse_client
            parent.generation(
                name=name or f"{self.name}-generation",
                model=model,
                input=self._safe_serialize(input_msgs),
                output=self._safe_serialize(output_content),
                usage={"input": usage_in, "output": usage_out},
                metadata=metadata,
            )
        except Exception as exc:
            logger.debug("Langfuse generation logging failed", error=str(exc))

    def _init_llm_client(self, provider_setting: str) -> None:
        """Initialize LLM client based on provider setting.

        Sets self.provider ("openai" | "anthropic") and self.client.

        Args:
            provider_setting: Name of the settings attribute for provider.
        """
        self.provider: str = getattr(self.settings, provider_setting, "anthropic")
        if self.provider == "anthropic":
            self.client = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        else:
            self.client = AsyncOpenAI(api_key=self.settings.openai_api_key)

    # --- Anthropic helpers ---

    @staticmethod
    def _extract_system_and_messages(
        messages: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Separate system messages from user/assistant messages.

        Anthropic requires system as a top-level param, not in messages.

        Returns:
            (system_text, filtered_messages)
        """
        system_parts: list[str] = []
        filtered: list[dict[str, Any]] = []
        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
            else:
                filtered.append(msg)
        return "\n\n".join(system_parts), filtered

    @staticmethod
    def _extract_tool_use(response: Any) -> Optional[tuple[str, dict, str]]:
        """Extract first tool use from Anthropic response.

        Returns:
            (tool_name, tool_args, tool_use_id) or None
        """
        for block in response.content:
            if block.type == "tool_use":
                return block.name, block.input, block.id
        return None

    @staticmethod
    def _extract_all_tool_uses(response: Any) -> list[tuple[str, dict, str]]:
        """Extract ALL tool_use blocks from Anthropic response.

        Returns:
            List of (tool_name, tool_args, tool_use_id) tuples.
        """
        return [
            (block.name, block.input, block.id)
            for block in response.content
            if block.type == "tool_use"
        ]

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Extract text content from Anthropic response."""
        parts = []
        for block in response.content:
            if block.type == "text":
                parts.append(block.text)
        return "\n".join(parts)

    @staticmethod
    def _build_tool_result_msg(tool_use_id: str, content: str) -> dict[str, Any]:
        """Build Anthropic tool_result message."""
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                }
            ],
        }

    @staticmethod
    def _anthropic_tokens(response: Any) -> tuple[int, int]:
        """Extract token counts from Anthropic response."""
        usage = response.usage
        return usage.input_tokens, usage.output_tokens

    def _format_history(self, history: list) -> list[dict[str, str]]:
        """Format conversation history for LLM.

        Args:
            history: List of Message objects.

        Returns:
            List of dicts with role and content.
        """
        return [
            {"role": msg["role"], "content": msg["content"]} if isinstance(msg, dict)
            else {"role": msg.role, "content": msg.content}
            for msg in history
        ]
