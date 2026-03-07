"""Correlation ID middleware for request tracing."""

import uuid
from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = structlog.get_logger()

# ContextVar so correlation ID is accessible anywhere in the request lifecycle
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Get the current request's correlation ID."""
    return correlation_id_var.get()


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Middleware that injects a correlation ID into every request.

    If the request already carries an `x-correlation-id` header, that value
    is reused; otherwise a new UUID is generated.  The ID is:
    - Stored in a ContextVar so it is available anywhere in the request chain
    - Bound to structlog context for automatic log enrichment
    - Echoed back in the response header
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        correlation_id_var.set(correlation_id)

        # Bind to structlog so all log lines in this request carry the ID
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        try:
            response = await call_next(request)
        finally:
            # Always clear contextvars after request completes
            structlog.contextvars.clear_contextvars()

        response.headers["x-correlation-id"] = correlation_id
        return response
