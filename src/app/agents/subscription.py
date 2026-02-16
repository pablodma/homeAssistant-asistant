"""Subscription Agent - Onboarding and subscription management.

This agent handles two scenarios:
1. Unregistered users: product pitch, plan info, checkout, registration
2. Registered users: subscription management (upgrade, downgrade, cancel, usage)

All decision logic is in the prompt (docs/prompts/subscription-agent.md).
"""

import json
from typing import Any, Optional

import httpx
import structlog
from openai import AsyncOpenAI

from ..config import get_settings
from ..services.quality_logger import get_quality_logger
from .base import AgentResult, BaseAgent

logger = structlog.get_logger()


# Tool definitions for unregistered users (acquisition mode)
ACQUISITION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_plans",
            "description": "Obtiene todos los planes disponibles con precios, límites y funcionalidades",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_starter",
            "description": "Registra un usuario nuevo con plan Starter (gratuito). Crea tenant + usuario al instante.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Teléfono del usuario en formato E.164",
                    },
                    "display_name": {
                        "type": "string",
                        "description": "Nombre del usuario",
                    },
                    "home_name": {
                        "type": "string",
                        "description": "Nombre del hogar",
                    },
                },
                "required": ["phone", "display_name", "home_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_checkout",
            "description": "Genera un link de pago de Lemon Squeezy para un plan pago (family o premium)",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Teléfono del usuario",
                    },
                    "display_name": {
                        "type": "string",
                        "description": "Nombre del usuario",
                    },
                    "home_name": {
                        "type": "string",
                        "description": "Nombre del hogar",
                    },
                    "plan_type": {
                        "type": "string",
                        "enum": ["family", "premium"],
                        "description": "Tipo de plan",
                    },
                    "coupon_code": {
                        "type": "string",
                        "description": "Código de cupón (opcional)",
                    },
                },
                "required": ["phone", "display_name", "home_name", "plan_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_coupon",
            "description": "Valida un cupón de descuento",
            "parameters": {
                "type": "object",
                "properties": {
                    "coupon_code": {
                        "type": "string",
                        "description": "Código del cupón",
                    },
                    "plan_type": {
                        "type": "string",
                        "enum": ["family", "premium"],
                        "description": "Plan al que se aplicaría",
                    },
                },
                "required": ["coupon_code", "plan_type"],
            },
        },
    },
]

# Tool definitions for registered users (management mode)
MANAGEMENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_plans",
            "description": "Obtiene todos los planes disponibles con precios, límites y funcionalidades",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_subscription_status",
            "description": "Consulta el plan actual, estado de suscripción y fecha de renovación",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_usage",
            "description": "Consulta mensajes usados/restantes este mes y cantidad de miembros",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_upgrade_checkout",
            "description": "Genera link de pago para upgrade de plan o reactivación",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_type": {
                        "type": "string",
                        "enum": ["family", "premium"],
                        "description": "Plan destino",
                    },
                },
                "required": ["plan_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_subscription",
            "description": "Cancela la suscripción. Solo usar después de confirmación explícita del usuario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Motivo de cancelación",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Debe ser true para ejecutar",
                    },
                },
                "required": ["reason", "confirmed"],
            },
        },
    },
]


