"""Tests for webhook access gating before router execution."""

from datetime import datetime, timezone

import pytest

import src.app.services.message_guards as guards_module
from src.app.services.message_guards import GuardResult
from src.app.services.phone_resolver import PhoneTenantInfo
from src.app.whatsapp import webhook as webhook_module
from src.app.whatsapp.types import IncomingMessage, WhatsAppMessage


async def _no_rate_limit(phone: str, max_per_minute: int = 20) -> GuardResult:  # noqa: ARG001
    return GuardResult(should_block=False, guard_name="rate_limit")


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
    monkeypatch.setattr(guards_module, "check_rate_limit", _no_rate_limit)

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
async def test_process_message_routes_access_web_button_click(monkeypatch):
    """Access web quick action should be handled directly, not re-gated."""
    access_button_called = False

    class _FakeResolver:
        async def resolve(self, phone: str):  # noqa: ARG002
            return None

        def invalidate_cache(self, phone: str) -> None:  # noqa: ARG002
            return None

    async def _fake_handle_access_web_button_click(message, phone_info, whatsapp):  # noqa: ARG001
        nonlocal access_button_called
        access_button_called = True

    async def _fail_unregistered_handler(message, whatsapp):  # noqa: ARG001
        raise AssertionError("No debería re-ejecutar el flujo de no registrado al tocar el botón")

    monkeypatch.setattr(webhook_module, "get_whatsapp_client", lambda: _FakeWhatsAppClient())
    monkeypatch.setattr(webhook_module, "get_phone_resolver", lambda: _FakeResolver())
    monkeypatch.setattr(webhook_module, "_handle_access_web_button_click", _fake_handle_access_web_button_click)
    monkeypatch.setattr(webhook_module, "_handle_unregistered_user", _fail_unregistered_handler)
    monkeypatch.setattr(guards_module, "check_rate_limit", _no_rate_limit)

    message = IncomingMessage(
        message_id="msg-access-cta-1",
        phone="5491111111111",
        text="Ir a la web",
        timestamp=datetime.now(timezone.utc),
        contact_name="Pablo",
        is_interactive=True,
        interactive_type="button_reply",
        interactive_id=webhook_module.ACCESS_WEB_BUTTON_ID,
    )

    await webhook_module.process_message(message)

    assert access_button_called is True


@pytest.mark.asyncio
async def test_process_message_routes_access_web_button_click_from_text_fallback(monkeypatch):
    """Text fallback 'Ir a la web' should trigger direct web access handler."""
    access_button_called = False

    class _FakeResolver:
        async def resolve(self, phone: str):  # noqa: ARG002
            return None

        def invalidate_cache(self, phone: str) -> None:  # noqa: ARG002
            return None

    async def _fake_handle_access_web_button_click(message, phone_info, whatsapp):  # noqa: ARG001
        nonlocal access_button_called
        access_button_called = True

    async def _fail_unregistered_handler(message, whatsapp):  # noqa: ARG001
        raise AssertionError("No debería re-ejecutar el flujo de no registrado al enviar 'Ir a la web'")

    monkeypatch.setattr(webhook_module, "get_whatsapp_client", lambda: _FakeWhatsAppClient())
    monkeypatch.setattr(webhook_module, "get_phone_resolver", lambda: _FakeResolver())
    monkeypatch.setattr(webhook_module, "_handle_access_web_button_click", _fake_handle_access_web_button_click)
    monkeypatch.setattr(webhook_module, "_handle_unregistered_user", _fail_unregistered_handler)
    monkeypatch.setattr(guards_module, "check_rate_limit", _no_rate_limit)

    message = IncomingMessage(
        message_id="msg-access-cta-2",
        phone="5491111111111",
        text="Ir a la web",
        timestamp=datetime.now(timezone.utc),
        contact_name="Pablo",
        is_interactive=False,
    )

    await webhook_module.process_message(message)

    assert access_button_called is True


