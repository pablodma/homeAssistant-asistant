"""Tests for webhook access gating before router execution."""

from datetime import datetime, timezone

import pytest

from src.app.services.phone_resolver import PhoneTenantInfo
from src.app.whatsapp import webhook as webhook_module
from src.app.whatsapp.types import IncomingMessage


class _FakeWhatsAppClient:
    async def mark_as_read_and_typing(self, message_id: str) -> None:  # noqa: ARG002
        return None

    async def send_text(self, phone: str, text: str) -> None:  # noqa: ARG002
        return None


@pytest.mark.asyncio
async def test_process_message_blocks_when_subscription_not_authorized(monkeypatch):
    """If subscription is not authorized, router must not run."""
    blocked_called = False

    class _FakeResolver:
        async def resolve(self, phone: str):  # noqa: ARG002
            return PhoneTenantInfo(
                tenant_id="f2c6f5a0-4d4d-4d53-a4b9-1b8c9e2c11aa",
                user_name="Pablo",
                home_name="Mi hogar",
                onboarding_completed=True,
                is_registered=True,
                tenant_active=True,
                subscription_status="pending",
                has_active_subscription=False,
                can_access_dashboard=False,
                can_interact_agent=False,
                next_step="subscribe",
            )

        def invalidate_cache(self, phone: str) -> None:  # noqa: ARG002
            return None

    async def _fake_handle_subscription_required_user(message, phone_info, whatsapp):  # noqa: ARG001
        nonlocal blocked_called
        blocked_called = True

    class _FailRouter:
        async def process(self, *args, **kwargs):  # noqa: ARG002
            raise AssertionError("RouterAgent no debería ejecutarse sin suscripción authorized")

    monkeypatch.setattr(webhook_module, "get_whatsapp_client", lambda: _FakeWhatsAppClient())
    monkeypatch.setattr(webhook_module, "get_phone_resolver", lambda: _FakeResolver())
    monkeypatch.setattr(webhook_module, "_handle_subscription_required_user", _fake_handle_subscription_required_user)
    monkeypatch.setattr(webhook_module, "RouterAgent", _FailRouter)
    monkeypatch.setattr(webhook_module, "_is_rate_limited", lambda *args, **kwargs: False)

    message = IncomingMessage(
        message_id="msg-1",
        phone="5491111111111",
        text="hola",
        timestamp=datetime.now(timezone.utc),
        contact_name="Pablo",
    )

    await webhook_module.process_message(message)

    assert blocked_called is True
