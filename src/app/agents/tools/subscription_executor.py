"""Subscription domain tool executor — backend API calls."""

from typing import Any

import structlog

from ...services.backend_client import get_backend_client

logger = structlog.get_logger()


def _normalize_phone(phone: str) -> str:
    """Ensure phone has + prefix for E.164 format."""
    return phone if phone.startswith("+") else f"+{phone}"


async def execute_subscription_tool(
    tool_name: str,
    args: dict[str, Any],
    phone: str,
    tenant_id: str,
) -> dict[str, Any]:
    """Execute a subscription tool by calling the backend API.

    Extracted from SubscriptionAgent._execute_tool.
    """
    backend = get_backend_client()

    try:
        if tool_name == "get_plans":
            return await _get_plans(backend)
        elif tool_name == "create_checkout":
            return await _create_checkout(backend, args, phone)
        elif tool_name == "validate_coupon":
            return await _validate_coupon(backend, args)
        elif tool_name == "check_payment_status":
            return await _check_payment_status(backend, phone)
        elif tool_name == "complete_setup":
            return await _complete_setup(backend, tenant_id, args)
        elif tool_name == "get_subscription_status":
            return await _get_subscription_status(backend, tenant_id)
        elif tool_name == "get_usage":
            return await _get_usage(backend, tenant_id)
        elif tool_name == "create_upgrade_checkout":
            return await _create_upgrade_checkout(backend, tenant_id, args)
        elif tool_name == "cancel_subscription":
            return await _cancel_subscription(backend, tenant_id, args)
        elif tool_name == "invite_member":
            return await _invite_member(backend, tenant_id, args)
        else:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        logger.error(f"Tool failed: {tool_name}", error=str(e))
        return {"success": False, "error": str(e)}


async def _get_plans(
    backend: Any,
) -> dict[str, Any]:
    """Fetch available plans from backend."""
    response = await backend.get("/api/v1/plans")
    if response.status_code == 200:
        return {"success": True, "data": response.json()}
    return {"success": False, "error": f"Error fetching plans: {response.status_code}"}


async def _create_checkout(
    backend: Any,
    args: dict[str, Any],
    phone: str,
) -> dict[str, Any]:
    """Create a pending registration and generate LS checkout link.

    All plans (including Starter) go through checkout. home_name is NOT
    collected here -- it's configured post-payment via complete_setup.
    """
    pending_response = await backend.post(
        "/api/v1/onboarding/whatsapp/pending",
        json={
            "phone": _normalize_phone(phone),
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
    backend: Any,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Validate a coupon code."""
    response = await backend.post(
        "/api/v1/coupons/validate",
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


async def _check_payment_status(
    backend: Any,
    phone: str,
) -> dict[str, Any]:
    """Check if payment has been processed for this phone number.

    Queries the phone lookup endpoint to see if the user has been
    registered (which only happens after successful payment via LS webhook).
    """
    normalized = _normalize_phone(phone)
    response = await backend.get(
        "/api/v1/phone/lookup",
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


async def _complete_setup(
    backend: Any,
    tenant_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Complete home setup after payment (set home_name, mark onboarding done)."""
    response = await backend.patch(
        f"/api/v1/tenants/{tenant_id}/setup",
        json={"home_name": args["home_name"]},
    )
    if response.status_code == 200:
        return {"success": True, "data": response.json()}
    error_detail = response.json().get("detail", "Error al configurar el hogar")
    return {"success": False, "error": error_detail}


async def _get_subscription_status(
    backend: Any,
    tenant_id: str,
) -> dict[str, Any]:
    """Get subscription status for a tenant."""
    response = await backend.get(
        f"/api/v1/subscriptions/usage/{tenant_id}",
    )
    if response.status_code == 200:
        return {"success": True, "data": response.json()}
    return {"success": False, "error": "Error al consultar suscripción"}


async def _get_usage(
    backend: Any,
    tenant_id: str,
) -> dict[str, Any]:
    """Get usage stats for a tenant."""
    response = await backend.get(
        f"/api/v1/subscriptions/usage/{tenant_id}",
    )
    if response.status_code == 200:
        return {"success": True, "data": response.json()}
    return {"success": False, "error": "Error al consultar uso"}


async def _create_upgrade_checkout(
    backend: Any,
    tenant_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Generate checkout URL for plan upgrade or reactivation."""
    response = await backend.post(
        "/api/v1/subscriptions/upgrade",
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
    backend: Any,
    tenant_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Cancel subscription for a tenant."""
    if not args.get("confirmed"):
        return {
            "success": False,
            "error": "Cancelación no confirmada por el usuario",
        }

    response = await backend.post(
        "/api/v1/subscriptions/cancel-by-tenant",
        json={
            "tenant_id": tenant_id,
            "reason": args.get("reason", "Sin motivo"),
        },
    )
    if response.status_code == 200:
        return {"success": True, "data": response.json()}
    return {"success": False, "error": "Error al cancelar suscripción"}


async def _invite_member(
    backend: Any,
    tenant_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Invite a member to the tenant's household."""
    response = await backend.post(
        f"/api/v1/tenants/{tenant_id}/members/bot",
        json={
            "phone": args["phone"],
        },
    )
    if response.status_code in (200, 201):
        return {"success": True, "data": response.json()}
    error_detail = response.json().get("detail", "Error al invitar miembro")
    return {"success": False, "error": error_detail}
