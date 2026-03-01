"""Tests for quick actions helpers."""

from src.app.services.quick_actions import (
    build_finance_expense_quick_actions,
    clear_pending_expense_edit,
    get_pending_expense_edit,
    parse_quick_action_id,
    set_pending_expense_edit,
)


def test_build_finance_expense_quick_actions_structure():
    payload = build_finance_expense_quick_actions("exp-123")

    assert payload["version"] == 1
    assert payload["context"]["domain"] == "finance"
    assert payload["context"]["expense_id"] == "exp-123"
    assert len(payload["actions"]) == 3
    assert payload["actions"][0]["id"].startswith("fin_expense_edit:")


def test_parse_quick_action_id_variants():
    parsed_edit = parse_quick_action_id("fin_expense_edit:abc")
    assert parsed_edit is not None
    assert parsed_edit.kind == "expense_edit"
    assert parsed_edit.expense_id == "abc"

    parsed_delete = parse_quick_action_id("fin_expense_delete:def")
    assert parsed_delete is not None
    assert parsed_delete.kind == "expense_delete"
    assert parsed_delete.expense_id == "def"

    parsed_menu = parse_quick_action_id("fin_menu")
    assert parsed_menu is not None
    assert parsed_menu.kind == "menu"
    assert parsed_menu.expense_id is None

    assert parse_quick_action_id("unknown_action") is None


def test_pending_expense_edit_store_roundtrip():
    phone = "5491111111111"
    clear_pending_expense_edit(phone)

    set_pending_expense_edit(phone, "expense-42", ttl_seconds=60)
    stored = get_pending_expense_edit(phone)
    assert stored is not None
    assert stored.expense_id == "expense-42"

    clear_pending_expense_edit(phone)
    assert get_pending_expense_edit(phone) is None