def test_incoming_message_from_webhook_button_type_maps_to_interactive_payload():
    """When webhook uses type='button', payload should map to interactive_id."""
    raw = {
        "id": "wamid.button.1",
        "from": "5491111111111",
        "timestamp": "1710000000",
        "type": "button",
        "button": {
            "payload": webhook_module.ACCESS_WEB_BUTTON_ID,
            "text": "Ir a la web",
        },
    }
    message = WhatsAppMessage.model_validate(raw)
    incoming = IncomingMessage.from_webhook(message)

    assert incoming.is_interactive is True
    assert incoming.interactive_type == "button_reply"
    assert incoming.interactive_id == webhook_module.ACCESS_WEB_BUTTON_ID
    assert incoming.text == "Ir a la web"


@pytest.mark.asyncio
async def test_handle_unregistered_user_sends_aira_copy_and_link(monkeypatch):
    """Unregistered users should receive Aira copy + plain web link."""

    class _FakeResponse:
        status_code = 200
        content = b'{"url":"https://app.example.com/onboarding/start/abc"}'

        def json(self) -> dict:
            return {"url": "https://app.example.com/onboarding/start/abc"}

    class _FakeBackend:
        async def post(self, *args, **kwargs):  # noqa: ARG002
            return _FakeResponse()

    class _FakeInteractionLogger:
        async def log(self, **kwargs):  # noqa: ARG002
            return "interaction-1"

    class _FakeSettings:
        @property
        def is_production(self) -> bool:
            return False

    monkeypatch.setattr(webhook_module, "get_backend_client", lambda: _FakeBackend())
    monkeypatch.setattr(webhook_module, "InteractionLogger", lambda: _FakeInteractionLogger())
    monkeypatch.setattr(webhook_module, "get_settings", lambda: _FakeSettings())

    whatsapp = _FakeWhatsAppClient()
    message = IncomingMessage(
        message_id="msg-unreg-1",
        phone="5491111111111",
        text="hola",
        timestamp=datetime.now(timezone.utc),
        contact_name="Pablo",
    )

    await webhook_module._handle_unregistered_user(message, whatsapp)

    assert len(whatsapp.sent_texts) == 1
    text = whatsapp.sent_texts[0][1]
    assert "Aira pone tu hogar en un solo lugar" in text
    assert "Para empezar o conocer más, ingresá a la web:" in text
    assert "https://home-assistant-frontend-brown.vercel.app/onboarding/start/abc" in text
    assert "Cuando termines, volvé a escribirme." in text

    assert len(whatsapp.sent_buttons) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subscription_status", "expected_fragment"),
    [
        ("pending", "vas a poder usar Aira"),
        ("canceled", "Para empezar o conocer más, ingresá a la web:"),
    ],
)
async def test_handle_subscription_required_user_sends_button_and_updated_copy(
    monkeypatch,
    subscription_status: str,
    expected_fragment: str,
):
    """Subscription blocked users should get updated copy + plain web link."""

    class _FakeSettings:
        @property
        def is_production(self) -> bool:
            return False

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

    assert len(whatsapp.sent_texts) == 1
    text = whatsapp.sent_texts[0][1]
    assert expected_fragment in text
    assert "https://home-assistant-frontend-brown.vercel.app/contratar" in text
    assert "Cuando termines, volvé a escribirme." in text

    assert len(whatsapp.sent_buttons) == 0


@pytest.mark.asyncio
async def test_handle_setup_user_sends_link(monkeypatch):
    """Setup-pending users should receive plain web link."""

    class _FakeSettings:
        @property
        def is_production(self) -> bool:
            return False

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

    assert len(whatsapp.sent_texts) == 1
    text = whatsapp.sent_texts[0][1]
    assert "Completá la configuración de tu hogar en la web:" in text
    assert "https://home-assistant-frontend-brown.vercel.app/onboarding" in text
    assert "Cuando termines, volvé a escribirme." in text

    assert len(whatsapp.sent_buttons) == 0
