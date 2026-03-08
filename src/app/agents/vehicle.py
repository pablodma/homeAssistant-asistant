"""Vehicle Agent - Vehicle and maintenance management."""

import json
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog

from ..config import get_settings
from .base import (
    FIRST_TIME_TOOL_DEFINITION,
    FIRST_TIME_TOOL_DEFINITION_ANTHROPIC,
    AgentResult,
    BaseAgent,
    ToolOutput,
    openai_tool_to_anthropic,
)
from .tools.vehicle_executor import execute_vehicle_tool, format_vehicle_response
from .tools.registry import VEHICLE_TOOLS

logger = structlog.get_logger()


class VehicleAgent(BaseAgent):
    """Agent for managing vehicles and maintenance."""

    name = "vehicle"

    def __init__(self):
        """Initialize the vehicle agent."""
        super().__init__()
        self._init_llm_client("vehicle_model_provider")

    async def process(
        self,
        message: str,
        phone: str,
        tenant_id: str,
        history: list,
        **kwargs,
    ) -> AgentResult:
        """Process a vehicle-related message.

        Args:
            message: The user's message.
            phone: The user's phone number.
            tenant_id: The tenant ID.
            history: Conversation history.

        Returns:
            The agent's response.
        """
        logger.info("Vehicle agent processing", message=message[:50])

        prompt = await self.get_prompt(tenant_id)

        # Build messages
        messages = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": f"Hoy es {['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo'][datetime.now().weekday()]} {datetime.now().strftime('%Y-%m-%d')}."},
        ]

        # Add history context
        for msg in history[-6:]:
            role = msg["role"] if isinstance(msg, dict) else msg.role
            content = msg["content"] if isinstance(msg, dict) else msg.content
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": f"[USER_MSG]{message}[/USER_MSG]"})

        # Check first-time use
        is_first_time = await self.check_first_time(phone)
        if is_first_time:
            messages.insert(1, {
                "role": "system",
                "content": (
                    "[PRIMERA_VEZ] Este es el primer uso del usuario con este módulo. "
                    "Seguí las instrucciones de la sección 'Primera Vez' del prompt."
                ),
            })

        # Define tools
        tools = list(VEHICLE_TOOLS)

        try:
            if self.provider == "anthropic":
                # --- Anthropic path ---
                anthropic_tools = [openai_tool_to_anthropic(t) for t in tools]
                if is_first_time:
                    anthropic_tools.append(FIRST_TIME_TOOL_DEFINITION_ANTHROPIC)

                system_text, filtered_msgs = self._extract_system_and_messages(messages)

                response = await self.client.messages.create(
                    model=self.settings.anthropic_subagent_model,
                    system=system_text,
                    messages=filtered_msgs,
                    tools=anthropic_tools,
                    tool_choice={"type": "auto"},
                    max_tokens=1000,
                )

                tokens_in, tokens_out = self._anthropic_tokens(response)

                tool_info = self._extract_tool_use(response)
                if tool_info:
                    tool_name, tool_args, tool_use_id = tool_info

                    logger.info(f"Vehicle tool call: {tool_name}", args=tool_args)

                    # First-time completion: execute and let LLM generate follow-up
                    if tool_name == "completar_configuracion_inicial":
                        result_msg = await self.complete_first_time(phone)
                        filtered_msgs.append({"role": "assistant", "content": response.content})
                        filtered_msgs.append(self._build_tool_result_msg(tool_use_id, result_msg))
                        follow_up = await self.client.messages.create(
                            model=self.settings.anthropic_subagent_model,
                            system=system_text,
                            messages=filtered_msgs,
                            max_tokens=500,
                        )
                        return AgentResult(
                            response=self._extract_text(follow_up) or "¡Configuración completada!",
                            agent_used=self.name,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                        )

                    # Execute the tool
                    tool_result = await execute_vehicle_tool(tool_name, tool_args, tenant_id, phone)

                    # Generate response based on tool result
                    response_text = format_vehicle_response(tool_name, tool_args, tool_result)

                    tool_output = ToolOutput(
                        success=tool_result.get("success", False),
                        domain="vehicle",
                        tool_name=tool_name,
                        tool_args=tool_args,
                        data=tool_result.get("data", {}),
                        formatted_text=response_text,
                    )

                    return AgentResult(
                        response=response_text,
                        agent_used=self.name,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        metadata={"tool": tool_name, "result": tool_result},
                        tool_output=tool_output,
                    )

                # Direct response (for tips/questions)
                return AgentResult(
                    response=self._extract_text(response) or "No pude procesar tu solicitud.",
                    agent_used=self.name,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )

            else:
                # --- OpenAI path (rollback) ---
                if is_first_time:
                    tools.append(FIRST_TIME_TOOL_DEFINITION)

                response = await self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.4,
                    max_completion_tokens=1000,
                )

                choice = response.choices[0]
                tokens_in = response.usage.prompt_tokens if response.usage else None
                tokens_out = response.usage.completion_tokens if response.usage else None

                # Check if tool was called
                if choice.message.tool_calls:
                    tool_call = choice.message.tool_calls[0]
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)

                    logger.info(f"Vehicle tool call: {tool_name}", args=tool_args)

                    # First-time completion: execute and let LLM generate follow-up
                    if tool_name == "completar_configuracion_inicial":
                        result_msg = await self.complete_first_time(phone)
                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{"id": tool_call.id, "type": "function", "function": {"name": tool_name, "arguments": tool_call.function.arguments}}],
                        })
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_msg})
                        follow_up = await self.client.chat.completions.create(
                            model=self.settings.openai_model,
                            messages=messages,
                            temperature=0.4,
                            max_completion_tokens=500,
                        )
                        return AgentResult(
                            response=follow_up.choices[0].message.content or "¡Configuración completada!",
                            agent_used=self.name,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                        )

                    # Execute the tool
                    tool_result = await execute_vehicle_tool(tool_name, tool_args, tenant_id, phone)

                    # Generate response based on tool result
                    response_text = format_vehicle_response(tool_name, tool_args, tool_result)

                    tool_output = ToolOutput(
                        success=tool_result.get("success", False),
                        domain="vehicle",
                        tool_name=tool_name,
                        tool_args=tool_args,
                        data=tool_result.get("data", {}),
                        formatted_text=response_text,
                    )

                    return AgentResult(
                        response=response_text,
                        agent_used=self.name,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        metadata={"tool": tool_name, "result": tool_result},
                        tool_output=tool_output,
                    )

                # Direct response (for tips/questions)
                return AgentResult(
                    response=choice.message.content or "No pude procesar tu solicitud.",
                    agent_used=self.name,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )

        except Exception as e:
            logger.error("Vehicle agent error", error=str(e))
            return AgentResult(
                response="Hubo un problema procesando tu solicitud. Intentá de nuevo.",
                agent_used=self.name,
            )