class SubscriptionAgent(BaseAgent):
    """Agent for onboarding and subscription management.

    Operates in two modes:
    - Acquisition: for unregistered users (no tenant_id)
    - Management: for registered users (has tenant_id)
    """

    name = "subscription"

    def __init__(self) -> None:
        """Initialize the subscription agent."""
        super().__init__()
        self.client = AsyncOpenAI(api_key=self.settings.openai_api_key)

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

        Returns:
            The agent's response.
        """
        is_registered = bool(tenant_id)
        mode = "management" if is_registered else "acquisition"
        logger.info(
            "Subscription agent processing",
            mode=mode,
            phone=phone,
            message=message[:50],
        )

        quality_logger = get_quality_logger()

        # Use a dummy tenant_id for prompt loading (prompts are not tenant-specific)
        prompt_tenant_id = tenant_id or self.settings.default_tenant_id
        prompt = await self.get_prompt(prompt_tenant_id)

        # Add mode context to system prompt
        mode_context = (
            f"\n\n## Contexto actual\n"
            f"- Modo: {'Gestión (usuario registrado)' if is_registered else 'Adquisición (usuario nuevo)'}\n"
            f"- Teléfono del usuario: {phone}\n"
        )
        if is_registered:
            mode_context += f"- Tenant ID: {tenant_id}\n"

        full_prompt = prompt + mode_context

        # Select tools based on mode
        tools = MANAGEMENT_TOOLS if is_registered else ACQUISITION_TOOLS

        # Build messages
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": full_prompt},
        ]

        for msg in history[-6:]:
            messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": message})

        try:
            total_tokens_in = 0
            total_tokens_out = 0
            max_tool_rounds = 5

            for _ in range(max_tool_rounds):
                response = await self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    max_tokens=1500,
                    temperature=0.5,
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

                tool_result = await self._execute_tool(
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

    async def _execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        phone: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        """Execute a subscription tool by calling the backend API.

        Args:
            tool_name: Name of the tool to execute.
            args: Tool arguments from LLM.
            phone: User's phone number.
            tenant_id: Tenant ID (empty for unregistered users).

        Returns:
            Tool execution result dict.
        """
        base_url = self.settings.backend_api_url
        headers = {"Authorization": f"Bearer {self.settings.backend_api_key}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                if tool_name == "get_plans":
                    return await self._get_plans(client, base_url)

                elif tool_name == "register_starter":
                    return await self._register_starter(client, base_url, headers, args)

                elif tool_name == "create_checkout":
                    return await self._create_checkout(client, base_url, headers, args)

                elif tool_name == "validate_coupon":
                    return await self._validate_coupon(client, base_url, args)

                elif tool_name == "get_subscription_status":
                    return await self._get_subscription_status(
                        client, base_url, headers, tenant_id
                    )

                elif tool_name == "get_usage":
                    return await self._get_usage(client, base_url, headers, tenant_id)

                elif tool_name == "create_upgrade_checkout":
                    return await self._create_upgrade_checkout(
                        client, base_url, headers, tenant_id, args
                    )

                elif tool_name == "cancel_subscription":
                    return await self._cancel_subscription(
                        client, base_url, headers, tenant_id, args
                    )

                else:
                    return {"success": False, "error": f"Unknown tool: {tool_name}"}

            except httpx.TimeoutException:
                logger.error(f"Tool timeout: {tool_name}")
                return {"success": False, "error": "Timeout al conectar con el servidor"}
            except Exception as e:
                logger.error(f"Tool failed: {tool_name}", error=str(e))
                return {"success": False, "error": str(e)}

    async def _get_plans(
        self,
        client: httpx.AsyncClient,
        base_url: str,
    ) -> dict[str, Any]:
        """Fetch available plans from backend (public endpoint)."""
        response = await client.get(f"{base_url}/api/v1/plans")
        if response.status_code == 200:
            return {"success": True, "data": response.json()}
        return {"success": False, "error": f"Error fetching plans: {response.status_code}"}

    async def _register_starter(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Register a new user with Starter plan via WhatsApp onboarding."""
        response = await client.post(
            f"{base_url}/api/v1/onboarding/whatsapp",
            headers=headers,
            json={
                "phone": args["phone"],
                "display_name": args["display_name"],
                "home_name": args["home_name"],
                "plan": "starter",
            },
        )
        if response.status_code in (200, 201):
            return {"success": True, "data": response.json()}
        return {
            "success": False,
            "error": response.json().get("detail", "Error al registrar"),
        }

    async def _create_checkout(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a pending registration and generate LS checkout link."""
        # First, save pending registration
        pending_response = await client.post(
            f"{base_url}/api/v1/onboarding/whatsapp/pending",
            headers=headers,
            json={
                "phone": args["phone"],
                "display_name": args["display_name"],
                "home_name": args["home_name"],
                "plan_type": args["plan_type"],
                "coupon_code": args.get("coupon_code"),
            },
        )
        if pending_response.status_code not in (200, 201):
            error = pending_response.json().get("detail", "Error al guardar registro")
            return {"success": False, "error": error}

        pending_data = pending_response.json()
        checkout_url = pending_data.get("checkout_url")

        if checkout_url:
            return {
                "success": True,
                "data": {
                    "checkout_url": checkout_url,
                    "plan_type": args["plan_type"],
                },
            }
        return {"success": False, "error": "No se pudo generar el link de pago"}

    async def _validate_coupon(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate a coupon code."""
        response = await client.post(
            f"{base_url}/api/v1/coupons/validate",
            json={
                "code": args["coupon_code"],
                "plan_type": args["plan_type"],
            },
        )
        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "data": {
                    "valid": data.get("valid", False),
                    "discount_percent": data.get("discount_percent"),
                    "message": data.get("message", ""),
                },
            }
        return {"success": False, "error": "Error al validar cupón"}

    async def _get_subscription_status(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        tenant_id: str,
    ) -> dict[str, Any]:
        """Get subscription status for a tenant."""
        response = await client.get(
            f"{base_url}/api/v1/subscriptions/usage/{tenant_id}",
            headers=headers,
        )
        if response.status_code == 200:
            return {"success": True, "data": response.json()}
        return {"success": False, "error": "Error al consultar suscripción"}

    async def _get_usage(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        tenant_id: str,
    ) -> dict[str, Any]:
        """Get usage stats for a tenant."""
        response = await client.get(
            f"{base_url}/api/v1/subscriptions/usage/{tenant_id}",
            headers=headers,
        )
        if response.status_code == 200:
            return {"success": True, "data": response.json()}
        return {"success": False, "error": "Error al consultar uso"}

    async def _create_upgrade_checkout(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        tenant_id: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate checkout URL for plan upgrade or reactivation."""
        response = await client.post(
            f"{base_url}/api/v1/subscriptions/upgrade",
            headers=headers,
            json={
                "tenant_id": tenant_id,
                "plan_type": args["plan_type"],
            },
        )
        if response.status_code in (200, 201):
            data = response.json()
            return {
                "success": True,
                "data": {
                    "checkout_url": data.get("checkout_url"),
                    "plan_type": args["plan_type"],
                },
            }
        return {"success": False, "error": "Error al generar link de upgrade"}

    async def _cancel_subscription(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        tenant_id: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Cancel subscription for a tenant."""
        if not args.get("confirmed"):
            return {
                "success": False,
                "error": "Cancelación no confirmada por el usuario",
            }

        response = await client.post(
            f"{base_url}/api/v1/subscriptions/cancel-by-tenant",
            headers=headers,
            json={
                "tenant_id": tenant_id,
                "reason": args.get("reason", "Sin motivo"),
            },
        )
        if response.status_code == 200:
            return {"success": True, "data": response.json()}
        return {"success": False, "error": "Error al cancelar suscripción"}
