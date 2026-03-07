"""Message Guards -- composable pre-LLM validation checks.

Each guard checks one condition and returns a GuardResult.
Guards are run sequentially; the first block stops processing.

Guards implemented:
  - check_rate_limit: replaces in-memory rate limiter in webhook.py
  - check_subscription_active: user must have active subscription
  - check_onboarding_complete: user must have completed setup

Usage:
    result = await run_guards(phone_info, phone, settings)
    if result and result.should_block:
        # handle block
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class GuardResult:
    """Result from a single guard check."""

    should_block: bool
    guard_name: str
    reason: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


# In-memory rate limit store (same as before, kept for non-Redis environments)
_rate_limit_store: dict[str, list[float]] = {}


async def check_rate_limit(phone: str, max_per_minute: int = 20) -> GuardResult:
    """Rate limit check -- max N messages per minute per phone."""
    now = time.time()
    window = 60.0

    timestamps = _rate_limit_store.get(phone, [])
    timestamps = [t for t in timestamps if now - t < window]
    timestamps.append(now)
    _rate_limit_store[phone] = timestamps

    if len(timestamps) > max_per_minute:
        return GuardResult(
            should_block=True,
            guard_name="rate_limit",
            reason="rate_limited",
            metadata={"count": len(timestamps), "max": max_per_minute},
        )
    return GuardResult(should_block=False, guard_name="rate_limit")


async def check_subscription_active(phone_info: Any) -> GuardResult:
    """Block if user does not have an active subscription."""
    if not phone_info:
        return GuardResult(should_block=False, guard_name="subscription")

    if not phone_info.has_active_subscription:
        return GuardResult(
            should_block=True,
            guard_name="subscription",
            reason="subscription_required",
            metadata={"status": phone_info.subscription_status},
        )
    return GuardResult(should_block=False, guard_name="subscription")


async def check_onboarding_complete(phone_info: Any) -> GuardResult:
    """Block if user has not completed onboarding setup."""
    if not phone_info:
        return GuardResult(should_block=False, guard_name="onboarding")

    if not phone_info.onboarding_completed:
        return GuardResult(
            should_block=True,
            guard_name="onboarding",
            reason="setup_pending",
        )
    return GuardResult(should_block=False, guard_name="onboarding")


async def check_registered(phone_info: Any) -> GuardResult:
    """Block if user is not registered at all."""
    if not phone_info:
        return GuardResult(
            should_block=True,
            guard_name="registration",
            reason="unregistered",
        )
    return GuardResult(should_block=False, guard_name="registration")


async def run_guards(
    phone_info: Any,
    phone: str,
    max_per_minute: int = 20,
) -> Optional[GuardResult]:
    """Run all guards in order. Returns the first blocking result, or None.

    Order matters: rate limit first (cheapest), then registration, then
    onboarding, then subscription.
    """
    guards = [
        await check_rate_limit(phone, max_per_minute),
        await check_registered(phone_info),
        await check_onboarding_complete(phone_info),
        await check_subscription_active(phone_info),
    ]

    for result in guards:
        if result.should_block:
            logger.info(
                "Message blocked by guard",
                guard=result.guard_name,
                reason=result.reason,
                phone=phone[-4:],  # Only log last 4 digits
            )
            return result

    return None
