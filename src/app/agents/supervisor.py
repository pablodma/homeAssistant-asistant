"""Supervisor Agent — central orchestrator using Anthropic claude-haiku.

Architecture:
  START -> [orchestrate_node] -> [guardrails_node] -> END

  orchestrate_node: Anthropic LLM with 5 domain tools, receives structured
                    ToolOutput from sub-agents, formulates ALL user responses.
  guardrails_node:  3-layer response security pipeline.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional
from typing_extensions import TypedDict

import structlog
from anthropic import AsyncAnthropic
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .base import AgentResult, BaseAgent, ToolOutput, _LANGFUSE_ENABLED
from ..config import get_settings
from ..services.llm_breaker import CircuitBreakerOpenError, get_circuit_breaker

logger = structlog.get_logger()

_CIRCUIT_OPEN_RESPONSE = (
    "Estoy teniendo dificultades tecnicas en este momento. "
    "Por favor, intenta de nuevo en unos minutos."
)


class SupervisorState(TypedDict):
    """Typed state for the supervisor graph."""

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


class SupervisorAgent(BaseAgent):
    """Supervisor agent that orchestrates sub-agents and formulates all responses.

    Uses Anthropic claude-haiku as the central LLM. Sub-agents return structured
    ToolOutput data, and the supervisor formulates ALL user-facing responses
    in Aira's voice.
    """

    name = "supervisor"

    def __init__(self) -> None:
        super().__init__()
        self.client = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
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
        graph = StateGraph(SupervisorState)

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
                logger.info("Supervisor using PostgreSQL checkpointing")
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

    async def _orchestrate_node(self, state: SupervisorState) -> dict:
        """Route the message through the Anthropic supervisor LLM."""
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
            logger.error("Supervisor orchestration failed", error=str(exc))
            fallback = AgentResult(
                response="Hubo un problema procesando tu mensaje. Por favor, intenta de nuevo.",
                agent_used=self.name,
            )
            return {"agent_result": fallback, "final_response": fallback.response}

    async def _guardrails_node(self, state: SupervisorState) -> dict:
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
            "Supervisor processing message",
            phone=phone,
            message=message[:50] + "..." if len(message) > 50 else message,
        )

        graph = await self._get_compiled_graph()

        # Convert Message objects to plain dicts so the checkpointer
        # can serialize the state (msgpack cannot handle custom classes).
        serializable_history = [
            msg.to_dict() if hasattr(msg, "to_dict") else {"role": msg.role, "content": msg.content}
            for msg in history
        ]

        initial_state: SupervisorState = {
            "message": message,
            "phone": phone,
            "tenant_id": tenant_id,
            "history": serializable_history,
            "agent_result": None,
            "final_response": "",
            "block_reason": None,
            "injection_risk": 0.0,
        }

        config = {"configurable": {"thread_id": f"{tenant_id}_{phone}"}}

        try:
            final_state = await graph.ainvoke(initial_state, config=config)
        except Exception as exc:
            logger.error("Supervisor graph invocation failed", error=str(exc))
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
                tool_output=agent_result.tool_output,
            )

        return AgentResult(
            response="No pude procesar tu mensaje.",
            agent_used=self.name,
        )

    # -----------------------------------------------------------------------
    # Anthropic tool definitions
    # -----------------------------------------------------------------------

    def _build_domain_tools(self) -> list[dict[str, Any]]:
        """Build Anthropic tool definitions for the 5 domain agents."""
        return [
            {
                "name": "finance_agent",
                "description": (
                    "Gestiona gastos, presupuestos, ingresos, reportes financieros "
                    "y balance. Retorna datos estructurados."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_request": {
                            "type": "string",
                            "description": "El pedido del usuario relacionado con finanzas",
                        },
                    },
                    "required": ["user_request"],
                },
            },
            {
                "name": "agenda_agent",
                "description": (
                    "Gestiona eventos, citas, turnos, agenda y recordatorios. "
                    "Retorna datos estructurados."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_request": {
                            "type": "string",
                            "description": (
                                "El pedido del usuario relacionado con calendario, "
                                "agenda o recordatorios"
                            ),
                        },
                    },
                    "required": ["user_request"],
                },
            },
            {
                "name": "shopping_agent",
                "description": (
                    "Gestiona listas de compras (sin precios). "
                    "Retorna datos estructurados."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_request": {
                            "type": "string",
                            "description": "El pedido del usuario relacionado con listas de compras",
                        },
                    },
                    "required": ["user_request"],
                },
            },
            {
                "name": "vehicle_agent",
                "description": (
                    "Gestiona vehiculos, mantenimiento, services y vencimientos. "
                    "Retorna datos estructurados."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_request": {
                            "type": "string",
                            "description": "El pedido del usuario relacionado con vehiculos",
                        },
                    },
                    "required": ["user_request"],
                },
            },
            {
                "name": "subscription_agent",
                "description": (
                    "Gestiona plan, suscripcion, upgrade, downgrade, cancelar, "
                    "uso, invitar miembros al hogar. Retorna datos estructurados."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_request": {
                            "type": "string",
                            "description": (
                                "El pedido del usuario relacionado con suscripcion "
                                "o miembros"
                            ),
                        },
                    },
                    "required": ["user_request"],
                },
            },
        ]

    # -----------------------------------------------------------------------
    # ToolOutput serialization
    # -----------------------------------------------------------------------

    def _tool_output_to_json(self, tool_output: Optional[ToolOutput]) -> str:
        """Serialize ToolOutput for the supervisor LLM to reason about."""
        if tool_output is None:
            return json.dumps({"success": True, "note": "No structured output available"})

        payload: dict[str, Any] = {
            "success": tool_output.success,
            "domain": tool_output.domain,
            "tool_name": tool_output.tool_name,
            "data": tool_output.data,
        }
        if tool_output.quick_actions:
            payload["quick_actions"] = tool_output.quick_actions
        if tool_output.error:
            payload["error"] = tool_output.error
        return json.dumps(payload, ensure_ascii=False, default=str)

    # -----------------------------------------------------------------------
    # Core LLM orchestration (multi-round tool use)
    # -----------------------------------------------------------------------

    async def _process_with_llm(
        self,
        message: str,
        phone: str,
        tenant_id: str,
        history: list,
    ) -> AgentResult:
        """Supervisor LLM: route to sub-agents, collect ToolOutput, formulate response.

        The supervisor runs in a loop (up to max_rounds) to support multi-step
        tool use. Each iteration either returns a final text response or calls
        a domain tool, feeds the structured result back, and continues.
        """
        prompt = await self.get_prompt(tenant_id)

        now = datetime.now()
        day_name = [
            "Lunes", "Martes", "Miercoles", "Jueves",
            "Viernes", "Sabado", "Domingo",
        ][now.weekday()]
        date_context = f"\n\n[FECHA_ACTUAL] Hoy es {day_name} {now.strftime('%Y-%m-%d %H:%M')}."

        system_text = prompt + date_context

        # Build conversation messages from history
        filtered_msgs: list[dict[str, Any]] = []
        for msg in history[-6:]:
            role = msg["role"] if isinstance(msg, dict) else msg.role
            content = msg["content"] if isinstance(msg, dict) else msg.content
            filtered_msgs.append({"role": role, "content": content})
        filtered_msgs.append({"role": "user", "content": f"[USER_MSG]{message}[/USER_MSG]"})

        domain_tools = self._build_domain_tools()

        total_tokens_in = 0
        total_tokens_out = 0
        agents_used: list[str] = []
        collected_quick_actions: Optional[dict[str, Any]] = None
        last_tool_output: Optional[ToolOutput] = None
        max_rounds = 5

        for _ in range(max_rounds):
            response = await self.client.messages.create(
                model=self.settings.anthropic_subagent_model,
                system=system_text,
                messages=filtered_msgs,
                tools=domain_tools,
                tool_choice={"type": "auto"},
                max_tokens=1500,
            )

            t_in, t_out = self._anthropic_tokens(response)
            total_tokens_in += t_in
            total_tokens_out += t_out

            # If supervisor returned text (no tool call) -> final response
            if response.stop_reason != "tool_use":
                final_text = self._extract_text(response) or "No pude procesar tu solicitud."
                merged_metadata: dict[str, Any] = {"response_mode": "supervisor"}
                if collected_quick_actions:
                    merged_metadata["quick_actions"] = collected_quick_actions
                return AgentResult(
                    response=final_text,
                    agent_used=self.name,
                    sub_agent_used=", ".join(agents_used) if agents_used else None,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    metadata=merged_metadata,
                    tool_output=last_tool_output,
                )

            # Extract ALL tool_use blocks (Anthropic may return multiple for
            # cross-domain, e.g. "gasté 3000 y recordame pagar la luz").
            tool_uses = [
                (block.name, block.input, block.id)
                for block in response.content
                if block.type == "tool_use"
            ]

            if not tool_uses:
                final_text = self._extract_text(response) or "No pude procesar tu solicitud."
                return AgentResult(
                    response=final_text,
                    agent_used=self.name,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                )

            # Append assistant message once (contains all tool_use blocks)
            filtered_msgs.append({"role": "assistant", "content": response.content})

            # Process each tool call and collect all results
            tool_results_content: list[dict[str, Any]] = []

            for tool_name, tool_args, tool_use_id in tool_uses:
                agent_name = tool_name.replace("_agent", "")

                logger.info(
                    "Supervisor routing to sub-agent",
                    agent=agent_name,
                    request=str(tool_args.get("user_request", ""))[:80],
                )

                sub_agent = self._get_sub_agent(agent_name)
                if not sub_agent:
                    logger.warning("Unknown sub-agent requested by supervisor", agent=agent_name)
                    tool_results_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": json.dumps({"success": False, "error": f"Agente desconocido: {agent_name}"}),
                    })
                    continue

                try:
                    user_request = tool_args.get("user_request", message)
                    if not isinstance(user_request, str) or not user_request.strip():
                        user_request = message

                    sub_result = await sub_agent.process(
                        message=user_request,
                        phone=phone,
                        tenant_id=tenant_id,
                        history=history,
                    )

                    agents_used.append(agent_name)
                    total_tokens_in += sub_result.tokens_in or 0
                    total_tokens_out += sub_result.tokens_out or 0

                    # Extract ToolOutput if available, else build from metadata
                    tool_output = sub_result.tool_output
                    if tool_output is None and sub_result.metadata:
                        meta = sub_result.metadata
                        tool_output = ToolOutput(
                            success=True,
                            domain=agent_name,
                            tool_name=meta.get("tool", "unknown"),
                            tool_args=meta.get("tool_args", {}),
                            data=(
                                meta.get("result", {}).get("data", {})
                                if isinstance(meta.get("result"), dict)
                                else {}
                            ),
                            formatted_text=sub_result.response,
                        )

                    last_tool_output = tool_output

                    # Collect quick actions from sub-agent
                    if sub_result.metadata and isinstance(sub_result.metadata, dict):
                        qa = sub_result.metadata.get("quick_actions")
                        if qa:
                            collected_quick_actions = qa

                    # Build tool result for supervisor
                    if tool_output:
                        result_json = self._tool_output_to_json(tool_output)
                    else:
                        result_json = json.dumps(
                            {"success": True, "domain": agent_name, "response_text": sub_result.response},
                            ensure_ascii=False,
                        )

                except Exception as exc:
                    logger.error("Sub-agent execution failed", agent=agent_name, error=str(exc))
                    result_json = json.dumps({"success": False, "domain": agent_name, "error": str(exc)})

                tool_results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_json,
                })

            # Append ALL tool results in a single user message (Anthropic requirement)
            filtered_msgs.append({"role": "user", "content": tool_results_content})

        # Max rounds reached without a final text response
        return AgentResult(
            response="No pude completar la operacion. Intenta de nuevo.",
            agent_used=self.name,
            sub_agent_used=", ".join(agents_used) if agents_used else None,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            metadata={"response_mode": "supervisor", "max_rounds_reached": True},
            tool_output=last_tool_output,
        )
