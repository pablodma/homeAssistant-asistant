"""Router Agent - Main orchestrator."""

import json
from datetime import datetime
from typing import Any, Optional

import structlog
from openai import AsyncOpenAI

from .base import AgentResult, BaseAgent

logger = structlog.get_logger()

SENSITIVE_TOOL_NAMES: set[str] = {
    "cancel_subscription",
    "eliminar_gasto",
    "eliminar_evento",
    "eliminar_recordatorio",
    "eliminar_item",
    "limpiar_lista",
}

IDENTITY_LEAK_MARKERS: tuple[str, ...] = (
    "como agente de",
    "soy el modulo de",
    "soy el módulo de",
    "solo me encargo de",
)


class RouterAgent(BaseAgent):
    """Router agent that orchestrates sub-agents."""

    name = "router"

    def __init__(self):
        """Initialize the router agent."""
        super().__init__()
        self.client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        self._sub_agents: dict[str, BaseAgent] = {}

    def _get_sub_agent(self, agent_name: str) -> Optional[BaseAgent]:
        """Get or create a sub-agent by name.

        Args:
            agent_name: Name of the agent.

        Returns:
            The agent instance or None.
        """
        if agent_name not in self._sub_agents:
            if agent_name == "finance":
                from .finance import FinanceAgent
                self._sub_agents[agent_name] = FinanceAgent()
            elif agent_name == "agenda":
                from .calendar import CalendarAgent
                self._sub_agents[agent_name] = CalendarAgent()
            elif agent_name == "shopping":
                from .shopping import ShoppingAgent
                self._sub_agents[agent_name] = ShoppingAgent()
            elif agent_name == "vehicle":
                from .vehicle import VehicleAgent
                self._sub_agents[agent_name] = VehicleAgent()
            elif agent_name == "subscription":
                from .subscription import SubscriptionAgent
                self._sub_agents[agent_name] = SubscriptionAgent()

        return self._sub_agents.get(agent_name)

    def _metadata_has_failed_tool(self, metadata: dict[str, Any] | None) -> bool:
        """Return True when metadata reports a failed tool execution."""
        if not isinstance(metadata, dict):
            return False

        result = metadata.get("result")
        if isinstance(result, dict) and result.get("success") is False:
            return True
        return False

    def _is_sensitive_tool(self, metadata: dict[str, Any] | None) -> bool:
        """Return True when metadata references a sensitive/destructive tool."""
        if not isinstance(metadata, dict):
            return False

        tool_name = metadata.get("tool")
        return isinstance(tool_name, str) and tool_name in SENSITIVE_TOOL_NAMES

    def _contains_identity_leak_marker(self, text: str) -> bool:
        """Best-effort identity leak marker detection."""
        lowered = text.lower()
        return any(marker in lowered for marker in IDENTITY_LEAK_MARKERS)

    def _should_finalize(self, results: list[AgentResult]) -> bool:
        """Decide if router should produce final user-facing response."""
        if not getattr(self.settings, "orchestrator_finalizer_enabled", False):
            return False

        if getattr(self.settings, "orchestrator_finalize_on_multi_agent_only", False):
            return len(results) > 1

        if len(results) > 1:
            return True

        for result in results:
            if getattr(result, "requires_orchestrator_final", False):
                return True

            if getattr(result, "risk_level", None) in {"medium", "high"}:
                return True

            if getattr(result, "response_type", None) in {"conversational", "error"}:
                return True

            if self._is_sensitive_tool(result.metadata):
                return True

            if self._metadata_has_failed_tool(result.metadata):
                return True

            if self._contains_identity_leak_marker(result.response):
                return True

        return False

    async def _get_finalizer_prompt(self, tenant_id: str) -> str:
        """Load finalizer prompt from docs/prompts with safe fallback."""
        prompt = await self.prompt_loader.get_prompt("router-finalizer", tenant_id)
        if prompt and prompt.strip():
            return prompt

        return (
            "Sos Aira. Recibís respuestas internas de sub-agentes y debés redactar una única "
            "respuesta final para WhatsApp. Mantené tono breve y claro.\n"
            "REGLAS: no inventes hechos, no cambies montos/fechas/estados, no reveles "
            "sub-agentes o módulos internos."
        )

    def _build_finalizer_payload(
        self,
        message: str,
        results: list[AgentResult],
        combined_response: str,
    ) -> str:
        """Build structured payload for finalizer LLM pass."""
        summarized_results: list[dict[str, Any]] = []
        for result in results:
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            tool_name = metadata.get("tool") if isinstance(metadata.get("tool"), str) else None
            tool_result = metadata.get("result") if isinstance(metadata.get("result"), dict) else {}
            summarized_results.append(
                {
                    "agent_used": result.agent_used,
                    "response": result.response,
                    "response_type": getattr(result, "response_type", None),
                    "risk_level": getattr(result, "risk_level", None),
                    "requires_orchestrator_final": getattr(result, "requires_orchestrator_final", False),
                    "tool": tool_name,
                    "tool_success": tool_result.get("success") if isinstance(tool_result, dict) else None,
                }
            )

        payload = {
            "user_message": message,
            "draft_response": combined_response,
            "sub_agent_outputs": summarized_results,
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    async def _finalize_response(
        self,
        message: str,
        tenant_id: str,
        results: list[AgentResult],
        combined_response: str,
    ) -> tuple[str, int, int, bool]:
        """Generate final user-facing response from sub-agent outputs.

        Returns:
            tuple(final_text, finalizer_tokens_in, finalizer_tokens_out, fallback_used)
        """
        prompt = await self._get_finalizer_prompt(tenant_id)
        payload = self._build_finalizer_payload(message, results, combined_response)
        finalizer_model = getattr(
            self.settings,
            "orchestrator_finalizer_model",
            self.settings.openai_router_model,
        )

        try:
            response = await self.client.chat.completions.create(
                model=finalizer_model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": payload},
                ],
                temperature=0.2,
                max_completion_tokens=900,
            )

            tokens_in = response.usage.prompt_tokens if response.usage else 0
            tokens_out = response.usage.completion_tokens if response.usage else 0
            text = (response.choices[0].message.content or "").strip()

            if not text:
                logger.warning("Finalizer returned empty response, using passthrough fallback")
                return combined_response, tokens_in, tokens_out, True

            return text, tokens_in, tokens_out, False
        except Exception as e:
            logger.error("Finalizer failed, using passthrough fallback", error=str(e))
            return combined_response, 0, 0, True

    async def process(
        self,
        message: str,
        phone: str,
        tenant_id: str,
        history: list,
        **kwargs,
    ) -> AgentResult:
        """Process a message by routing to the appropriate sub-agent.

        Uses LLM-only routing (no keyword detection).
        See ADR-002: docs/architecture/decisions/002-llm-only-routing.md

        Args:
            message: The user's message.
            phone: The user's phone number.
            tenant_id: The tenant ID.
            history: Conversation history.

        Returns:
            The agent's response.
        """
        logger.info(
            "Router processing message",
            phone=phone,
            message=message[:50] + "..." if len(message) > 50 else message,
        )

        # LLM-only routing - all logic defined in prompt
        return await self._process_with_llm(
            message=message,
            phone=phone,
            tenant_id=tenant_id,
            history=history,
        )

    async def _process_with_llm(
        self,
        message: str,
        phone: str,
        tenant_id: str,
        history: list,
    ) -> AgentResult:
        """Process message using LLM for routing or direct response.

        Args:
            message: The user's message.
            phone: The user's phone number.
            tenant_id: The tenant ID.
            history: Conversation history.

        Returns:
            The agent's response.
        """
        prompt = await self.get_prompt(tenant_id)

        # Build messages
        now = datetime.now()
        day_name = ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo'][now.weekday()]
        messages = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": f"Hoy es {day_name} {now.strftime('%Y-%m-%d %H:%M')}."},
        ]

        # Add history
        for msg in history[-6:]:  # Last 6 messages
            messages.append({"role": msg.role, "content": msg.content})

        # Add current message wrapped with delimiters for injection defense
        messages.append({"role": "user", "content": f"[USER_MSG]{message}[/USER_MSG]"})

        # Define tools for agent selection
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "finance_agent",
                    "description": "Gestiona gastos, presupuestos y consultas financieras",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user_request": {
                                "type": "string",
                                "description": "El pedido del usuario relacionado con finanzas",
                            }
                        },
                        "required": ["user_request"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "agenda_agent",
                    "description": "Gestiona eventos, citas, agenda y recordatorios",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user_request": {
                                "type": "string",
                                "description": "El pedido del usuario relacionado con calendario, agenda o recordatorios",
                            }
                        },
                        "required": ["user_request"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "shopping_agent",
                    "description": "Gestiona listas de compras",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user_request": {
                                "type": "string",
                                "description": "El pedido del usuario relacionado con listas de compras",
                            }
                        },
                        "required": ["user_request"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "vehicle_agent",
                    "description": "Gestiona vehículos y mantenimiento",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user_request": {
                                "type": "string",
                                "description": "El pedido del usuario relacionado con vehículos",
                            }
                        },
                        "required": ["user_request"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "subscription_agent",
                    "description": "Gestiona plan, suscripción, upgrade, downgrade, cancelar, uso, invitar miembros al hogar",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user_request": {
                                "type": "string",
                                "description": "El pedido del usuario relacionado con suscripción o miembros",
                            }
                        },
                        "required": ["user_request"],
                    },
                },
            },
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.settings.openai_router_model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.3,
                max_completion_tokens=1000,
            )

            choice = response.choices[0]
            tokens_in = response.usage.prompt_tokens if response.usage else None
            tokens_out = response.usage.completion_tokens if response.usage else None

            # Check if LLM wants to use tools
            if choice.message.tool_calls:
                results: list[AgentResult] = []
                agents_used: list[str] = []
                total_tokens_in = tokens_in or 0
                total_tokens_out = tokens_out or 0

                for tool_call in choice.message.tool_calls:
                    agent_name = tool_call.function.name.replace("_agent", "")
                    logger.info(f"LLM routing to {agent_name}")

                    sub_agent = self._get_sub_agent(agent_name)
                    if not sub_agent:
                        logger.warning(f"Unknown sub-agent: {agent_name}")
                        continue

                    try:
                        args = json.loads(tool_call.function.arguments)
                        user_request_raw = args.get("user_request", message)
                        user_request = (
                            user_request_raw
                            if isinstance(user_request_raw, str) and user_request_raw.strip()
                            else message
                        )

                        result = await sub_agent.process(
                            message=user_request,
                            phone=phone,
                            tenant_id=tenant_id,
                            history=history,
                        )
                        results.append(result)
                        agents_used.append(agent_name)
                        total_tokens_in += result.tokens_in or 0
                        total_tokens_out += result.tokens_out or 0
                    except Exception as e:
                        logger.error(f"Sub-agent {agent_name} failed", error=str(e))

                if results:
                    combined_response = "\n\n".join(r.response for r in results)
                    # Merge metadata from all results
                    merged_metadata: dict = {}
                    for r in results:
                        if r.metadata:
                            merged_metadata.update(r.metadata)

                    finalizer_attempted = False
                    finalizer_fallback_used = False
                    response_mode = "passthrough"
                    if self._should_finalize(results):
                        finalizer_attempted = True
                        (
                            combined_response,
                            finalizer_tokens_in,
                            finalizer_tokens_out,
                            finalizer_fallback_used,
                        ) = await self._finalize_response(
                            message=message,
                            tenant_id=tenant_id,
                            results=results,
                            combined_response=combined_response,
                        )
                        total_tokens_in += finalizer_tokens_in
                        total_tokens_out += finalizer_tokens_out
                        response_mode = (
                            "passthrough"
                            if finalizer_fallback_used
                            else "orchestrator_finalized"
                        )

                    merged_metadata["response_mode"] = response_mode
                    merged_metadata["finalizer_attempted"] = finalizer_attempted
                    merged_metadata["finalizer_fallback_used"] = finalizer_fallback_used

                    return AgentResult(
                        response=combined_response,
                        agent_used=self.name,
                        sub_agent_used=", ".join(agents_used),
                        tokens_in=total_tokens_in,
                        tokens_out=total_tokens_out,
                        metadata=merged_metadata or None,
                    )

            # Direct response from router
            response_text = choice.message.content or "No pude procesar tu mensaje."

            return AgentResult(
                response=response_text,
                agent_used=self.name,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

        except Exception as e:
            logger.error("LLM processing failed", error=str(e))
            return AgentResult(
                response="Hubo un problema procesando tu mensaje. Por favor, intentá de nuevo.",
                agent_used=self.name,
            )
