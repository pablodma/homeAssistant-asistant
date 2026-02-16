"""Router Agent - Main orchestrator."""

from typing import Optional

import structlog
from openai import AsyncOpenAI

from .base import AgentResult, BaseAgent

logger = structlog.get_logger()


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
            elif agent_name == "calendar":
                from .calendar import CalendarAgent
                self._sub_agents[agent_name] = CalendarAgent()
            elif agent_name == "reminder":
                from .reminder import ReminderAgent
                self._sub_agents[agent_name] = ReminderAgent()
            elif agent_name == "shopping":
                from .shopping import ShoppingAgent
                self._sub_agents[agent_name] = ShoppingAgent()
            elif agent_name == "vehicle":
                from .vehicle import VehicleAgent
                self._sub_agents[agent_name] = VehicleAgent()

        return self._sub_agents.get(agent_name)

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
        messages = [
            {"role": "system", "content": prompt},
        ]

        # Add history
        for msg in history[-6:]:  # Last 6 messages
            messages.append({"role": msg.role, "content": msg.content})

        # Add current message
        messages.append({"role": "user", "content": message})

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
                    "name": "calendar_agent",
                    "description": "Gestiona eventos, citas y agenda",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user_request": {
                                "type": "string",
                                "description": "El pedido del usuario relacionado con calendario",
                            }
                        },
                        "required": ["user_request"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "reminder_agent",
                    "description": "Crea y gestiona recordatorios",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user_request": {
                                "type": "string",
                                "description": "El pedido del usuario relacionado con recordatorios",
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
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=1000,
                temperature=0.7,
            )

            choice = response.choices[0]
            tokens_in = response.usage.prompt_tokens if response.usage else None
            tokens_out = response.usage.completion_tokens if response.usage else None

            # Check if LLM wants to use tools
            if choice.message.tool_calls:
                import json

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
                        user_request = args.get("user_request", message)

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
