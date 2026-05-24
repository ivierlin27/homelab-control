"""Tests for Planka auth header selection."""

from __future__ import annotations

from unittest import mock

import pytest

from apps._shared import planka_client


def test_planka_auth_headers_prefers_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANKA_API_KEY", "pk_test_123")
    monkeypatch.setenv("PLANKA_API_TOKEN", "legacy-token")
    headers = planka_client.planka_auth_headers()
    assert headers["X-Api-Key"] == "pk_test_123"
    assert "Authorization" not in headers


def test_planka_auth_headers_falls_back_to_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANKA_API_KEY", raising=False)
    monkeypatch.setenv("PLANKA_API_TOKEN", "legacy-token")
    headers = planka_client.planka_auth_headers()
    assert headers["Authorization"] == "Bearer legacy-token"
    assert "X-Api-Key" not in headers


def test_planka_auth_headers_ignores_replace_me_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANKA_API_KEY", "replace-me")
    monkeypatch.setenv("PLANKA_API_TOKEN", "replace-me")
    monkeypatch.delenv("PLANKA_EMAIL_OR_USERNAME", raising=False)
    headers = planka_client.planka_auth_headers()
    assert "X-Api-Key" not in headers
    assert "Authorization" not in headers


def test_planka_auth_configured_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANKA_BASE_URL", "https://planka.example")
    monkeypatch.setenv("PLANKA_API_KEY", "pk_live")
    assert planka_client.planka_auth_configured() is True
