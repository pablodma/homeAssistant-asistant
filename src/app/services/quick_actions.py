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
            {"id": "fin_summary_month", "title": "Ver resumen"},
        ],
    }


@dataclass
class ParsedQuickAction:
    """Parsed quick action identifier from WhatsApp interactive reply."""

    kind: Literal[
        "expense_edit", "expense_delete", "summary",
        "income_edit", "income_delete",
        "balance", "add_expense", "add_income",
        "budget_status", "add_category", "undo_expense",
    ]
    expense_id: Optional[str] = None
    income_id: Optional[str] = None


def parse_quick_action_id(action_id: str | None) -> Optional[ParsedQuickAction]:
    """Parse a quick action identifier into a typed payload."""
    if not action_id:
        return None

    if action_id in ("fin_summary_month", "fin_menu"):
        return ParsedQuickAction(kind="summary", expense_id=None)

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

    if action_id == "fin_balance_month":
        return ParsedQuickAction(kind="balance")

    if action_id == "fin_add_expense":
        return ParsedQuickAction(kind="add_expense")

    if action_id == "fin_add_income":
        return ParsedQuickAction(kind="add_income")

    if action_id == "fin_budget_status":
        return ParsedQuickAction(kind="budget_status")

    if action_id == "fin_add_category":
        return ParsedQuickAction(kind="add_category")

    if action_id.startswith("fin_income_edit:"):
        return ParsedQuickAction(
            kind="income_edit",
            income_id=action_id.split(":", 1)[1] or None,
        )

    if action_id.startswith("fin_income_delete:"):
        return ParsedQuickAction(
            kind="income_delete",
            income_id=action_id.split(":", 1)[1] or None,
        )

    if action_id.startswith("fin_undo_expense:"):
        return ParsedQuickAction(kind="undo_expense")

    return None


def build_finance_income_quick_actions(income_id: str) -> QuickActionsPayload:
    """Build default finance quick actions after income creation."""
    return {
        "version": 1,
        "context": {"domain": "finance", "operation": "income_created", "income_id": income_id},
        "actions": [
            {"id": f"fin_income_edit:{income_id}", "title": "Editar ingreso"},
            {"id": f"fin_income_delete:{income_id}", "title": "Cancelar ingreso"},
            {"id": "fin_balance_month", "title": "Ver balance"},
        ],
    }


def build_finance_delete_expense_quick_actions(amount: str, category: str) -> QuickActionsPayload:
    """Build quick actions after expense deletion."""
    return {
        "version": 1,
        "context": {"domain": "finance", "operation": "expense_deleted"},
        "actions": [
            {"id": f"fin_undo_expense:{amount}:{category}", "title": "Deshacer"},
            {"id": "fin_summary_month", "title": "Ver resumen"},
        ],
    }


def build_finance_modify_expense_quick_actions() -> QuickActionsPayload:
    """Build quick actions after expense modification."""
    return {
        "version": 1,
        "context": {"domain": "finance", "operation": "expense_modified"},
        "actions": [
            {"id": "fin_summary_month", "title": "Ver resumen"},
        ],
    }


def build_finance_balance_quick_actions() -> QuickActionsPayload:
    """Build quick actions for balance view."""
    return {
        "version": 1,
        "context": {"domain": "finance", "operation": "balance_view"},
        "actions": [
            {"id": "fin_add_expense", "title": "Registrar gasto"},
            {"id": "fin_add_income", "title": "Registrar ingreso"},
            {"id": "fin_summary_month", "title": "Ver detalle gastos"},
        ],
    }


def build_finance_report_quick_actions() -> QuickActionsPayload:
    """Build quick actions for report view."""
    return {
        "version": 1,
        "context": {"domain": "finance", "operation": "report_view"},
        "actions": [
            {"id": "fin_add_expense", "title": "Registrar gasto"},
            {"id": "fin_budget_status", "title": "Ver presupuesto"},
            {"id": "fin_balance_month", "title": "Ver balance"},
        ],
    }


def build_finance_budget_quick_actions() -> QuickActionsPayload:
    """Build quick actions for budget view."""
    return {
        "version": 1,
        "context": {"domain": "finance", "operation": "budget_view"},
        "actions": [
            {"id": "fin_add_expense", "title": "Registrar gasto"},
            {"id": "fin_summary_month", "title": "Ver gastos"},
            {"id": "fin_balance_month", "title": "Ver balance"},
        ],
    }


def build_finance_incomes_quick_actions() -> QuickActionsPayload:
    """Build quick actions for incomes view."""
    return {
        "version": 1,
        "context": {"domain": "finance", "operation": "incomes_view"},
        "actions": [
            {"id": "fin_add_income", "title": "Registrar ingreso"},
            {"id": "fin_balance_month", "title": "Ver balance"},
        ],
    }


def build_finance_search_quick_actions() -> QuickActionsPayload:
    """Build quick actions for expense search view."""
    return {
        "version": 1,
        "context": {"domain": "finance", "operation": "search_view"},
        "actions": [
            {"id": "fin_add_expense", "title": "Registrar gasto"},
            {"id": "fin_summary_month", "title": "Ver resumen"},
        ],
    }


def build_finance_categories_quick_actions() -> QuickActionsPayload:
    """Build quick actions for categories view."""
    return {
        "version": 1,
        "context": {"domain": "finance", "operation": "categories_view"},
        "actions": [
            {"id": "fin_add_expense", "title": "Registrar gasto"},
            {"id": "fin_add_category", "title": "Crear categoria"},
        ],
    }


def build_finance_new_category_quick_actions() -> QuickActionsPayload:
    """Build quick actions after category creation."""
    return {
        "version": 1,
        "context": {"domain": "finance", "operation": "category_created"},
        "actions": [
            {"id": "fin_budget_status", "title": "Ver presupuesto"},
            {"id": "fin_add_expense", "title": "Registrar gasto"},
        ],
    }


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
