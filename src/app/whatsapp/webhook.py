"""WhatsApp webhook handler.

Note: All agent decision logic is in prompts, not in this code.
This webhook only routes messages and sends responses.
"""

import asyncio
import hashlib
import hmac

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response

from ..config import get_settings
from ..agents.router import RouterAgent
from ..agents.qa import QAAgent
from ..services.conversation import ConversationService
from ..services.interaction_log import InteractionLogger
from ..services.input_guard import sanitize_message
from ..services.output_guard import check_response
from ..services.quick_actions import (
    clear_pending_expense_edit,
    get_pending_expense_edit,
    parse_quick_action_id,
    set_pending_expense_edit,
)
from ..services.quality_logger import get_quality_logger
from ..services.backend_client import get_backend_client
from ..services.phone_resolver import get_phone_resolver
from ..services.transcription import get_transcription_service
from .client import get_whatsapp_client
from .types import IncomingMessage, WhatsAppWebhookPayload

logger = structlog.get_logger()
router = APIRouter(tags=["WhatsApp Webhook"])

# Per-phone rate limiter (in-memory, resets on restart)
_rate_limit_store: dict[str, list[float]] = {}


def _is_rate_limited(phone: str, max_per_minute: int = 20) -> bool:
    """Check if a phone number has exceeded the rate limit."""
    import time

    now = time.time()
    window = 60.0

    timestamps = _rate_limit_store.get(phone, [])
    timestamps = [t for t in timestamps if now - t < window]
    timestamps.append(now)
    _rate_limit_store[phone] = timestamps

    return len(timestamps) > max_per_minute


# QA Agent singleton
_qa_agent: QAAgent | None = None


def get_qa_agent() -> QAAgent:
    """Get singleton QA Agent instance."""
    global _qa_agent
    if _qa_agent is None:
        _qa_agent = QAAgent()
    return _qa_agent


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
) -> Response:
    """Verify WhatsApp webhook.

    This endpoint is called by Meta when setting up the webhook.
    It verifies that we own this endpoint by checking the verify token.
    """
    settings = get_settings()

    logger.info(
        "Webhook verification request",
        mode=hub_mode,
        token_received=bool(hub_verify_token),
    )

    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("Webhook verified successfully")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("Webhook verification failed", mode=hub_mode)
    return Response(content="Verification failed", status_code=403)


