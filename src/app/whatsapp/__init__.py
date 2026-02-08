"""WhatsApp integration module."""

from .client import WhatsAppClient
from .types import IncomingMessage, OutgoingMessage

__all__ = ["WhatsAppClient", "IncomingMessage", "OutgoingMessage"]
