"""WhatsApp webhook handler.

Note: All agent decision logic is in prompts, not in this code.
This webhook only routes messages and sends responses.
"""

import asyncio

import structlog
from fastapi import APIRouter, BackgroundTasks, Query, Request, Response

from ..config import get_settings
from ..agents.router import RouterAgent
from ..agents.qa import QAAgent
from ..services.conversation import ConversationService
from ..services.interaction_log import InteractionLogger
from ..services.quality_logger import get_quality_logger
from ..services.phone_resolver import get_phone_resolver
from .client import get_whatsapp_client
from .types import IncomingMessage, WhatsAppWebhookPayload

logger = structlog.get_logger()
router = APIRouter(tags=["WhatsApp Webhook"])

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
        body = await request.json()
        logger.debug("Webhook received", body=body)

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
    settings = get_settings()
    whatsapp = get_whatsapp_client()
    quality_logger = get_quality_logger()

    # Track context for error logging
    tenant_id: str | None = None
    agent_used: str | None = None

    logger.info(
        "Processing message",
        phone=message.phone,
        text=message.text[:50] + "..." if len(message.text) > 50 else message.text,
    )

    try:
        # Mark message as read
        await whatsapp.mark_as_read(message.message_id)

        # Resolve tenant by phone number
        phone_resolver = get_phone_resolver()
        phone_info = await phone_resolver.resolve(message.phone)

        if not phone_info:
            # Phone not registered - send registration message
            logger.info("unregistered_phone", phone=message.phone)
            await whatsapp.send_text(
                message.phone,
                "¡Hola! 👋 Para usar HomeAI necesitás registrar tu número primero.\n\n"
                "Visitá https://home-assistant-frontend-brown.vercel.app para crear tu cuenta y vincular tu WhatsApp.",
            )
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

        # Send response (always text - prompt handles conversational flows)
        await whatsapp.send_text(message.phone, result.response)

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
            content=result.response,
        )

        # Log interaction and get ID for QA
        response_time_ms = int((time.time() - start_time) * 1000)
        interaction_id = await interaction_logger.log(
            tenant_id=tenant_id,
            user_phone=message.phone,
            user_name=user_name,
            message_in=message.text,
            message_out=result.response,
            agent_used=result.agent_used,
            sub_agent_used=result.sub_agent_used,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            response_time_ms=response_time_ms,
            metadata=result.metadata,
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
                    message_out=result.response,
                    agent_name=result.sub_agent_used or result.agent_used,
                    metadata=result.metadata,
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


async def _run_qa_analysis(
    interaction_id: str,
    tenant_id: str,
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
