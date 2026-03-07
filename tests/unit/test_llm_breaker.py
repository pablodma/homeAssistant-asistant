"""Unit tests for LLM circuit breaker."""

import pytest
from unittest.mock import AsyncMock, patch

from src.app.services.llm_breaker import LLMCircuitBreaker, BreakerState


@pytest.fixture
def breaker():
    return LLMCircuitBreaker(fail_threshold=3, open_seconds=10, half_open_max_calls=2)


@pytest.mark.asyncio
async def test_breaker_starts_closed(breaker):
    assert await breaker.get_state() == BreakerState.CLOSED
    assert await breaker.allow_call() is True


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold(breaker):
    # Record enough failures to trip the breaker
    for _ in range(3):
        await breaker.record_failure(Exception("Server error 500"))
    assert await breaker.get_state() == BreakerState.OPEN
    assert await breaker.allow_call() is False


@pytest.mark.asyncio
async def test_breaker_ignores_client_errors(breaker):
    # 429 and 401 should NOT count toward the threshold
    for _ in range(10):
        await breaker.record_failure(Exception("429 Too Many Requests"))
    assert await breaker.get_state() == BreakerState.CLOSED


@pytest.mark.asyncio
async def test_breaker_resets_on_success(breaker):
    await breaker.record_failure(Exception("500 Server error"))
    await breaker.record_failure(Exception("500 Server error"))
    await breaker.record_success()
    assert await breaker.get_state() == BreakerState.CLOSED
    assert breaker._local_failures == 0


@pytest.mark.asyncio
async def test_breaker_half_open_state(breaker):
    # Set failures to threshold (without opening time)
    breaker._local_failures = 3
    breaker._local_open_until = 0.0  # Not in open time window
    assert await breaker.get_state() == BreakerState.HALF_OPEN
