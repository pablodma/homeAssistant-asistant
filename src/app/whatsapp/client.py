"""WhatsApp Cloud API client."""

from typing import Optional

import httpx
import structlog

from ..config import get_settings
from .types import OutgoingMessage

logger = structlog.get_logger()

WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"


class WhatsAppClient:
    """Client for WhatsApp Cloud API."""

    def __init__(self):
        """Initialize WhatsApp client."""
        self.settings = get_settings()
        self.phone_number_id = self.settings.whatsapp_phone_number_id
        self.access_token = self.settings.whatsapp_access_token

    def _normalize_phone_for_whatsapp(self, phone: str) -> str:
        """
        Normalize phone number for WhatsApp API.
        
        WhatsApp API (in test mode) doesn't accept Argentine numbers with the '9'
        prefix for mobile numbers. This removes it for sending.
        
        Example: 5491161366496 -> 541161366496
        """
        # Remove + if present
        phone = phone.lstrip("+")
        
        # Argentine mobile numbers: remove the 9 after country code
        # Format: 549XXXXXXXXXX -> 54XXXXXXXXXX
        if phone.startswith("549") and len(phone) == 13:
            normalized = "54" + phone[3:]
            logger.debug(
                "phone_normalized_for_whatsapp",
                original=phone,
                normalized=normalized,
            )
            return normalized
        
        return phone

    async def send_message(self, message: OutgoingMessage) -> bool:
        """Send a text message to a WhatsApp number.

        Args:
            message: The message to send.

        Returns:
            True if sent successfully, False otherwise.
        """
        url = f"{WHATSAPP_API_URL}/{self.phone_number_id}/messages"

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        # Normalize phone for WhatsApp API (removes Argentine mobile '9' prefix)
        normalized_phone = self._normalize_phone_for_whatsapp(message.phone)
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_phone,
            "type": "text",
            "text": {"body": message.text},
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=30.0,
                )

                if response.status_code == 200:
                    logger.info(
                        "Message sent successfully",
                        phone=message.phone,
                        message_id=response.json().get("messages", [{}])[0].get("id"),
                    )
                    return True
                else:
                    logger.error(
                        "Failed to send message",
                        phone=message.phone,
                        status_code=response.status_code,
                        response=response.text,
                    )
                    return False

        except Exception as e:
            logger.error(
                "Error sending message",
                phone=message.phone,
                error=str(e),
            )
            return False

    async def send_text(self, phone: str, text: str) -> bool:
        """Convenience method to send a text message.

        Args:
            phone: Recipient phone number.
            text: Message text.

        Returns:
            True if sent successfully.
        """
        return await self.send_message(OutgoingMessage(phone=phone, text=text))

    async def mark_as_read(self, message_id: str) -> bool:
        """Mark a message as read.

        Args:
            message_id: The WhatsApp message ID.

        Returns:
            True if marked successfully.
        """
        url = f"{WHATSAPP_API_URL}/{self.phone_number_id}/messages"

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=10.0,
                )
                return response.status_code == 200
        except Exception as e:
            logger.warning("Failed to mark message as read", error=str(e))
            return False


# Global client instance
_client: Optional[WhatsAppClient] = None


def get_whatsapp_client() -> WhatsAppClient:
    """Get WhatsApp client singleton."""
    global _client
    if _client is None:
        _client = WhatsAppClient()
    return _client
