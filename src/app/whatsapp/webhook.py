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
from ..agents.subscription import SubscriptionAgent
from ..agents.qa import QAAgent
from ..services.conversation import ConversationService
from ..services.interaction_log import InteractionLogger
from ..services.input_guard import sanitize_message
from ..services.output_guard import check_response
from ..services.quality_logger import get_quality_logger
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
    """Handle messages from registered users with pending setup (onboarding_completed=false).

    Invokes the SubscriptionAgent in setup mode to collect home_name
    and optionally invite members after payment.
    """
    try:
        import time

        start_time = time.time()
        tenant_id = phone_info.tenant_id
        conversation_service = ConversationService()
        interaction_logger = InteractionLogger()

        # Get or create session with real tenant_id
        await conversation_service.get_or_create(
            phone=message.phone,
            tenant_id=tenant_id,
        )

        # Load conversation history
        history = await conversation_service.get_history(
            phone=message.phone,
            tenant_id=tenant_id,
            limit=10,
        )
        # #region agent log
        import json as _dbg_json; open(r"d:\Proyectos\homeAsiss\.cursor\debug.log", "a", encoding="utf-8").write(_dbg_json.dumps({"timestamp": __import__("time").time(), "location": "webhook.py:432", "message": "setup_history", "data": {"phone": message.phone, "tenant_id": tenant_id, "history_count": len(history), "history_preview": [{"role": h.role, "content": h.content[:60]} for h in history[-3:]]}, "hypothesisId": "H4"}) + "\n")
        # #endregion

        # Process through SubscriptionAgent in setup mode
        subscription_agent = SubscriptionAgent()
        result = await subscription_agent.process(
            message=message.text,
            phone=message.phone,
            tenant_id=tenant_id,
            history=history,
            mode="setup",
            contact_name=message.contact_name,
        )

        # Send response
        await whatsapp.send_text(message.phone, result.response)

        # Save conversation history
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
            content=result.response,
        )

        # Log interaction
        response_time_ms = int((time.time() - start_time) * 1000)
        interaction_metadata = result.metadata or {}
        interaction_metadata["mode"] = "setup"
        interaction_id = await interaction_logger.log(
            tenant_id=tenant_id,
            user_phone=message.phone,
            user_name=phone_info.user_name or message.contact_name,
            message_in=message.text,
            message_out=result.response,
            agent_used=result.agent_used,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            response_time_ms=response_time_ms,
            metadata=interaction_metadata,
        )

        logger.info(
            "Setup user message processed",
            phone=message.phone,
            tenant_id=tenant_id,
            agent_used=result.agent_used,
            response_time_ms=response_time_ms,
        )

        # Run QA analysis
        if interaction_id:
            asyncio.create_task(
                _run_qa_analysis(
                    interaction_id=interaction_id,
                    tenant_id=tenant_id,
                    user_phone=message.phone,
                    message_in=message.text,
                    message_out=result.response,
                    agent_name=result.sub_agent_used or result.agent_used,
                    metadata=interaction_metadata,
                )
            )

    except Exception as e:
        logger.error(
            "Error handling setup user",
            phone=message.phone,
            error=str(e),
        )

        quality_logger = get_quality_logger()
        await quality_logger.log_hard_error(
            tenant_id=phone_info.tenant_id,
            category="agent_error",
            error_message=str(e),
            user_phone=message.phone,
            agent_name="subscription",
            message_in=message.text,
            severity="high",
            exception=e,
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
    """Handle messages from unregistered users via SubscriptionAgent.

    Invokes the SubscriptionAgent in acquisition mode (no tenant_id).
    Manages conversation memory keyed by phone number.
    Logs interaction for admin visibility.
    Runs QA analysis so errors are visible in the admin panel.
    """
    try:
        import time

        start_time = time.time()
        conversation_service = ConversationService()
        interaction_logger = InteractionLogger()
        quality_logger = get_quality_logger()

        # Get or create onboarding session (no tenant_id)
        await conversation_service.get_or_create(
            phone=message.phone,
            tenant_id="",
        )

        # Load conversation history for onboarding
        history = await conversation_service.get_history(
            phone=message.phone,
            tenant_id="",
            limit=10,
        )

        # Process through SubscriptionAgent in acquisition mode
        subscription_agent = SubscriptionAgent()
        result = await subscription_agent.process(
            message=message.text,
            phone=message.phone,
            tenant_id="",  # Empty = acquisition mode
            history=history,
            contact_name=message.contact_name,
        )

        # Send response
        await whatsapp.send_text(message.phone, result.response)

        # Save conversation history
        await conversation_service.add_message(
            phone=message.phone,
            tenant_id="",
            role="user",
            content=message.text,
        )
        await conversation_service.add_message(
            phone=message.phone,
            tenant_id="",
            role="assistant",
            content=result.response,
        )

        # Log interaction for admin panel visibility
        response_time_ms = int((time.time() - start_time) * 1000)
        interaction_metadata = result.metadata or {}
        interaction_metadata["mode"] = "acquisition"
        interaction_id = await interaction_logger.log(
            tenant_id=None,  # No tenant in acquisition mode
            user_phone=message.phone,
            user_name=message.contact_name,
            message_in=message.text,
            message_out=result.response,
            agent_used=result.agent_used,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            response_time_ms=response_time_ms,
            metadata=interaction_metadata,
        )

        logger.info(
            "Unregistered user message processed",
            phone=message.phone,
            agent_used=result.agent_used,
            response_time_ms=response_time_ms,
        )

        # Run QA analysis (fire and forget) — None tenant for onboarding
        if interaction_id:
            asyncio.create_task(
                _run_qa_analysis(
                    interaction_id=interaction_id,
                    tenant_id=None,
                    user_phone=message.phone,
                    message_in=message.text,
                    message_out=result.response,
                    agent_name=result.sub_agent_used or result.agent_used,
                    metadata=interaction_metadata,
                )
            )

    except Exception as e:
        logger.error(
            "Error handling unregistered user",
            phone=message.phone,
            error=str(e),
        )

        # Log hard error for admin visibility (None tenant = onboarding)
        quality_logger = get_quality_logger()
        await quality_logger.log_hard_error(
            tenant_id=None,
            category="agent_error",
            error_message=str(e),
            user_phone=message.phone,
            agent_name="subscription",
            message_in=message.text,
            severity="high",
            exception=e,
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
