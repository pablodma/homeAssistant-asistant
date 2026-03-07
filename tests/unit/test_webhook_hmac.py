"""Tests for HMAC webhook signature validation."""

import hashlib
import hmac

import pytest

from src.app.whatsapp.webhook import _verify_webhook_signature


def _make_signature(payload: bytes, secret: str) -> str:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={expected}"


def test_valid_signature_passes():
    payload = b'{"test": "data"}'
    secret = "my-app-secret"
    sig = _make_signature(payload, secret)
    assert _verify_webhook_signature(payload, sig, secret) is True


def test_invalid_signature_fails():
    payload = b'{"test": "data"}'
    secret = "my-app-secret"
    wrong_sig = "sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert _verify_webhook_signature(payload, wrong_sig, secret) is False


def test_tampered_payload_fails():
    payload = b'{"test": "data"}'
    tampered = b'{"test": "tampered"}'
    secret = "my-app-secret"
    sig = _make_signature(payload, secret)
    assert _verify_webhook_signature(tampered, sig, secret) is False


def test_empty_signature_fails():
    payload = b'{"test": "data"}'
    secret = "my-app-secret"
    assert _verify_webhook_signature(payload, "", secret) is False
