"""Phone resolver service for multitenancy."""

from dataclasses import dataclass
from typing import Optional

import httpx
import structlog

from ..config import get_settings

logger = structlog.get_logger()


@dataclass
class PhoneTenantInfo:
    """Information about a phone's associated tenant."""
    
    tenant_id: str
    user_name: Optional[str] = None
    home_name: Optional[str] = None


class PhoneResolver:
    """
    Service to resolve phone numbers to tenant IDs.
    
    This service queries the backend API to find which tenant
    a phone number belongs to, enabling multitenancy for the bot.
    """
    
    def __init__(self) -> None:
        """Initialize the phone resolver."""
        self._cache: dict[str, PhoneTenantInfo | None] = {}
        self._settings = get_settings()
    
    async def resolve(self, phone: str) -> PhoneTenantInfo | None:
        """
        Resolve a phone number to its tenant information.
        
        Args:
            phone: Phone number in E.164 format (+5491112345678)
            
        Returns:
            PhoneTenantInfo if found, None if phone is not registered
        """
        # Check cache first
        if phone in self._cache:
            logger.debug("phone_cache_hit", phone=phone)
            return self._cache[phone]
        
        # Query backend
        result = await self._lookup_phone(phone)
        
        # Cache result (including None for unregistered phones)
        self._cache[phone] = result
        
        return result
    
    async def _lookup_phone(self, phone: str) -> PhoneTenantInfo | None:
        """
        Query the backend API for phone tenant mapping.
        
        Args:
            phone: Phone number to look up
            
        Returns:
            PhoneTenantInfo if found, None otherwise
        """
        url = f"{self._settings.backend_api_url}/api/v1/phone/lookup"
        
        logger.debug(
            "phone_lookup_starting",
            phone=phone,
            url=url,
            backend_url=self._settings.backend_api_url,
        )
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params={"phone": phone})
                
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
                
                if not data.get("found"):
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
                )
                
        except httpx.RequestError as e:
            logger.error(
                "phone_lookup_error",
                phone=phone,
                error=str(e),
                error_type=type(e).__name__,
                url=url,
            )
            return None
        except Exception as e:
            logger.error(
                "phone_lookup_unexpected_error",
                phone=phone,
                error=str(e),
                error_type=type(e).__name__,
                url=url,
            )
            return None
    
    def invalidate_cache(self, phone: str) -> None:
        """
        Remove a phone from the cache.
        
        Call this when a phone's tenant association changes.
        
        Args:
            phone: Phone number to remove from cache
        """
        if phone in self._cache:
            del self._cache[phone]
            logger.debug("phone_cache_invalidated", phone=phone)
    
    def clear_cache(self) -> None:
        """Clear all cached phone mappings."""
        self._cache.clear()
        logger.info("phone_cache_cleared")


# Singleton instance
_resolver: PhoneResolver | None = None


def get_phone_resolver() -> PhoneResolver:
    """Get or create the phone resolver instance."""
    global _resolver
    if _resolver is None:
        _resolver = PhoneResolver()
    return _resolver
