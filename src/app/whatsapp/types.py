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


class WhatsAppInteractiveListReply(BaseModel):
    """Interactive list reply content."""

    id: str
    title: str
    description: Optional[str] = None


class WhatsAppInteractiveButtonReply(BaseModel):
    """Interactive button reply content."""

    id: str
    title: str


class WhatsAppInteractiveResponse(BaseModel):
    """WhatsApp interactive response (list or button selection)."""

    type: Literal["list_reply", "button_reply"]
    list_reply: Optional[WhatsAppInteractiveListReply] = None
    button_reply: Optional[WhatsAppInteractiveButtonReply] = None


class WhatsAppMessage(BaseModel):
    """WhatsApp incoming message."""

    id: str
    from_: str = Field(alias="from")
    timestamp: str
    type: Literal["text", "image", "audio", "video", "document", "location", "contacts", "button", "interactive"]
    text: Optional[WhatsAppTextMessage] = None
    interactive: Optional[WhatsAppInteractiveResponse] = None

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
    is_interactive: bool = False
    interactive_type: Optional[str] = None  # "list_reply" or "button_reply"
    interactive_id: Optional[str] = None  # ID of the selected item

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

        # Handle text messages
        if message.type == "text" and message.text:
            return cls(
                message_id=message.id,
                phone=phone,
                text=message.text.body,
                timestamp=datetime.fromtimestamp(int(message.timestamp)),
                contact_name=contact_name,
            )

        # Handle interactive responses (list or button selections)
        if message.type == "interactive" and message.interactive:
            interactive = message.interactive
            interactive_id = None
            text = ""

            if interactive.type == "list_reply" and interactive.list_reply:
                interactive_id = interactive.list_reply.id
                text = interactive.list_reply.title
            elif interactive.type == "button_reply" and interactive.button_reply:
                interactive_id = interactive.button_reply.id
                text = interactive.button_reply.title

            return cls(
                message_id=message.id,
                phone=phone,
                text=text,
                timestamp=datetime.fromtimestamp(int(message.timestamp)),
                contact_name=contact_name,
                is_interactive=True,
                interactive_type=interactive.type,
                interactive_id=interactive_id,
            )

        # Fallback for other message types
        return cls(
            message_id=message.id,
            phone=phone,
            text="",
            timestamp=datetime.fromtimestamp(int(message.timestamp)),
            contact_name=contact_name,
        )


class OutgoingMessage(BaseModel):
    """Outgoing message to send."""

    phone: str
    text: str
