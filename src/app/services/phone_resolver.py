"""Phone resolver service for multitenancy."""

import time
from dataclasses import dataclass
from typing import Optional

import httpx
import structlog

from .backend_client import get_backend_client

logger = structlog.get_logger()

# Cache TTL: re-query backend after this many seconds so deleted users
# (e.g. after DB wipe) are detected without restarting the bot.
CACHE_TTL_SECONDS = 300  # 5 minutes


@dataclass
class PhoneTenantInfo:
    """Information about a phone's associated tenant."""

    tenant_id: str
    user_name: Optional[str] = None
    home_name: Optional[str] = None
    onboarding_completed: bool = True
    is_registered: bool = True
    tenant_active: bool = True
    subscription_status: Optional[str] = None
    has_active_subscription: bool = False
    can_access_dashboard: bool = False
    can_interact_agent: bool = False
    next_step: str = "register"


class PhoneResolver:
    """
    Service to resolve phone numbers to tenant IDs.

    This service queries the backend API to find which tenant
    a phone number belongs to, enabling multitenancy for the bot.
    Cache has a short TTL so that DB changes (e.g. user/tenant deleted)
    are picked up without restarting the bot.
    """

    def __init__(self) -> None:
        """Initialize the phone resolver."""
        self._cache: dict[str, tuple[PhoneTenantInfo, float]] = {}  # phone -> (info, cached_at)

    async def resolve(self, phone: str) -> PhoneTenantInfo | None:
        """
        Resolve a phone number to its tenant information.

        Args:
            phone: Phone number in E.164 format (+5491112345678)

        Returns:
            PhoneTenantInfo if found, None if phone is not registered
        """
        now = time.monotonic()
        if phone in self._cache:
            info, cached_at = self._cache[phone]
            if now - cached_at < CACHE_TTL_SECONDS:
                logger.debug("phone_cache_hit", phone=phone)
                return info
            # TTL expired, drop entry and re-query
            del self._cache[phone]

        result = await self._lookup_phone(phone)

        if result is not None:
            self._cache[phone] = (result, time.monotonic())
        else:
            # Backend says not registered: ensure no stale positive cache
            self.invalidate_cache(phone)

        return result
    
    async def _lookup_phone(self, phone: str) -> PhoneTenantInfo | None:
        """
        Query the backend API for phone access status.
        
        Args:
            phone: Phone number to look up
            
        Returns:
            PhoneTenantInfo if found, None otherwise
        """
        backend = get_backend_client()
        
        logger.debug(
            "phone_lookup_starting",
            phone=phone,
        )
        
        try:
            response = await backend.get("/api/v1/access/status-by-phone", params={"phone": phone})
            
            logger.debug(
                "phone_lookup_response",
                phone=phone,
                status_code=response.status_code,
            )
            
            if response.status_code != 200:
                logger.warning(
                    "phone_lookup_failed",
                    phone=phone,
                    status=response.status_code,
                )
                return None
            
            data = response.json()
            
            if not data.get("tenant_id"):
                logger.info("phone_not_registered", phone=phone)
                return None
            
            logger.info(
                "phone_resolved",
                phone=phone,
                tenant_id=data.get("tenant_id"),
                home_name=data.get("home_name"),
            )
            
            return PhoneTenantInfo(
                tenant_id=data["tenant_id"],
                user_name=data.get("user_name"),
                home_name=data.get("home_name"),
                onboarding_completed=data.get("onboarding_completed", True),
                is_registered=data.get("is_registered", True),
                tenant_active=data.get("tenant_active", True),
                subscription_status=data.get("subscription_status"),
                has_active_subscription=data.get("has_active_subscription", False),
                can_access_dashboard=data.get("can_access_dashboard", False),
                can_interact_agent=data.get("can_interact_agent", False),
                next_step=data.get("next_step", "register"),
            )
                
        except httpx.RequestError as e:
            logger.error(
                "phone_lookup_error",
                phone=phone,
                error=str(e),
                error_type=type(e).__name__,
            )
            return None
        except Exception as e:
            logger.error(
                "phone_lookup_unexpected_error",
                phone=phone,
                error=str(e),
                error_type=type(e).__name__,
            )
            return None
    
    def invalidate_cache(self, phone: str) -> None:
        """Remove a phone from the cache (e.g. after setup completed or backend says not found)."""
        if phone in self._cache:
            del self._cache[phone]
            logger.debug("phone_cache_invalidated", phone=phone)
    
    def clear_cache(self) -> None:
        """Clear all cached phone mappings."""
        self._cache.clear()
        logger.info("phone_cache_cleared")


_resolver: PhoneResolver | None = None


def get_phone_resolver() -> PhoneResolver:
    """Get or create the phone resolver instance."""
    global _resolver
    if _resolver is None:
        _resolver = PhoneResolver()
    return _resolver
