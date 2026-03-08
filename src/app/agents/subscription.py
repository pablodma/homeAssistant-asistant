"""Subscription Agent - Onboarding and subscription management.

This agent handles two scenarios:
1. Unregistered users: product pitch, plan info, checkout, registration
2. Registered users: subscription management (upgrade, downgrade, cancel, usage)

All decision logic is in the prompt (docs/prompts/subscription-agent.md).
"""

import json
from typing import Any

import structlog
from ..services.quality_logger import get_quality_logger
from .base import AgentResult, BaseAgent, ToolOutput, openai_tool_to_anthropic
from .tools.subscription_executor import execute_subscription_tool
from .tools.registry import (
    SUBSCRIPTION_ACQUISITION_TOOLS,
    SUBSCRIPTION_SETUP_TOOLS,
    SUBSCRIPTION_MANAGEMENT_TOOLS,
)

logger = structlog.get_logger()


class SubscriptionAgent(BaseAgent):
    """Agent for onboarding and subscription management.

    Operates in three modes:
    - Acquisition: for unregistered users (no tenant_id) — pitch, plans, checkout
    - Setup: for registered users with onboarding_completed=false — home config post-payment
    - Management: for registered users with onboarding complete — status, upgrade, cancel
    """

    name = "subscription"

    def __init__(self) -> None:
        """Initialize the subscription agent."""
        super().__init__()
        self._init_llm_client("subscription_model_provider")

    async def process(
        self,
        message: str,
        phone: str,
        tenant_id: str,
        history: list,
        **kwargs,
    ) -> AgentResult:
        """Process a subscription-related message.

        Args:
            message: The user's message.
            phone: The user's phone number.
            tenant_id: The tenant ID (empty string for unregistered users).
            history: Conversation history.
            mode: Override mode — "acquisition", "setup", or "management".
            contact_name: WhatsApp profile name if available.

        Returns:
            The agent's response.
        """
        explicit_mode = kwargs.get("mode")
        contact_name = kwargs.get("contact_name")

        if explicit_mode:
            mode = explicit_mode
        elif not tenant_id:
            mode = "acquisition"
        else:
            mode = "management"

        logger.info(
            "Subscription agent processing",
            mode=mode,
            phone=phone,
            contact_name=contact_name,
            message=message[:50],
        )
        quality_logger = get_quality_logger()

        # Use a dummy tenant_id for prompt loading (prompts are not tenant-specific)
        prompt_tenant_id = tenant_id or self.settings.default_tenant_id
        prompt = await self.get_prompt(prompt_tenant_id)

        # Add mode context to system prompt
        mode_labels = {
            "acquisition": "Adquisición (usuario nuevo)",
            "setup": "Setup (post-pago, configurar hogar)",
            "management": "Gestión (usuario registrado)",
        }
        mode_context = (
            f"\n\n## Contexto actual\n"
            f"- Modo: {mode_labels.get(mode, mode)}\n"
            f"- Teléfono del usuario: {phone}\n"
        )
        if mode == "setup":
            mode_context += "- Estado del pago: CONFIRMADO por el sistema (la cuenta ya fue creada tras verificar el pago)\n"
        if contact_name:
            mode_context += f"- Nombre de perfil WhatsApp: {contact_name}\n"
        if tenant_id:
            mode_context += f"- Tenant ID: {tenant_id}\n"

        full_prompt = prompt + mode_context

        # Select tools based on mode
        tools_map = {
            "acquisition": SUBSCRIPTION_ACQUISITION_TOOLS,
            "setup": SUBSCRIPTION_SETUP_TOOLS,
            "management": SUBSCRIPTION_MANAGEMENT_TOOLS,
        }
        tools = tools_map.get(mode, SUBSCRIPTION_MANAGEMENT_TOOLS)

        # Build messages
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": full_prompt},
        ]

        for msg in history[-6:]:
            role = msg["role"] if isinstance(msg, dict) else msg.role
            content = msg["content"] if isinstance(msg, dict) else msg.content
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": f"[USER_MSG]{message}[/USER_MSG]"})

        try:
            total_tokens_in = 0
            total_tokens_out = 0
            max_tool_rounds = 5

            if self.provider == "anthropic":
                anthropic_tools = [openai_tool_to_anthropic(t) for t in tools]
                system_text, filtered_msgs = self._extract_system_and_messages(messages)

                for _ in range(max_tool_rounds):
                    response = await self.client.messages.create(
                        model=self.settings.anthropic_subagent_model,
                        system=system_text,
                        messages=filtered_msgs,
                        tools=anthropic_tools,
                        tool_choice={"type": "auto"},
                        max_tokens=1500,
                    )

                    t_in, t_out = self._anthropic_tokens(response)
                    total_tokens_in += t_in
                    total_tokens_out += t_out

                    if response.stop_reason != "tool_use":
                        return AgentResult(
                            response=self._extract_text(response) or "No pude procesar tu solicitud.",
                            agent_used=self.name,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                        )

                    # Process tool call
                    tool_info = self._extract_tool_use(response)
                    if not tool_info:
                        return AgentResult(
                            response=self._extract_text(response) or "No pude procesar tu solicitud.",
                            agent_used=self.name,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                        )

                    tool_name, tool_args, tool_use_id = tool_info

                    logger.info(
                        f"Subscription tool call: {tool_name}",
                        args=tool_args,
                        mode=mode,
                    )

                    tool_result = await execute_subscription_tool(
                        tool_name=tool_name,
                        args=tool_args,
                        phone=phone,
                        tenant_id=tenant_id,
                    )

                    # Append assistant message with raw content blocks
                    filtered_msgs.append({"role": "assistant", "content": response.content})

                    # Append tool result
                    filtered_msgs.append(
                        self._build_tool_result_msg(tool_use_id, json.dumps(tool_result, ensure_ascii=False))
                    )

                    # Always continue the loop so LLM can format the response

            else:
                # OpenAI provider (rollback path)
                for _ in range(max_tool_rounds):
                    response = await self.client.chat.completions.create(
                        model=self.settings.openai_model,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                        temperature=0.4,
                        max_completion_tokens=1500,
                    )

                    choice = response.choices[0]
                    if response.usage:
                        total_tokens_in += response.usage.prompt_tokens
                        total_tokens_out += response.usage.completion_tokens

                    if not choice.message.tool_calls:
                        return AgentResult(
                            response=choice.message.content or "No pude procesar tu solicitud.",
                            agent_used=self.name,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                        )

                    # Process tool calls
                    tool_call = choice.message.tool_calls[0]
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)

                    logger.info(
                        f"Subscription tool call: {tool_name}",
                        args=tool_args,
                        mode=mode,
                    )

                    tool_result = await execute_subscription_tool(
                        tool_name=tool_name,
                        args=tool_args,
                        phone=phone,
                        tenant_id=tenant_id,
                    )

                    # Append assistant message with tool call
                    messages.append(
                        {
                            "role": "assistant",
                            "content": choice.message.content or None,
                            "tool_calls": [
                                {
                                    "id": tool_call.id,
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": tool_call.function.arguments,
                                    },
                                }
                            ],
                        }
                    )

                    # Append tool result
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        }
                    )

                    # Always continue the loop so LLM can format the response

            # Max rounds reached
            return AgentResult(
                response="No pude completar la operación. Intentá de nuevo.",
                agent_used=self.name,
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
            )

        except Exception as e:
            logger.error("Subscription agent error", error=str(e))
            if tenant_id:
                await quality_logger.log_hard_error(
                    tenant_id=tenant_id,
                    category="llm_error",
                    error_message=str(e),
                    agent_name=self.name,
                    user_phone=phone,
                    message_in=message,
                    severity="high",
                    exception=e,
                )
            return AgentResult(
                response="Hubo un problema procesando tu solicitud. Intentá de nuevo.",
                agent_used=self.name,
            )

