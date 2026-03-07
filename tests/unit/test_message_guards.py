"""Unit tests for message_guards.py."""

import pytest
from unittest.mock import MagicMock

from src.app.services.message_guards import (
    GuardResult,
    check_rate_limit,
    check_subscription_active,
    check_onboarding_complete,
    check_registered,
    run_guards,
)


@pytest.mark.asyncio
async def test_rate_limit_allows_normal_traffic():
    result = await check_rate_limit("1234567890", max_per_minute=20)
    assert not result.should_block
    assert result.guard_name == "rate_limit"


@pytest.mark.asyncio
async def test_rate_limit_blocks_excessive_traffic():
    phone = "+5491100000001"
    # Flood the rate limiter
    for _ in range(21):
        result = await check_rate_limit(phone, max_per_minute=20)
    assert result.should_block
    assert result.reason == "rate_limited"


@pytest.mark.asyncio
async def test_subscription_blocks_inactive_user():
    phone_info = MagicMock()
    phone_info.has_active_subscription = False
    phone_info.subscription_status = "expired"
    result = await check_subscription_active(phone_info)
    assert result.should_block
    assert result.reason == "subscription_required"


@pytest.mark.asyncio
async def test_subscription_allows_active_user():
    phone_info = MagicMock()
    phone_info.has_active_subscription = True
    result = await check_subscription_active(phone_info)
    assert not result.should_block


@pytest.mark.asyncio
async def test_onboarding_blocks_incomplete_setup():
    phone_info = MagicMock()
    phone_info.onboarding_completed = False
    result = await check_onboarding_complete(phone_info)
    assert result.should_block
    assert result.reason == "setup_pending"


@pytest.mark.asyncio
async def test_registered_blocks_unknown_phone():
    result = await check_registered(None)
    assert result.should_block
    assert result.reason == "unregistered"


@pytest.mark.asyncio
async def test_run_guards_blocks_on_first_failure():
    # None phone_info -> registration guard fires first
    result = await run_guards(phone_info=None, phone="+5491100000002")
    assert result is not None
    assert result.should_block
    assert result.guard_name == "registration"


@pytest.mark.asyncio
async def test_run_guards_passes_valid_user():
    phone_info = MagicMock()
    phone_info.has_active_subscription = True
    phone_info.onboarding_completed = True
    result = await run_guards(phone_info=phone_info, phone="+5491100000099")
    assert result is None  # No block
