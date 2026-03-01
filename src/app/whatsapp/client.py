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

    async def send_interactive_list(
        self,
        phone: str,
        header: str,
        body: str,
        button_text: str,
        sections: list[dict],
    ) -> bool:
        """Send an interactive list message.

        Args:
            phone: Recipient phone number.
            header: Header text (max 60 chars).
            body: Body text (max 1024 chars).
            button_text: Text on the button to open list (max 20 chars).
            sections: List of sections, each with 'title' and 'rows'.
                      Each row has 'id', 'title' (max 24 chars), and optional 'description'.

        Returns:
            True if sent successfully.

        Example:
            await send_interactive_list(
                phone="5491161366496",
                header="Seleccionar categoría",
                body="¿A qué categoría querés asignar el gasto?",
                button_text="Ver categorías",
                sections=[{
                    "title": "Categorías disponibles",
                    "rows": [
                        {"id": "cat_1", "title": "Supermercado", "description": "$50,000/mes"},
                        {"id": "cat_2", "title": "Transporte", "description": "$30,000/mes"},
                    ]
                }]
            )
        """
        url = f"{WHATSAPP_API_URL}/{self.phone_number_id}/messages"

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        normalized_phone = self._normalize_phone_for_whatsapp(phone)

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_phone,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": header[:60]},
                "body": {"text": body[:1024]},
                "action": {
                    "button": button_text[:20],
                    "sections": sections,
                },
            },
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
                        "Interactive list sent successfully",
                        phone=phone,
                        message_id=response.json().get("messages", [{}])[0].get("id"),
                    )
                    return True
                else:
                    logger.error(
                        "Failed to send interactive list",
                        phone=phone,
                        status_code=response.status_code,
                        response=response.text,
                    )
                    return False

        except Exception as e:
            logger.error(
                "Error sending interactive list",
                phone=phone,
                error=str(e),
            )
            return False

    async def send_interactive_buttons(
        self,
        phone: str,
        body: str,
        buttons: list[dict[str, str]],
    ) -> bool:
        """Send WhatsApp reply buttons (max 3 buttons).

        Args:
            phone: Recipient phone number.
            body: Message body text (max 1024 chars).
            buttons: Buttons with shape [{"id": "...", "title": "..."}].

        Returns:
            True if sent successfully.
        """
        limited_buttons = buttons[:3]
        if not limited_buttons:
            return False

        url = f"{WHATSAPP_API_URL}/{self.phone_number_id}/messages"

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        normalized_phone = self._normalize_phone_for_whatsapp(phone)

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body[:1024]},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {
                                "id": button["id"][:256],
                                "title": button["title"][:20],
                            },
                        }
                        for button in limited_buttons
                    ]
                },
            },
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
                        "Interactive buttons sent successfully",
                        phone=phone,
                        message_id=response.json().get("messages", [{}])[0].get("id"),
                    )
                    return True

                logger.error(
                    "Failed to send interactive buttons",
                    phone=phone,
                    status_code=response.status_code,
                    response=response.text,
                )
                return False

        except Exception as e:
            logger.error(
                "Error sending interactive buttons",
                phone=phone,
                error=str(e),
            )
            return False

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """Download media from WhatsApp Cloud API.

        Two-step process:
        1. GET /{media_id} to obtain the download URL
        2. GET {url} to download the actual bytes

        Args:
            media_id: The WhatsApp media ID.

        Returns:
            Tuple of (audio_bytes, content_type).

        Raises:
            RuntimeError: If download fails at any step.
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
        }

        async with httpx.AsyncClient() as client:
            # Step 1: Get media URL
            media_url = f"{WHATSAPP_API_URL}/{media_id}"
            response = await client.get(
                media_url,
                headers=headers,
                timeout=15.0,
            )

            if response.status_code != 200:
                logger.error(
                    "Failed to get media URL",
                    media_id=media_id,
                    status_code=response.status_code,
                    response=response.text,
                )
                raise RuntimeError(f"Failed to get media URL: {response.status_code}")

            media_info = response.json()
            download_url = media_info.get("url")
            content_type = media_info.get("mime_type", "audio/ogg")

            if not download_url:
                raise RuntimeError("No download URL in media response")

            # Step 2: Download actual bytes
            response = await client.get(
                download_url,
                headers=headers,
                timeout=60.0,
            )

            if response.status_code != 200:
                logger.error(
                    "Failed to download media",
                    media_id=media_id,
                    status_code=response.status_code,
                )
                raise RuntimeError(f"Failed to download media: {response.status_code}")

            logger.info(
                "Media downloaded",
                media_id=media_id,
                content_type=content_type,
                size_bytes=len(response.content),
            )

            return response.content, content_type

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

    async def mark_as_read_and_typing(self, message_id: str) -> bool:
        """Mark a message as read and show typing indicator.

        The typing indicator disappears when a response is sent or after 25 seconds.
        Use this when the bot will take a few seconds to respond.

        Args:
            message_id: The WhatsApp message ID.

        Returns:
            True if sent successfully.
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
            "typing_indicator": {"type": "text"},
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
            logger.warning(
                "Failed to send typing indicator", error=str(e)
            )
            return False


# Global client instance
_client: Optional[WhatsAppClient] = None


def get_whatsapp_client() -> WhatsAppClient:
    """Get WhatsApp client singleton."""
    global _client
    if _client is None:
        _client = WhatsAppClient()
    return _client
