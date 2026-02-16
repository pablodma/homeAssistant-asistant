"""Quality Logger - Unified service for logging hard and soft errors."""

import traceback
from typing import Any, Optional
from uuid import uuid4

import structlog

from ..config.database import get_pool

logger = structlog.get_logger()


class QualityLogger:
    """Unified service for logging quality issues (hard and soft errors).

    Hard errors: Technical exceptions, API failures, timeouts
    Soft errors: QA Agent detected issues (misinterpretations, hallucinations, etc.)
    """

    async def log_hard_error(
        self,
        tenant_id: Optional[str],
        category: str,
        error_message: str,
        interaction_id: Optional[str] = None,
        user_phone: Optional[str] = None,
        agent_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        message_in: Optional[str] = None,
        message_out: Optional[str] = None,
        error_code: Optional[str] = None,
        severity: str = "medium",
        request_payload: Optional[dict[str, Any]] = None,
        exception: Optional[Exception] = None,
    ) -> Optional[str]:
        """Log a hard error (technical failure).

        Args:
            tenant_id: The tenant ID.
            category: Error category (api_error, llm_error, timeout, database_error, webhook_error).
            error_message: Human-readable error message.
            interaction_id: Related interaction ID if available.
            user_phone: User's phone number.
            agent_name: Which agent was processing.
            tool_name: Which tool was executing.
            message_in: User's original message.
            message_out: Bot's response (if any).
            error_code: Error code (e.g., HTTP status).
            severity: low, medium, high, critical.
            request_payload: Request data (sanitized).
            exception: The exception object for stack trace.

        Returns:
            The issue ID if logged successfully, None otherwise.
        """
        correlation_id = str(uuid4())[:8]
        stack_trace = None

        if exception:
            stack_trace = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))

        # Log to structlog (appears in Railway)
        logger.error(
            "hard_error",
            correlation_id=correlation_id,
            category=category,
            error_message=error_message,
            tenant_id=tenant_id,
            user_phone=user_phone,
            agent_name=agent_name,
            tool_name=tool_name,
            error_code=error_code,
            severity=severity,
        )

        return await self._persist_issue(
            tenant_id=tenant_id,
            issue_type="hard_error",
            issue_category=category,
            error_message=error_message,
            interaction_id=interaction_id,
            user_phone=user_phone,
            agent_name=agent_name,
            tool_name=tool_name,
            message_in=message_in,
            message_out=message_out,
            error_code=error_code,
            severity=severity,
            request_payload=request_payload,
            stack_trace=stack_trace,
            correlation_id=correlation_id,
        )

    async def log_soft_error(
        self,
        tenant_id: Optional[str],
        interaction_id: str,
        category: str,
        qa_analysis: str,
        qa_suggestion: str,
        qa_confidence: float,
        user_phone: Optional[str] = None,
        agent_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        message_in: Optional[str] = None,
        message_out: Optional[str] = None,
        severity: str = "medium",
    ) -> Optional[str]:
        """Log a soft error (QA detected quality issue).

        Args:
            tenant_id: The tenant ID.
            interaction_id: The interaction being analyzed.
            category: Issue category (misinterpretation, hallucination, unsupported_case, incomplete_response).
            qa_analysis: QA Agent's explanation of the problem.
            qa_suggestion: QA Agent's suggested improvement.
            qa_confidence: QA Agent's confidence score (0.0-1.0).
            user_phone: User's phone number.
            agent_name: Which agent processed the message.
            tool_name: Which tool was used.
            message_in: User's original message.
            message_out: Bot's response.
            severity: low, medium, high, critical.

        Returns:
            The issue ID if logged successfully, None otherwise.
        """
        correlation_id = str(uuid4())[:8]

        # Determine severity based on category
        if category == "hallucination":
            severity = "high"  # Hallucinations are serious
        elif category == "misinterpretation":
            severity = "medium"

        # Log to structlog (appears in Railway)
        logger.warning(
            "soft_error",
            correlation_id=correlation_id,
            category=category,
            qa_analysis=qa_analysis[:200] if qa_analysis else None,
            qa_confidence=qa_confidence,
            tenant_id=tenant_id,
            user_phone=user_phone,
            agent_name=agent_name,
        )

        return await self._persist_issue(
            tenant_id=tenant_id,
            issue_type="soft_error",
            issue_category=category,
            error_message=qa_analysis,  # Use analysis as error message
            interaction_id=interaction_id,
            user_phone=user_phone,
            agent_name=agent_name,
            tool_name=tool_name,
            message_in=message_in,
            message_out=message_out,
            severity=severity,
            qa_analysis=qa_analysis,
            qa_suggestion=qa_suggestion,
            qa_confidence=qa_confidence,
            correlation_id=correlation_id,
        )

    async def _persist_issue(
        self,
        tenant_id: Optional[str],
        issue_type: str,
        issue_category: str,
        error_message: str,
        interaction_id: Optional[str] = None,
        user_phone: Optional[str] = None,
        agent_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        message_in: Optional[str] = None,
        message_out: Optional[str] = None,
        error_code: Optional[str] = None,
        severity: str = "medium",
        request_payload: Optional[dict[str, Any]] = None,
        stack_trace: Optional[str] = None,
        qa_analysis: Optional[str] = None,
        qa_suggestion: Optional[str] = None,
        qa_confidence: Optional[float] = None,
        correlation_id: Optional[str] = None,
    ) -> Optional[str]:
        """Persist a quality issue to the database.

        Returns:
            The issue ID if successful, None otherwise.
        """
        try:
            pool = await get_pool()

            query = """
                INSERT INTO quality_issues (
                    tenant_id,
                    interaction_id,
                    issue_type,
                    issue_category,
                    user_phone,
                    agent_name,
                    tool_name,
                    message_in,
                    message_out,
                    error_code,
                    error_message,
                    severity,
                    qa_analysis,
                    qa_suggestion,
                    qa_confidence,
                    request_payload,
                    stack_trace,
                    correlation_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
                RETURNING id
            """

            import json
            payload_json = json.dumps(request_payload) if request_payload else None

            result = await pool.fetchval(
                query,
                tenant_id,
                interaction_id,
                issue_type,
                issue_category,
                user_phone,
                agent_name,
                tool_name,
                message_in,
                message_out,
                error_code,
                error_message,
                severity,
                qa_analysis,
                qa_suggestion,
                qa_confidence,
                payload_json,
                stack_trace,
                correlation_id,
            )

            logger.debug(
                "Quality issue persisted",
                issue_id=str(result),
                issue_type=issue_type,
                category=issue_category,
            )

            return str(result) if result else None

        except Exception as e:
            # Don't fail the main flow if logging fails
            logger.error(
                "Failed to persist quality issue",
                error=str(e),
                issue_type=issue_type,
                category=issue_category,
            )
            return None


# Singleton instance
_quality_logger: Optional[QualityLogger] = None


def get_quality_logger() -> QualityLogger:
    """Get the singleton QualityLogger instance."""
    global _quality_logger
    if _quality_logger is None:
        _quality_logger = QualityLogger()
    return _quality_logger
