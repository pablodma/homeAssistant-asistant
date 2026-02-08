"""Interaction logging service."""

from typing import Any, Optional

import structlog

from ..config.database import get_pool

logger = structlog.get_logger()


class InteractionLogger:
    """Service for logging agent interactions."""

    async def log(
        self,
        tenant_id: str,
        user_phone: str,
        message_in: str,
        message_out: str,
        agent_used: str,
        user_name: Optional[str] = None,
        sub_agent_used: Optional[str] = None,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        response_time_ms: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Log an interaction to the database.

        Args:
            tenant_id: The tenant ID.
            user_phone: User's phone number.
            message_in: The user's message.
            message_out: The bot's response.
            agent_used: Which agent processed the message.
            user_name: User's name if available.
            sub_agent_used: Sub-agent if delegation occurred.
            tokens_in: Input tokens used.
            tokens_out: Output tokens used.
            response_time_ms: Response time in milliseconds.
            metadata: Additional metadata.
        """
        try:
            pool = await get_pool()

            query = """
                INSERT INTO agent_interactions (
                    tenant_id,
                    user_phone,
                    user_name,
                    message_in,
                    message_out,
                    agent_used,
                    sub_agent_used,
                    tokens_in,
                    tokens_out,
                    response_time_ms,
                    metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """

            import json
            metadata_json = json.dumps(metadata) if metadata else None

            await pool.execute(
                query,
                tenant_id,
                user_phone,
                user_name,
                message_in,
                message_out,
                agent_used,
                sub_agent_used,
                tokens_in,
                tokens_out,
                response_time_ms,
                metadata_json,
            )

            logger.debug(
                "Interaction logged",
                tenant_id=tenant_id,
                user_phone=user_phone,
                agent_used=agent_used,
            )

        except Exception as e:
            # Don't fail the main flow if logging fails
            logger.error(
                "Failed to log interaction",
                error=str(e),
                tenant_id=tenant_id,
                user_phone=user_phone,
            )
