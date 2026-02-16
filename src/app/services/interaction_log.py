"""Interaction logging service."""

from typing import Any, Optional

import structlog

from ..config.database import get_pool

logger = structlog.get_logger()


class InteractionLogger:
    """Service for logging agent interactions."""

    async def log(
        self,
        tenant_id: Optional[str],
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
    ) -> Optional[str]:
        """Log an interaction to the database.

        Args:
            tenant_id: The tenant ID. None for acquisition-mode (unregistered users).
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

        Returns:
            The interaction ID if successful, None otherwise.
        """
        try:
            pool = await get_pool()

            # Convert empty string tenant_id to None (DB expects UUID or NULL)
            effective_tenant_id = tenant_id if tenant_id else None

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
                RETURNING id
            """

            import json
            metadata_json = json.dumps(metadata) if metadata else None

            result = await pool.fetchval(
                query,
                effective_tenant_id,
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
                interaction_id=str(result),
                tenant_id=tenant_id,
                user_phone=user_phone,
                agent_used=agent_used,
            )

            return str(result) if result else None

        except Exception as e:
            # Don't fail the main flow if logging fails
            logger.error(
                "Failed to log interaction",
                error=str(e),
                tenant_id=tenant_id,
                user_phone=user_phone,
            )
            return None
