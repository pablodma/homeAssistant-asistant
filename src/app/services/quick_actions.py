"""Helpers for WhatsApp quick actions metadata and state."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Optional, TypedDict


class QuickActionItem(TypedDict):
    """Quick action item shown to the user."""

    id: str
    title: str


class QuickActionsPayload(TypedDict):
    """Structured quick actions contract stored in AgentResult.metadata."""

    version: int
    context: dict[str, str]
    actions: list[QuickActionItem]


def build_finance_expense_quick_actions(expense_id: str) -> QuickActionsPayload:
    """Build default finance quick actions after expense creation."""
    return {
        "version": 1,
        "context": {"domain": "finance", "operation": "expense_created", "expense_id": expense_id},
        "actions": [
            {"id": f"fin_expense_edit:{expense_id}", "title": "Editar gasto"},
            {"id": f"fin_expense_delete:{expense_id}", "title": "Cancelar gasto"},
            {"id": "fin_menu", "title": "Ir al menu"},
        ],
    }


@dataclass
class ParsedQuickAction:
    """Parsed quick action identifier from WhatsApp interactive reply."""

    kind: Literal["expense_edit", "expense_delete", "menu"]
    expense_id: Optional[str] = None


def parse_quick_action_id(action_id: str | None) -> Optional[ParsedQuickAction]:
    """Parse a quick action identifier into a typed payload."""
    if not action_id:
        return None

    if action_id == "fin_menu":
        return ParsedQuickAction(kind="menu", expense_id=None)

    if action_id.startswith("fin_expense_edit:"):
        return ParsedQuickAction(
            kind="expense_edit",
            expense_id=action_id.split(":", 1)[1] or None,
        )

    if action_id.startswith("fin_expense_delete:"):
        return ParsedQuickAction(
            kind="expense_delete",
            expense_id=action_id.split(":", 1)[1] or None,
        )

    return None


@dataclass
class PendingExpenseEdit:
    """In-memory pending edit context for quick-action follow-up."""

    expense_id: str
    expires_at: float


_pending_expense_edits: dict[str, PendingExpenseEdit] = {}
_DEFAULT_TTL_SECONDS = 10 * 60


def set_pending_expense_edit(phone: str, expense_id: str, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
    """Store pending expense edit context for a phone number."""
    _pending_expense_edits[phone] = PendingExpenseEdit(
        expense_id=expense_id,
        expires_at=time.time() + ttl_seconds,
    )


def get_pending_expense_edit(phone: str) -> Optional[PendingExpenseEdit]:
    """Get pending expense edit context if still valid."""
    item = _pending_expense_edits.get(phone)
    if not item:
        return None
    if item.expires_at < time.time():
        _pending_expense_edits.pop(phone, None)
        return None
    return item


def clear_pending_expense_edit(phone: str) -> None:
    """Remove pending expense edit context for a phone number."""
    _pending_expense_edits.pop(phone, None)
