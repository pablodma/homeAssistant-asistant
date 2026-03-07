"""Router Agent - Main orchestrator using LangGraph StateGraph.

Architecture:
  START -> [orchestrate_node] -> [guardrails_node] -> END

  orchestrate_node: LLM-only routing (gpt-4.1-nano) + sub-agent dispatch
  guardrails_node: 3-layer response security (injection / coherence / fabrication)
                   Only active when settings.final_security_check_enabled = True.

Checkpointing:
  - MemorySaver in development (in-process, cleared on restart)
  - AsyncPostgresSaver in production (PostgreSQL, persistent)
  - thread_id = "{tenant_id}_{phone}" for per-conversation state isolation
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional
from typing_extensions import TypedDict

import structlog
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from openai import AsyncOpenAI

from .base import AgentResult, BaseAgent
from ..config import get_settings
from ..services.llm_breaker import CircuitBreakerOpenError, get_circuit_breaker

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
    "soy el modulo de",
    "solo me encargo de",
)

_CIRCUIT_OPEN_RESPONSE = (
    "Estoy teniendo dificultades tecnicas en este momento. "
    "Por favor, intenta de nuevo en unos minutos."
)


class AgentState(TypedDict):
    """Typed state passed through the LangGraph agent graph."""

    # Input
    message: str
    phone: str
    tenant_id: str
    history: list

    # Orchestration output
    agent_result: Optional[AgentResult]

    # Response (may be modified by guardrails)
    final_response: str

    # Security
    block_reason: Optional[str]
    injection_risk: float


class RouterAgent(BaseAgent):
    """Router agent that orchestrates sub-agents via LangGraph StateGraph."""

    name = "router"

    def __init__(self) -> None:
        super().__init__()
        self.client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        self._sub_agents: dict[str, BaseAgent] = {}
        self._compiled_graph = None

    # -----------------------------------------------------------------------
    # Sub-agent registry
    # -----------------------------------------------------------------------

    def _get_sub_agent(self, agent_name: str) -> Optional[BaseAgent]:
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

    # -----------------------------------------------------------------------
    # LangGraph construction
    # -----------------------------------------------------------------------

    async def _build_graph(self):
        """Build and compile the StateGraph with the appropriate checkpointer."""
        graph = StateGraph(AgentState)

        graph.add_node("orchestrate", self._orchestrate_node)
        graph.add_node("guardrails", self._guardrails_node)

        graph.set_entry_point("orchestrate")
        graph.add_edge("orchestrate", "guardrails")
        graph.add_edge("guardrails", END)

        checkpointer = await self._create_checkpointer()
        return graph.compile(checkpointer=checkpointer)

    async def _create_checkpointer(self):
        """Return the appropriate checkpointer based on settings."""
        if getattr(self.settings, "langgraph_checkpointing", "memory") == "postgres":
            try:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
                checkpointer = AsyncPostgresSaver.from_conn_string(self.settings.database_url)
                await checkpointer.setup()
                logger.info("LangGraph using PostgreSQL checkpointing")
                return checkpointer
            except Exception as exc:
                logger.warning(
                    "PostgreSQL checkpointer unavailable, falling back to MemorySaver",
                    error=str(exc),
                )
        return MemorySaver()

    async def _get_compiled_graph(self):
        if self._compiled_graph is None:
            self._compiled_graph = await self._build_graph()
        return self._compiled_graph

    # -----------------------------------------------------------------------
    # Graph nodes
    # -----------------------------------------------------------------------

    async def _orchestrate_node(self, state: AgentState) -> dict:
        """Route the message to sub-agent(s) and collect the response."""
        breaker = get_circuit_breaker()

        if not await breaker.allow_call():
            return {
                "agent_result": AgentResult(
                    response=_CIRCUIT_OPEN_RESPONSE,
                    agent_used=self.name,
                    metadata={"circuit_breaker": "open"},
                ),
                "final_response": _CIRCUIT_OPEN_RESPONSE,
            }

        try:
            result = await self._process_with_llm(
                message=state["message"],
                phone=state["phone"],
                tenant_id=state["tenant_id"],
                history=state["history"],
            )
            await breaker.record_success()
            return {"agent_result": result, "final_response": result.response}
        except Exception as exc:
            await breaker.record_failure(exc)
            logger.error("Orchestration node failed", error=str(exc))
            fallback = AgentResult(
                response="Hubo un problema procesando tu mensaje. Por favor, intenta de nuevo.",
                agent_used=self.name,
            )
            return {"agent_result": fallback, "final_response": fallback.response}

    async def _guardrails_node(self, state: AgentState) -> dict:
        """Run the response through the security guardrails pipeline."""
        settings = get_settings()
        if not settings.final_security_check_enabled:
            return {"block_reason": None, "injection_risk": 0.0}

        try:
            from ..services.llm_security import check_response_security
            security = await check_response_security(
                user_message=state["message"],
                bot_response=state["final_response"],
                agent_result=state.get("agent_result"),
            )
            if security.should_block:
                blocked_response = (
                    "No puedo procesar esa respuesta en este momento. "
                    "Por favor, intenta reformular tu consulta."
                )
                return {
                    "final_response": blocked_response,
                    "block_reason": security.reason,
                    "injection_risk": security.injection_risk,
                }
            return {"block_reason": None, "injection_risk": security.injection_risk}
        except Exception as exc:
            logger.error("Guardrails node error", error=str(exc))
            return {"block_reason": None, "injection_risk": 0.0}

    # -----------------------------------------------------------------------
    # Public interface
    # -----------------------------------------------------------------------

    async def process(
        self,
        message: str,
        phone: str,
        tenant_id: str,
        history: list,
        **kwargs: Any,
    ) -> AgentResult:
        """Process a message through the LangGraph graph."""
        logger.info(
            "Router processing message",
            phone=phone,
            message=message[:50] + "..." if len(message) > 50 else message,
        )

        # Langfuse tracing — wraps the full turn as a root span
        if getattr(self, "_langfuse_enabled", False):
            try:
                from langfuse.decorators import langfuse_context
                langfuse_context.update_current_observation(
                    name="router_turn",
                    input={"message": message[:300], "tenant_id": tenant_id},
                    metadata={"phone_suffix": phone[-4:] if phone else "????"},
                )
            except Exception:
                pass

        graph = await self._get_compiled_graph()

        initial_state: AgentState = {
            "message": message,
            "phone": phone,
            "tenant_id": tenant_id,
            "history": history,
            "agent_result": None,
            "final_response": "",
            "block_reason": None,
            "injection_risk": 0.0,
        }

        config = {"configurable": {"thread_id": f"{tenant_id}_{phone}"}}

        try:
            final_state = await graph.ainvoke(initial_state, config=config)
        except Exception as exc:
            logger.error("Graph invocation failed", error=str(exc))
            return AgentResult(
                response="Hubo un problema procesando tu mensaje. Por favor, intenta de nuevo.",
                agent_used=self.name,
            )

        agent_result = final_state.get("agent_result")
        if agent_result:
            # Merge guardrail metadata into agent result
            merged_metadata = dict(agent_result.metadata or {})
            if final_state.get("block_reason"):
                merged_metadata["block_reason"] = final_state["block_reason"]
            if final_state.get("injection_risk", 0.0) > 0:
                merged_metadata["injection_risk"] = final_state["injection_risk"]

            return AgentResult(
                response=final_state["final_response"],
                agent_used=agent_result.agent_used,
                sub_agent_used=agent_result.sub_agent_used,
                tokens_in=agent_result.tokens_in,
                tokens_out=agent_result.tokens_out,
                metadata=merged_metadata or None,
                response_type=agent_result.response_type,
                risk_level=agent_result.risk_level,
                requires_orchestrator_final=agent_result.requires_orchestrator_final,
            )

        return AgentResult(
            response="No pude procesar tu mensaje.",
            agent_used=self.name,
        )

    # -----------------------------------------------------------------------
    # LLM orchestration logic (preserved from original RouterAgent)
    # -----------------------------------------------------------------------

    def _metadata_has_failed_tool(self, metadata: dict[str, Any] | None) -> bool:
        if not isinstance(metadata, dict):
            return False
        result = metadata.get("result")
        if isinstance(result, dict) and result.get("success") is False:
            return True
        return False

    def _is_sensitive_tool(self, metadata: dict[str, Any] | None) -> bool:
        if not isinstance(metadata, dict):
            return False
        tool_name = metadata.get("tool")
        return isinstance(tool_name, str) and tool_name in SENSITIVE_TOOL_NAMES

    def _contains_identity_leak_marker(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in IDENTITY_LEAK_MARKERS)

    def _should_finalize(self, results: list[AgentResult]) -> bool:
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
        prompt = await self.prompt_loader.get_prompt("router-finalizer", tenant_id)
        if prompt and prompt.strip():
            return prompt
        return (
            "Sos Aira. Recibs respuestas internas de sub-agentes y debes redactar una unica "
            "respuesta final para WhatsApp. Mantene tono breve y claro.\n"
            "REGLAS: no inventes hechos, no cambies montos/fechas/estados, no reveles "
            "sub-agentes o modulos internos."
        )

    def _build_finalizer_payload(
        self,
        message: str,
        results: list[AgentResult],
        combined_response: str,
    ) -> str:
        summarized_results: list[dict[str, Any]] = []
        for result in results:
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            tool_name = metadata.get("tool") if isinstance(metadata.get("tool"), str) else None
            tool_result = metadata.get("result") if isinstance(metadata.get("result"), dict) else {}
            summarized_results.append({
                "agent_used": result.agent_used,
                "response": result.response,
                "response_type": getattr(result, "response_type", None),
                "risk_level": getattr(result, "risk_level", None),
                "requires_orchestrator_final": getattr(result, "requires_orchestrator_final", False),
                "tool": tool_name,
                "tool_success": tool_result.get("success") if isinstance(tool_result, dict) else None,
            })
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
        prompt = await self._get_finalizer_prompt(tenant_id)
        payload = self._build_finalizer_payload(message, results, combined_response)
        finalizer_model = getattr(
            self.settings, "orchestrator_finalizer_model", self.settings.openai_router_model
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
                return combined_response, tokens_in, tokens_out, True
            return text, tokens_in, tokens_out, False
        except Exception as exc:
            logger.error("Finalizer failed, using passthrough fallback", error=str(exc))
            return combined_response, 0, 0, True

    async def _process_with_llm(
        self,
        message: str,
        phone: str,
        tenant_id: str,
        history: list,
    ) -> AgentResult:
        """LLM-only routing and sub-agent dispatch (core logic)."""
        prompt = await self.get_prompt(tenant_id)

        now = datetime.now()
        day_name = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"][now.weekday()]
        messages = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": f"Hoy es {day_name} {now.strftime('%Y-%m-%d %H:%M')}."},
        ]

        for msg in history[-6:]:
            messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": f"[USER_MSG]{message}[/USER_MSG]"})

        tools = [
            {"type": "function", "function": {"name": "finance_agent", "description": "Gestiona gastos, presupuestos y consultas financieras", "parameters": {"type": "object", "properties": {"user_request": {"type": "string", "description": "El pedido del usuario relacionado con finanzas"}}, "required": ["user_request"]}}},
            {"type": "function", "function": {"name": "agenda_agent", "description": "Gestiona eventos, citas, agenda y recordatorios", "parameters": {"type": "object", "properties": {"user_request": {"type": "string", "description": "El pedido del usuario relacionado con calendario, agenda o recordatorios"}}, "required": ["user_request"]}}},
            {"type": "function", "function": {"name": "shopping_agent", "description": "Gestiona listas de compras", "parameters": {"type": "object", "properties": {"user_request": {"type": "string", "description": "El pedido del usuario relacionado con listas de compras"}}, "required": ["user_request"]}}},
            {"type": "function", "function": {"name": "vehicle_agent", "description": "Gestiona vehiculos y mantenimiento", "parameters": {"type": "object", "properties": {"user_request": {"type": "string", "description": "El pedido del usuario relacionado con vehiculos"}}, "required": ["user_request"]}}},
            {"type": "function", "function": {"name": "subscription_agent", "description": "Gestiona plan, suscripcion, upgrade, downgrade, cancelar, uso, invitar miembros al hogar", "parameters": {"type": "object", "properties": {"user_request": {"type": "string", "description": "El pedido del usuario relacionado con suscripcion o miembros"}}, "required": ["user_request"]}}},
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
                    except Exception as exc:
                        logger.error(f"Sub-agent {agent_name} failed", error=str(exc))

                if results:
                    combined_response = "\n\n".join(r.response for r in results)
                    merged_metadata: dict = {}
                    for r in results:
                        if r.metadata:
                            merged_metadata.update(r.metadata)

                    finalizer_attempted = False
                    finalizer_fallback_used = False
                    response_mode = "passthrough"
                    if self._should_finalize(results):
                        finalizer_attempted = True
                        (combined_response, fin_in, fin_out, finalizer_fallback_used) = await self._finalize_response(
                            message=message,
                            tenant_id=tenant_id,
                            results=results,
                            combined_response=combined_response,
                        )
                        total_tokens_in += fin_in
                        total_tokens_out += fin_out
                        response_mode = "passthrough" if finalizer_fallback_used else "orchestrator_finalized"

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

            response_text = choice.message.content or "No pude procesar tu mensaje."
            return AgentResult(
                response=response_text,
                agent_used=self.name,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

        except CircuitBreakerOpenError:
            raise
        except Exception as exc:
            logger.error("LLM processing failed", error=str(exc))
            raise