def _verify_webhook_signature(payload: bytes, signature_header: str, app_secret: str) -> bool:
    """Verify Meta's X-Hub-Signature-256 HMAC signature."""
    expected = hmac.new(
        app_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


@router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Receive WhatsApp webhook events.

    This endpoint receives all WhatsApp events (messages, status updates, etc.)
    and processes them in the background.
    """
    try:
        settings = get_settings()
        raw_body = await request.body()

        if settings.whatsapp_app_secret:
            signature = request.headers.get("x-hub-signature-256", "")
            if not signature or not _verify_webhook_signature(
                raw_body, signature, settings.whatsapp_app_secret
            ):
                logger.warning("Invalid webhook signature")
                raise HTTPException(status_code=401, detail="Invalid signature")
        elif settings.is_production:
            logger.error("WHATSAPP_APP_SECRET not configured in production")
            raise HTTPException(status_code=500, detail="Webhook verification not configured")

        import json
        body = json.loads(raw_body)
        logger.debug("Webhook received")

        # Parse webhook payload
        payload = WhatsAppWebhookPayload.model_validate(body)

        # Process each entry
        for entry in payload.entry:
            for change in entry.changes:
                if change.field == "messages" and change.value.messages:
                    # Process messages in background
                    for message in change.value.messages:
                        # Handle text messages
                        if message.type == "text" and message.text:
                            contact = None
                            if change.value.contacts:
                                contact = change.value.contacts[0]

                            incoming = IncomingMessage.from_webhook(message, contact)
                            background_tasks.add_task(process_message, incoming)

                        # Handle audio/voice messages
                        elif message.type == "audio" and message.audio:
                            contact = None
                            if change.value.contacts:
                                contact = change.value.contacts[0]

                            incoming = IncomingMessage.from_webhook(message, contact)
                            background_tasks.add_task(process_message, incoming)

                        # Handle interactive responses (list/button selections)
                        elif message.type == "interactive" and message.interactive:
                            contact = None
                            if change.value.contacts:
                                contact = change.value.contacts[0]

                            incoming = IncomingMessage.from_webhook(message, contact)
                            background_tasks.add_task(process_message, incoming)

        return {"status": "ok"}

    except Exception as e:
        logger.error("Error processing webhook", error=str(e))
        # Always return 200 to prevent Meta from retrying
        return {"status": "error", "message": str(e)}


async def process_message(message: IncomingMessage) -> None:
    """Process an incoming message.

    This runs in the background after the webhook returns.
    """
    import time

    start_time = time.time()
    whatsapp = get_whatsapp_client()
    quality_logger = get_quality_logger()

    # Track context for error logging
    tenant_id: str | None = None
    agent_used: str | None = None
    selected_quick_action_id: str | None = None
    selected_quick_action_kind: str | None = None
    selected_quick_action_expense_id: str | None = None

    settings = get_settings()
    if _is_rate_limited(message.phone, settings.max_messages_per_minute):
        logger.warning("Rate limited", phone=message.phone)
        whatsapp = get_whatsapp_client()
        await whatsapp.send_text(
            message.phone,
            "Estás enviando mensajes muy rápido. Esperá un momento e intentá de nuevo.",
        )
        return

    logger.info(
        "Processing message",
        phone=message.phone,
        is_audio=message.is_audio,
        text=message.text[:50] + "..." if message.text and len(message.text) > 50 else message.text,
    )

    try:
        # Mark message as read and show typing indicator
        await whatsapp.mark_as_read_and_typing(message.message_id)

        # Transcribe audio messages before processing
        if message.is_audio and message.audio_media_id:
            try:
                audio_bytes, content_type = await whatsapp.download_media(
                    message.audio_media_id
                )
                transcription_service = get_transcription_service()
                transcribed_text = await transcription_service.transcribe(
                    audio_bytes, content_type
                )

                if not transcribed_text:
                    await whatsapp.send_text(
                        message.phone,
                        "No pude entender el audio. ¿Podrías repetirlo o enviarlo como texto? 🎙️",
                    )
                    return

                # Replace empty text with transcription
                message = message.model_copy(update={"text": transcribed_text})
                logger.info(
                    "Audio transcribed for processing",
                    phone=message.phone,
                    transcribed_text=transcribed_text[:80],
                )

            except Exception as e:
                logger.error(
                    "Audio processing failed",
                    phone=message.phone,
                    error=str(e),
                )
                await whatsapp.send_text(
                    message.phone,
                    "No pude procesar tu audio. Por favor, intentá de nuevo o enviá un mensaje de texto. 🎙️",
                )
                return

        # Input guard: sanitize and flag injection attempts
        guard_result = sanitize_message(message.text or "")
        message = message.model_copy(update={"text": guard_result.text})

        # Convert known interactive actions to explicit user intents.
        if message.is_interactive and message.interactive_id:
            parsed_action = parse_quick_action_id(message.interactive_id)
            if parsed_action:
                selected_quick_action_id = message.interactive_id
                selected_quick_action_kind = parsed_action.kind
                selected_quick_action_expense_id = parsed_action.expense_id

                if parsed_action.kind == "expense_delete" and parsed_action.expense_id:
                    message = message.model_copy(
                        update={"text": f"Eliminar gasto con expense_id={parsed_action.expense_id}"}
                    )
                elif parsed_action.kind == "expense_edit" and parsed_action.expense_id:
                    set_pending_expense_edit(message.phone, parsed_action.expense_id)
                    message = message.model_copy(
                        update={"text": f"Editar gasto con expense_id={parsed_action.expense_id}"}
                    )
                elif parsed_action.kind == "menu":
                    clear_pending_expense_edit(message.phone)
                    message = message.model_copy(
                        update={"text": "Quiero volver al menú principal de finanzas."}
                    )

        # If there is pending edit context, append it to next user message.
        pending_edit = get_pending_expense_edit(message.phone)
        if pending_edit and not message.is_interactive:
            message = message.model_copy(
                update={"text": f"[PENDING_EXPENSE_EDIT_ID={pending_edit.expense_id}] {message.text}"}
            )

        # Resolve tenant by phone number
        phone_resolver = get_phone_resolver()
        phone_info = await phone_resolver.resolve(message.phone)

        if not phone_info:
            logger.info("unregistered_phone_onboarding", phone=message.phone)
            await _handle_unregistered_user(message, whatsapp)
            return

        if not phone_info.onboarding_completed:
            logger.info(
                "setup_pending",
                phone=message.phone,
                tenant_id=phone_info.tenant_id,
            )
            phone_resolver.invalidate_cache(message.phone)
            await _handle_setup_user(message, phone_info, whatsapp)
            return

        tenant_id = phone_info.tenant_id
        user_name = phone_info.user_name or message.contact_name

        logger.info(
            "tenant_resolved",
            phone=message.phone,
            tenant_id=tenant_id,
            home_name=phone_info.home_name,
        )

        # Initialize services
        conversation_service = ConversationService()
        interaction_logger = InteractionLogger()

        # Get or create conversation context
        conversation = await conversation_service.get_or_create(
            phone=message.phone,
            tenant_id=tenant_id,
        )

        # Load conversation history
        history = await conversation_service.get_history(
            phone=message.phone,
            tenant_id=tenant_id,
            limit=10,
        )

        # Process message through router agent
        # All decision logic is in the agent prompts - the code just routes and sends
        router_agent = RouterAgent()
        result = await router_agent.process(
            message=message.text,
            phone=message.phone,
            tenant_id=tenant_id,
            history=history,
        )

        agent_used = result.agent_used

        # Output guard: check for prompt leakage, sensitive data, length
        output_check = check_response(result.response, agent_name=result.sub_agent_used or result.agent_used)
        response_text = output_check.text

        await whatsapp.send_text(message.phone, response_text)

        # Send quick actions when agent metadata provides them.
        metadata = result.metadata or {}
        quick_actions = metadata.get("quick_actions") if isinstance(metadata, dict) else None
        if quick_actions and isinstance(quick_actions, dict):
            actions = quick_actions.get("actions") or []
            if isinstance(actions, list) and actions:
                if len(actions) <= 3:
                    await whatsapp.send_interactive_buttons(
                        phone=message.phone,
                        body="Elegí una acción rápida:",
                        buttons=[
                            {"id": str(action.get("id", "")), "title": str(action.get("title", ""))}
                            for action in actions
                            if action.get("id") and action.get("title")
                        ],
                    )
                else:
                    await whatsapp.send_interactive_list(
                        phone=message.phone,
                        header="Acciones rápidas",
                        body="Elegí una opción",
                        button_text="Ver opciones",
                        sections=[
                            {
                                "title": "Acciones disponibles",
                                "rows": [
                                    {
                                        "id": str(action.get("id", "")),
                                        "title": str(action.get("title", "")),
                                    }
                                    for action in actions
                                    if action.get("id") and action.get("title")
                                ],
                            }
                        ],
                    )

        # Save to conversation history
        await conversation_service.add_message(
            phone=message.phone,
            tenant_id=tenant_id,
            role="user",
            content=message.text,
        )
        await conversation_service.add_message(
            phone=message.phone,
            tenant_id=tenant_id,
            role="assistant",
            content=response_text,
        )

        # Log interaction and get ID for QA
        response_time_ms = int((time.time() - start_time) * 1000)
        interaction_metadata = result.metadata or {}
        if selected_quick_action_id:
            interaction_metadata["quick_action_selected"] = selected_quick_action_id
        if selected_quick_action_kind:
            interaction_metadata["quick_action_kind"] = selected_quick_action_kind
        if selected_quick_action_expense_id:
            interaction_metadata["quick_action_expense_id"] = selected_quick_action_expense_id
        if isinstance((result.metadata or {}).get("quick_actions"), dict):
            actions = (result.metadata or {}).get("quick_actions", {}).get("actions", [])
            if isinstance(actions, list):
                interaction_metadata["quick_actions_offered"] = [
                    str(a.get("id")) for a in actions if isinstance(a, dict) and a.get("id")
                ]
        if message.is_audio:
            interaction_metadata["input_type"] = "audio"
        if guard_result.injection_suspected:
            interaction_metadata["injection_suspected"] = True
            interaction_metadata["injection_patterns"] = guard_result.matched_patterns
        if output_check.was_modified:
            interaction_metadata["output_guard_modified"] = True
            interaction_metadata["output_guard_leak"] = output_check.leak_detected
        interaction_id = await interaction_logger.log(
            tenant_id=tenant_id,
            user_phone=message.phone,
            user_name=user_name,
            message_in=message.text,
            message_out=response_text,
            agent_used=result.agent_used,
            sub_agent_used=result.sub_agent_used,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            response_time_ms=response_time_ms,
            metadata=interaction_metadata,
        )

        logger.info(
            "Message processed successfully",
            phone=message.phone,
            tenant_id=tenant_id,
            agent_used=result.agent_used,
            response_time_ms=response_time_ms,
            interaction_id=interaction_id,
        )

        # Clear pending edit context when update/delete completes successfully.
        if isinstance(result.metadata, dict):
            tool_name = result.metadata.get("tool")
            tool_result = result.metadata.get("result")
            is_success = bool(tool_result and isinstance(tool_result, dict) and tool_result.get("success"))
            if is_success and tool_name in ("modificar_gasto", "eliminar_gasto"):
                clear_pending_expense_edit(message.phone)

        # Run QA analysis asynchronously (fire and forget)
        if interaction_id:
            asyncio.create_task(
                _run_qa_analysis(
                    interaction_id=interaction_id,
                    tenant_id=tenant_id,
                    user_phone=message.phone,
                    message_in=message.text,
                    message_out=response_text,
                    agent_name=result.sub_agent_used or result.agent_used,
                    metadata=interaction_metadata,
                )
            )

    except Exception as e:
        logger.error(
            "Error processing message",
            phone=message.phone,
            error=str(e),
            tenant_id=tenant_id,
        )

        # Log hard error
        if tenant_id:
            await quality_logger.log_hard_error(
                tenant_id=tenant_id,
                category="agent_error",
                error_message=str(e),
                user_phone=message.phone,
                agent_name=agent_used,
                message_in=message.text,
                severity="high",
                exception=e,
            )

        # Send error message to user
        try:
            await whatsapp.send_text(
                message.phone,
                "Hubo un problema procesando tu mensaje. Por favor, intentá de nuevo en unos segundos.",
            )
        except Exception:
            logger.error("Failed to send error message to user")


async def _handle_setup_user(
    message: IncomingMessage,
    phone_info,
    whatsapp,
) -> None:
    """Redirect users with pending setup to web onboarding.

    Sends a single message with link to complete home configuration on the web.
    No SubscriptionAgent or conversational setup.
    """
    try:
        settings = get_settings()
        url = f"{settings.frontend_url.rstrip('/')}/onboarding"
        text = (
            f"Tu pago está confirmado. Completá la configuración de tu hogar acá: {url} "
            "Cuando termines, volvé a escribirme."
        )
        await whatsapp.send_text(message.phone, text)

        interaction_logger = InteractionLogger()
        await interaction_logger.log(
            tenant_id=phone_info.tenant_id,
            user_phone=message.phone,
            user_name=phone_info.user_name or message.contact_name,
            message_in=message.text or "",
            message_out=text,
            agent_used="web_onboarding_redirect",
            tokens_in=0,
            tokens_out=0,
            response_time_ms=0,
            metadata={"mode": "setup_redirect"},
        )
        logger.info(
            "Setup-pending user redirected to web onboarding",
            phone=message.phone,
            tenant_id=phone_info.tenant_id,
        )
    except Exception as e:
        logger.error(
            "Error redirecting setup user to web",
            phone=message.phone,
            error=str(e),
        )
        try:
            await whatsapp.send_text(
                message.phone,
                "Hubo un problema. Por favor, intentá de nuevo en unos segundos.",
            )
        except Exception:
            logger.error("Failed to send error message to setup user")


async def _handle_unregistered_user(
    message: IncomingMessage,
    whatsapp,
) -> None:
    """Redirect unregistered users to web onboarding.

    Calls backend to get a signed onboarding URL, then sends a single message
    with the link. No SubscriptionAgent or conversational onboarding.
    """
    try:
        # Backend expects E.164 with leading '+' (WebLinkRequest validator)
        phone_e164 = message.phone if message.phone.startswith("+") else f"+{message.phone}"
        backend = get_backend_client()
        response = await backend.post(
            "/api/v1/onboarding/web-link",
            json={"phone": phone_e164},
            timeout=15.0,
        )
        data = response.json() if response.content else {}

        if response.status_code == 200 and data.get("url"):
            name = (message.contact_name or "").strip() or None
            greeting = f"Hola {name}! 👋" if name else "Hola! 👋"
            text = (
                f"{greeting} HomeAI pone tu hogar en un solo lugar: gastos, agenda, "
                "listas y recordatorios, todo por WhatsApp. Sin apps ni planillas.\n\n"
                f"Para activarlo, completá tu registro acá: {data['url']}\n\n"
                "Cuando termines, volvé a escribirme."
            )
        elif data.get("already_registered"):
            text = "Este número ya está registrado en otro hogar. Si tenés problemas para ingresar, contactá soporte."
        else:
            text = "Algo falló; intentá más tarde o contactá soporte."

        await whatsapp.send_text(message.phone, text)

        # Log for admin visibility (no tenant)
        interaction_logger = InteractionLogger()
        await interaction_logger.log(
            tenant_id=None,
            user_phone=message.phone,
            user_name=message.contact_name,
            message_in=message.text or "",
            message_out=text,
            agent_used="web_onboarding_redirect",
            tokens_in=0,
            tokens_out=0,
            response_time_ms=0,
            metadata={"mode": "unregistered_redirect"},
        )
        logger.info("Unregistered user redirected to web onboarding", phone=message.phone)

    except Exception as e:
        logger.error(
            "Error redirecting unregistered user to web",
            phone=message.phone,
            error=str(e),
        )
        try:
            await whatsapp.send_text(
                message.phone,
                "Hubo un problema. Por favor, intentá de nuevo en unos segundos.",
            )
        except Exception:
            logger.error("Failed to send error message to unregistered user")


async def _run_qa_analysis(
    interaction_id: str,
    tenant_id: str | None,
    user_phone: str,
    message_in: str,
    message_out: str,
    agent_name: str,
    metadata: dict | None,
) -> None:
    """Run QA analysis on an interaction (fire and forget).

    This runs asynchronously after the response is sent to avoid adding latency.
    """
    try:
        qa_agent = get_qa_agent()
        quality_logger = get_quality_logger()

        # Extract tool info from metadata
        tool_name = None
        tool_result = None
        if metadata:
            tool_name = metadata.get("tool")
            tool_result = metadata.get("result")

        # Check if we should analyze (always analyze if tool failed)
        if not qa_agent.should_analyze(tool_result, sample_rate=1.0):
            return

        # Run analysis
        analysis = await qa_agent.analyze(
            message_in=message_in,
            message_out=message_out,
            agent_name=agent_name,
            tenant_id=tenant_id,
            tool_name=tool_name,
            tool_result=tool_result,
        )

        # Log if issue found
        if analysis.has_issue and analysis.category:
            await quality_logger.log_soft_error(
                tenant_id=tenant_id,
                interaction_id=interaction_id,
                category=analysis.category,
                qa_analysis=analysis.explanation or "No explanation provided",
                qa_suggestion=analysis.suggestion or "No suggestion provided",
                qa_confidence=analysis.confidence,
                user_phone=user_phone,
                agent_name=agent_name,
                tool_name=tool_name,
                message_in=message_in,
                message_out=message_out,
            )

            logger.info(
                "QA issue detected",
                interaction_id=interaction_id,
                category=analysis.category,
                confidence=analysis.confidence,
            )

    except Exception as e:
        # Never fail silently, but don't crash either
        logger.error(
            "QA analysis failed",
            interaction_id=interaction_id,
            error=str(e),
        )
