"""Tests for webhook access gating before router execution."""

from datetime import datetime, timezone

import pytest

from src.app.services.phone_resolver import PhoneTenantInfo
from src.app.whatsapp import webhook as webhook_module
from src.app.whatsapp.types import IncomingMessage


class _FakeWhatsAppClient:
    def __init__(self) -> None:
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_buttons: list[dict] = []

    async def mark_as_read_and_typing(self, message_id: str) -> None:  # noqa: ARG002
        return None

    async def send_text(self, phone: str, text: str) -> bool:
        self.sent_texts.append((phone, text))
        return True

    async def send_interactive_buttons(
        self,
        phone: str,
        body: str,
        buttons: list[dict[str, str]],
    ) -> bool:
        self.sent_buttons.append({"phone": phone, "body": body, "buttons": buttons})
        return True


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


@pytest.mark.asyncio
async def test_handle_unregistered_user_sends_aira_copy_and_button(monkeypatch):
    """Unregistered users should receive Aira copy + web quick-action button."""

    class _FakeResponse:
        status_code = 200
        content = b'{"url":"https://app.example.com/onboarding?token=abc"}'

        def json(self) -> dict:
            return {"url": "https://app.example.com/onboarding?token=abc"}

    class _FakeBackend:
        async def post(self, *args, **kwargs):  # noqa: ARG002
            return _FakeResponse()

    class _FakeInteractionLogger:
        async def log(self, **kwargs):  # noqa: ARG002
            return "interaction-1"

    monkeypatch.setattr(webhook_module, "get_backend_client", lambda: _FakeBackend())
    monkeypatch.setattr(webhook_module, "InteractionLogger", lambda: _FakeInteractionLogger())

    whatsapp = _FakeWhatsAppClient()
    message = IncomingMessage(
        message_id="msg-unreg-1",
        phone="5491111111111",
        text="hola",
        timestamp=datetime.now(timezone.utc),
        contact_name="Pablo",
    )

    await webhook_module._handle_unregistered_user(message, whatsapp)

    assert len(whatsapp.sent_texts) == 0

    assert len(whatsapp.sent_buttons) == 1
    button_body = whatsapp.sent_buttons[0]["body"]
    assert "Aira pone tu hogar en un solo lugar" in button_body
    assert "Para empezar o conocer más, ingresá a la web tocando el botón de abajo." in button_body
    assert "Cuando termines, volvé a escribirme." in button_body

    button = whatsapp.sent_buttons[0]["buttons"][0]
    assert button["id"] == webhook_module.ACCESS_WEB_BUTTON_ID
    assert button["title"] == "Ir a la web"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subscription_status", "expected_fragment"),
    [
        ("pending", "vas a poder usar Aira"),
        ("canceled", "Para empezar o conocer más, ingresá a la web tocando el botón de abajo."),
    ],
)
async def test_handle_subscription_required_user_sends_button_and_updated_copy(
    monkeypatch,
    subscription_status: str,
    expected_fragment: str,
):
    """Subscription blocked users should get updated copy + web quick-action button."""

    class _FakeSettings:
        frontend_url = "https://app.example.com"

    class _FakeInteractionLogger:
        async def log(self, **kwargs):  # noqa: ARG002
            return "interaction-2"

    monkeypatch.setattr(webhook_module, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(webhook_module, "InteractionLogger", lambda: _FakeInteractionLogger())

    whatsapp = _FakeWhatsAppClient()
    message = IncomingMessage(
        message_id="msg-sub-1",
        phone="5491111111111",
        text="hola",
        timestamp=datetime.now(timezone.utc),
        contact_name="Pablo",
    )
    phone_info = PhoneTenantInfo(
        tenant_id="f2c6f5a0-4d4d-4d53-a4b9-1b8c9e2c11aa",
        user_name="Pablo",
        home_name="Mi hogar",
        onboarding_completed=True,
        is_registered=True,
        tenant_active=True,
        subscription_status=subscription_status,
        has_active_subscription=False,
        can_access_dashboard=False,
        can_interact_agent=False,
        next_step="subscribe",
    )

    await webhook_module._handle_subscription_required_user(message, phone_info, whatsapp)

    assert len(whatsapp.sent_texts) == 0

    assert len(whatsapp.sent_buttons) == 1
    button_body = whatsapp.sent_buttons[0]["body"]
    assert expected_fragment in button_body
    assert "Cuando termines, volvé a escribirme." in button_body

    button = whatsapp.sent_buttons[0]["buttons"][0]
    assert button["id"] == webhook_module.ACCESS_WEB_BUTTON_ID
    assert button["title"] == "Ir a la web"


@pytest.mark.asyncio
async def test_handle_setup_user_sends_button(monkeypatch):
    """Setup-pending users should receive link + quick-action button."""

    class _FakeSettings:
        frontend_url = "https://app.example.com"

    class _FakeInteractionLogger:
        async def log(self, **kwargs):  # noqa: ARG002
            return "interaction-3"

    monkeypatch.setattr(webhook_module, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(webhook_module, "InteractionLogger", lambda: _FakeInteractionLogger())

    whatsapp = _FakeWhatsAppClient()
    message = IncomingMessage(
        message_id="msg-setup-1",
        phone="5491111111111",
        text="hola",
        timestamp=datetime.now(timezone.utc),
        contact_name="Pablo",
    )
    phone_info = PhoneTenantInfo(
        tenant_id="f2c6f5a0-4d4d-4d53-a4b9-1b8c9e2c11aa",
        user_name="Pablo",
        home_name="Mi hogar",
        onboarding_completed=False,
        is_registered=True,
        tenant_active=True,
        subscription_status="authorized",
        has_active_subscription=True,
        can_access_dashboard=False,
        can_interact_agent=False,
        next_step="setup",
    )

    await webhook_module._handle_setup_user(message, phone_info, whatsapp)

    assert len(whatsapp.sent_texts) == 0

    assert len(whatsapp.sent_buttons) == 1
    button_body = whatsapp.sent_buttons[0]["body"]
    assert "Completá la configuración de tu hogar tocando el botón de abajo." in button_body
    assert "Cuando termines, volvé a escribirme." in button_body

    button = whatsapp.sent_buttons[0]["buttons"][0]
    assert button["id"] == webhook_module.ACCESS_WEB_BUTTON_ID
    assert button["title"] == "Ir a la web"
