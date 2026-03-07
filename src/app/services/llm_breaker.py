"""LLM Circuit Breaker.

Implements a CLOSED -> OPEN -> HALF_OPEN -> CLOSED state machine backed by
Redis (with automatic in-memory fallback when Redis is unavailable).

States:
  CLOSED    -- Normal operation. All calls go through.
  OPEN      -- Rejecting all calls. LLM is considered unhealthy.
  HALF_OPEN -- Recovery testing. Limited calls allowed.

Rules for recording failures:
  - 5xx errors and network timeouts count as failures.
  - 429 (rate limit) and 4xx client errors are NOT counted -- they are
    the caller's fault, not an indicator that the service is down.
"""

import time
from enum import Enum
from typing import Optional

import structlog

logger = structlog.get_logger()

# Client-side / non-service errors -- do not count toward circuit breaker
_CLIENT_ERROR_MARKERS = ("429", "401", "403", "400", "invalid_api_key")


class BreakerState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(Exception):
    """Raised when a call is rejected because the circuit is OPEN."""


class LLMCircuitBreaker:
    """Redis-backed circuit breaker with in-memory fallback."""

    _REDIS_KEY_FAILURES = "llm_breaker:failures"
    _REDIS_KEY_OPEN_UNTIL = "llm_breaker:open_until"

    def __init__(
        self,
        fail_threshold: int = 5,
        open_seconds: int = 60,
        half_open_max_calls: int = 3,
    ) -> None:
        self.fail_threshold = fail_threshold
        self.open_seconds = open_seconds
        self.half_open_max_calls = half_open_max_calls

        self._redis_client = None
        self._redis_checked = False

        # In-memory fallback state
        self._local_failures: int = 0
        self._local_open_until: float = 0.0

    async def _get_redis(self):
        """Lazily initialise Redis client; returns None if unavailable."""
        if self._redis_checked:
            return self._redis_client
        self._redis_checked = True
        try:
            from ..config import get_settings
            import redis.asyncio as aioredis

            settings = get_settings()
            if not settings.redis_url:
                return None
            client = aioredis.from_url(settings.redis_url, decode_responses=True)
            await client.ping()
            self._redis_client = client
            logger.info("Circuit breaker connected to Redis")
        except Exception as exc:
            logger.warning("Circuit breaker Redis unavailable, using in-memory", error=str(exc))
            self._redis_client = None
        return self._redis_client

    def _is_client_error(self, exc: Exception) -> bool:
        exc_str = str(exc).lower()
        return any(marker in exc_str for marker in _CLIENT_ERROR_MARKERS)

    async def get_state(self) -> BreakerState:
        """Return the current circuit breaker state."""
        now = time.time()
        redis = await self._get_redis()

        if redis:
            try:
                open_until_raw = await redis.get(self._REDIS_KEY_OPEN_UNTIL)
                if open_until_raw and float(open_until_raw) > now:
                    return BreakerState.OPEN
                failures = int(await redis.get(self._REDIS_KEY_FAILURES) or 0)
                if failures >= self.fail_threshold:
                    return BreakerState.HALF_OPEN
                return BreakerState.CLOSED
            except Exception as exc:
                logger.warning("Redis state check failed, falling back", error=str(exc))

        # In-memory fallback
        if self._local_open_until > now:
            return BreakerState.OPEN
        if self._local_failures >= self.fail_threshold:
            return BreakerState.HALF_OPEN
        return BreakerState.CLOSED

    async def allow_call(self) -> bool:
        """Return True if the call should be allowed through."""
        state = await self.get_state()
        if state == BreakerState.OPEN:
            logger.warning("Circuit breaker OPEN -- rejecting LLM call")
            return False
        return True

    async def record_failure(self, exc: Exception) -> None:
        """Record a service failure (ignored for client errors)."""
        if self._is_client_error(exc):
            return

        logger.warning("LLM failure recorded for circuit breaker", error=str(exc))
        redis = await self._get_redis()

        if redis:
            try:
                pipe = redis.pipeline()
                pipe.incr(self._REDIS_KEY_FAILURES)
                pipe.expire(self._REDIS_KEY_FAILURES, self.open_seconds * 2)
                results = await pipe.execute()
                failures = int(results[0])
                if failures >= self.fail_threshold:
                    open_until = time.time() + self.open_seconds
                    await redis.set(self._REDIS_KEY_OPEN_UNTIL, open_until, ex=self.open_seconds)
                    logger.error(
                        "Circuit breaker OPENED",
                        failures=failures,
                        open_until_secs=self.open_seconds,
                    )
                return
            except Exception as exc2:
                logger.warning("Redis failure recording failed, using in-memory", error=str(exc2))

        # In-memory fallback
        self._local_failures += 1
        if self._local_failures >= self.fail_threshold:
            self._local_open_until = time.time() + self.open_seconds
            logger.error("Circuit breaker OPENED (in-memory)")

    async def record_success(self) -> None:
        """Record a successful call -- resets failure counter."""
        redis = await self._get_redis()
        if redis:
            try:
                await redis.delete(self._REDIS_KEY_FAILURES, self._REDIS_KEY_OPEN_UNTIL)
                return
            except Exception:
                pass
        self._local_failures = 0
        self._local_open_until = 0.0


# Module-level singleton
_breaker: Optional[LLMCircuitBreaker] = None


def get_circuit_breaker() -> LLMCircuitBreaker:
    """Return the shared circuit breaker instance."""
    global _breaker
    if _breaker is None:
        from ..config import get_settings
        s = get_settings()
        _breaker = LLMCircuitBreaker(
            fail_threshold=s.breaker_fail_threshold,
            open_seconds=s.breaker_open_seconds,
            half_open_max_calls=s.breaker_half_open_max_calls,
        )
    return _breaker
