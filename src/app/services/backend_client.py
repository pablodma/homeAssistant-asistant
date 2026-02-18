"""Centralized HTTP client for backend API calls.

All bot-to-backend communication should go through this client to ensure
the Authorization header is always included. This prevents auth bugs
like missing service tokens on protected endpoints.
"""

from typing import Any

import httpx
import structlog

from ..config import get_settings
from ..config.settings import Settings

logger = structlog.get_logger()


class BackendClient:
    """HTTP client for the HomeAI backend API.

    Automatically injects the service token (Bearer) on every request.
    All agents and services should use this instead of raw httpx.AsyncClient.
    """

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.backend_api_url
        self._headers = {"Authorization": f"Bearer {settings.backend_api_key}"}

    async def get(
        self, path: str, *, timeout: float = 30.0, **kwargs: Any
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(
                f"{self._base_url}{path}", headers=self._headers, **kwargs
            )

    async def post(
        self, path: str, *, timeout: float = 30.0, **kwargs: Any
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(
                f"{self._base_url}{path}", headers=self._headers, **kwargs
            )

    async def put(
        self, path: str, *, timeout: float = 30.0, **kwargs: Any
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.put(
                f"{self._base_url}{path}", headers=self._headers, **kwargs
            )

    async def patch(
        self, path: str, *, timeout: float = 30.0, **kwargs: Any
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.patch(
                f"{self._base_url}{path}", headers=self._headers, **kwargs
            )

    async def delete(
        self, path: str, *, timeout: float = 30.0, **kwargs: Any
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.delete(
                f"{self._base_url}{path}", headers=self._headers, **kwargs
            )

    async def request(
        self, method: str, path: str, *, timeout: float = 30.0, **kwargs: Any
    ) -> httpx.Response:
        """Generic request for methods like DELETE with body."""
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(
                method, f"{self._base_url}{path}", headers=self._headers, **kwargs
            )


_client: BackendClient | None = None


def get_backend_client() -> BackendClient:
    """Get or create the singleton BackendClient."""
    global _client
    if _client is None:
        _client = BackendClient(get_settings())
    return _client
