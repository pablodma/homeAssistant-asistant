"""WhatsApp message types."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class WhatsAppContact(BaseModel):
    """WhatsApp contact information."""

    wa_id: str
    profile: Optional[dict[str, Any]] = None

    @property
    def phone(self) -> str:
        """Get phone number in E.164 format."""
        return self.wa_id


class WhatsAppTextMessage(BaseModel):
    """WhatsApp text message content."""

    body: str


class WhatsAppMessage(BaseModel):
    """WhatsApp incoming message."""

    id: str
    from_: str = Field(alias="from")
    timestamp: str
    type: Literal["text", "image", "audio", "video", "document", "location", "contacts", "button", "interactive"]
    text: Optional[WhatsAppTextMessage] = None

    class Config:
        populate_by_name = True


class WhatsAppMetadata(BaseModel):
    """WhatsApp webhook metadata."""

    display_phone_number: str
    phone_number_id: str


class WhatsAppValue(BaseModel):
    """WhatsApp webhook value."""

    messaging_product: str
    metadata: WhatsAppMetadata
    contacts: Optional[list[WhatsAppContact]] = None
    messages: Optional[list[WhatsAppMessage]] = None
    statuses: Optional[list[dict[str, Any]]] = None


class WhatsAppChange(BaseModel):
    """WhatsApp webhook change."""

    field: str
    value: WhatsAppValue


class WhatsAppEntry(BaseModel):
    """WhatsApp webhook entry."""

    id: str
    changes: list[WhatsAppChange]


class WhatsAppWebhookPayload(BaseModel):
    """WhatsApp webhook payload."""

    object: str
    entry: list[WhatsAppEntry]


# Simplified types for internal use


class IncomingMessage(BaseModel):
    """Simplified incoming message for processing."""

    message_id: str
    phone: str
    text: str
    timestamp: datetime
    contact_name: Optional[str] = None

    @classmethod
    def from_webhook(
        cls,
        message: WhatsAppMessage,
        contact: Optional[WhatsAppContact] = None,
    ) -> "IncomingMessage":
        """Create from webhook data."""
        # Use phone number as-is from WhatsApp (already in E.164 format without +)
        phone = message.from_

        contact_name = None
        if contact and contact.profile:
            contact_name = contact.profile.get("name")

        return cls(
            message_id=message.id,
            phone=phone,
            text=message.text.body if message.text else "",
            timestamp=datetime.fromtimestamp(int(message.timestamp)),
            contact_name=contact_name,
        )


class OutgoingMessage(BaseModel):
    """Outgoing message to send."""

    phone: str
    text: str
