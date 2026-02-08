"""Router Agent - Main orchestrator."""

from typing import Any, Optional

import structlog
from openai import AsyncOpenAI

from ..config import get_settings
from .base import AgentResult, BaseAgent

logger = structlog.get_logger()


# Agent routing configuration
AGENT_KEYWORDS = {
    "finance": [
        "gast", "pagu", "plata", "dinero", "presupuesto", "cuánto", "cuanto",
        "registr", "supermercado", "super", "nafta", "luz", "gas", "servicio",
        "transporte", "entretenimiento", "restaurante", "café", "salud",
        "farmacia", "medic", "hospital", "sueldo", "cobr", "$", "pesos",
        "mensual", "semanal", "hoy gasté", "ayer gasté", "borrá", "eliminá",
        "modific", "cambiá el gasto", "reporte", "resumen",
    ],
    "calendar": [
        "reunión", "reunion", "turno", "cita", "evento", "agenda", "agendar",
        "agendá", "agendame", "calendario", "programado", "mañana a las",
        "hoy a las", "el lunes", "el martes", "el miércoles", "el jueves",
        "el viernes", "el sábado", "el domingo", "próxima semana",
        "cancelá", "cancelar", "mover", "cambiar fecha", "qué tengo",
        "disponibilidad", "libre", "ocupado", "google calendar",
    ],
    "reminder": [
        "recordame", "recordá", "acordate", "avisame", "no me olvide",
        "recordatorio", "alarma", "alerta", "pendiente", "avisar",
        "notificame", "notificación",
    ],
    "shopping": [
        "lista", "compras", "supermercado", "comprar", "agregar a la lista",
        "agregá", "poneme", "necesito comprar", "falta", "ya compré",
        "marcar comprado", "lista del super", "items", "productos",
    ],
    "vehicle": [
        "auto", "coche", "vehículo", "vehiculo", "service", "aceite",
        "vtv", "seguro del auto", "patente", "kilometraje", "km",
        "mantenimiento", "taller", "mecánico", "mecanico", "cubiertas",
        "neumáticos", "neumaticos", "frenos", "batería", "bateria",
        "correa", "filtro", "bujías", "bujias",
    ],
}


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

    def _detect_agent(self, message: str) -> Optional[str]:
        """Detect which agent should handle the message based on keywords.

        Args:
            message: The user's message.

        Returns:
            Agent name or None if unclear.
        """
        message_lower = message.lower()

        scores = {}
        for agent_name, keywords in AGENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in message_lower)
            if score > 0:
                scores[agent_name] = score

        if not scores:
            return None

        # Return agent with highest score
        return max(scores, key=scores.get)

    async def process(
        self,
        message: str,
        phone: str,
        tenant_id: str,
        history: list,
        **kwargs,
    ) -> AgentResult:
        """Process a message by routing to the appropriate sub-agent.

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

        # First, try keyword-based detection
        detected_agent = self._detect_agent(message)

        if detected_agent:
            logger.info(f"Keyword detection: routing to {detected_agent}")
            sub_agent = self._get_sub_agent(detected_agent)
            if sub_agent:
                try:
                    result = await sub_agent.process(
                        message=message,
                        phone=phone,
                        tenant_id=tenant_id,
                        history=history,
                    )
                    result.agent_used = self.name
                    result.sub_agent_used = detected_agent
                    return result
                except Exception as e:
                    logger.error(f"Sub-agent {detected_agent} failed", error=str(e))

        # If no clear agent detected or sub-agent failed, use LLM routing
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

            # Check if LLM wants to use a tool
            if choice.message.tool_calls:
                tool_call = choice.message.tool_calls[0]
                agent_name = tool_call.function.name.replace("_agent", "")

                logger.info(f"LLM routing to {agent_name}")

                sub_agent = self._get_sub_agent(agent_name)
                if sub_agent:
                    try:
                        import json
                        args = json.loads(tool_call.function.arguments)
                        user_request = args.get("user_request", message)

                        result = await sub_agent.process(
                            message=user_request,
                            phone=phone,
                            tenant_id=tenant_id,
                            history=history,
                        )
                        result.agent_used = self.name
                        result.sub_agent_used = agent_name
                        result.tokens_in = (result.tokens_in or 0) + (tokens_in or 0)
                        result.tokens_out = (result.tokens_out or 0) + (tokens_out or 0)
                        return result
                    except Exception as e:
                        logger.error(f"Sub-agent {agent_name} failed", error=str(e))

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
