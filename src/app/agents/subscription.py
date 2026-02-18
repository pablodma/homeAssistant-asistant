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
            "name": "create_checkout",
            "description": "Genera un link de pago de Lemon Squeezy para cualquier plan. El teléfono se obtiene automáticamente. NO pide home_name (se configura después del pago). REQUIERE email del usuario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "display_name": {
                        "type": "string",
                        "description": "Nombre del usuario",
                    },
                    "email": {
                        "type": "string",
                        "description": "Email del usuario (para facturación)",
                    },
                    "plan_type": {
                        "type": "string",
                        "enum": ["starter", "family", "premium"],
                        "description": "Tipo de plan",
                    },
                    "coupon_code": {
                        "type": "string",
                        "description": "Código de cupón (opcional)",
                    },
                },
                "required": ["display_name", "email", "plan_type"],
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
                        "enum": ["starter", "family", "premium"],
                        "description": "Plan al que se aplicaría",
                    },
                },
                "required": ["coupon_code", "plan_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_payment_status",
            "description": "Verifica si el pago del usuario fue procesado. OBLIGATORIO usarla cuando el usuario dice que ya pagó. Consulta el estado real del registro.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]

# Tool definitions for post-payment setup (registered but onboarding_completed=false)
SETUP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "complete_setup",
            "description": "Completa la configuración del hogar después del pago. Actualiza el nombre del hogar y marca el onboarding como completo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "home_name": {
                        "type": "string",
                        "description": "Nombre del hogar (ej: Casa García, Mi Depto)",
                    },
                },
                "required": ["home_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "invite_member",
            "description": "Invita a un miembro al hogar. Solo necesita el número de WhatsApp. El nombre se toma automáticamente cuando el invitado escriba.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Número de WhatsApp del invitado en formato +549...",
                    },
                },
                "required": ["phone"],
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
    {
        "type": "function",
        "function": {
            "name": "invite_member",
            "description": "Invita a un miembro al hogar. Solo necesita el número de WhatsApp. El nombre se toma automáticamente cuando el invitado escriba.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Número de WhatsApp del invitado en formato +549...",
                    },
                },
                "required": ["phone"],
            },
        },
    },
]


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
        # #region agent log
        import json as _dbg_json; open(r"d:\Proyectos\homeAsiss\.cursor\debug.log", "a", encoding="utf-8").write(_dbg_json.dumps({"timestamp": __import__("time").time(), "location": "subscription.py:274", "message": "agent_mode", "data": {"mode": mode, "explicit_mode": explicit_mode, "tenant_id": tenant_id, "phone": phone, "message_preview": message[:80]}, "hypothesisId": "H1,H2,H3"}) + "\n")
        # #endregion

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
            "acquisition": ACQUISITION_TOOLS,
            "setup": SETUP_TOOLS,
            "management": MANAGEMENT_TOOLS,
        }
        tools = tools_map.get(mode, MANAGEMENT_TOOLS)

        # Build messages
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": full_prompt},
        ]

        for msg in history[-6:]:
            messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": f"[USER_MSG]{message}[/USER_MSG]"})

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
                    temperature=0.4,
                    max_completion_tokens=1500,
                )

                choice = response.choices[0]
                if response.usage:
                    total_tokens_in += response.usage.prompt_tokens
                    total_tokens_out += response.usage.completion_tokens

                if not choice.message.tool_calls:
                    # #region agent log
                    import json as _dbg_json; open(r"d:\Proyectos\homeAsiss\.cursor\debug.log", "a", encoding="utf-8").write(_dbg_json.dumps({"timestamp": __import__("time").time(), "location": "subscription.py:340", "message": "no_tool_call_response", "data": {"mode": mode, "response_preview": (choice.message.content or "")[:150], "tool_calls": None}, "hypothesisId": "H1,H2,H3"}) + "\n")
                    # #endregion
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
                # #region agent log
                import json as _dbg_json; open(r"d:\Proyectos\homeAsiss\.cursor\debug.log", "a", encoding="utf-8").write(_dbg_json.dumps({"timestamp": __import__("time").time(), "location": "subscription.py:358", "message": "tool_call_executed", "data": {"mode": mode, "tool_name": tool_name, "tool_args": tool_args}, "hypothesisId": "H3"}) + "\n")
                # #endregion

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

                elif tool_name == "create_checkout":
                    return await self._create_checkout(client, base_url, headers, args, phone)

                elif tool_name == "validate_coupon":
                    return await self._validate_coupon(client, base_url, args)

                elif tool_name == "check_payment_status":
                    return await self._check_payment_status(client, base_url, phone)

                elif tool_name == "complete_setup":
                    return await self._complete_setup(
                        client, base_url, headers, tenant_id, args
                    )

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

                elif tool_name == "invite_member":
                    return await self._invite_member(
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

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """Ensure phone has + prefix for E.164 format."""
        return phone if phone.startswith("+") else f"+{phone}"

    async def _check_payment_status(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        phone: str,
    ) -> dict[str, Any]:
        """Check if payment has been processed for this phone number.

        Queries the phone lookup endpoint to see if the user has been
        registered (which only happens after successful payment via LS webhook).
        """
        normalized = self._normalize_phone(phone)
        response = await client.get(
            f"{base_url}/api/v1/phone/lookup",
            params={"phone": normalized},
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("found"):
                return {
                    "success": True,
                    "data": {
                        "payment_confirmed": True,
                        "message": "El pago fue procesado. El usuario será redirigido automáticamente al configurar su hogar en su próximo mensaje.",
                    },
                }
            return {
                "success": True,
                "data": {
                    "payment_confirmed": False,
                    "message": "El pago todavía no fue procesado por el sistema.",
                },
            }
        return {
            "success": False,
            "error": "No se pudo verificar el estado del pago",
        }

    async def _create_checkout(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        args: dict[str, Any],
        phone: str,
    ) -> dict[str, Any]:
        """Create a pending registration and generate LS checkout link.

        All plans (including Starter) go through checkout. home_name is NOT
        collected here — it's configured post-payment via complete_setup.
        """
        pending_response = await client.post(
            f"{base_url}/api/v1/onboarding/whatsapp/pending",
            headers=headers,
            json={
                "phone": self._normalize_phone(phone),
                "display_name": args["display_name"],
                "email": args["email"],
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

    async def _complete_setup(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        tenant_id: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Complete home setup after payment (set home_name, mark onboarding done)."""
        response = await client.patch(
            f"{base_url}/api/v1/tenants/{tenant_id}/setup",
            headers=headers,
            json={"home_name": args["home_name"]},
        )
        if response.status_code == 200:
            return {"success": True, "data": response.json()}
        error_detail = response.json().get("detail", "Error al configurar el hogar")
        return {"success": False, "error": error_detail}

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

    async def _invite_member(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        tenant_id: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Invite a member to the tenant's household."""
        response = await client.post(
            f"{base_url}/api/v1/tenants/{tenant_id}/members/bot",
            headers=headers,
            json={
                "phone": args["phone"],
            },
        )
        if response.status_code in (200, 201):
            return {"success": True, "data": response.json()}
        error_detail = response.json().get("detail", "Error al invitar miembro")
        return {"success": False, "error": error_detail}
