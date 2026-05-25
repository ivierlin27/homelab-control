"""Tests for shared gateway route resolution."""

from __future__ import annotations

import pytest

from apps._shared.gateway_routes import (
    DEFAULT_LOCAL_MODEL,
    gateway_model_for_route,
    normalize_logical_route,
)


def test_normalize_legacy_tiers_to_local() -> None:
    assert normalize_logical_route("local-fast") == "local"
    assert normalize_logical_route("local-strong") == "local"
    assert normalize_logical_route("local") == "local"


def test_local_routes_resolve_to_strong_long() -> None:
    assert gateway_model_for_route("local") == DEFAULT_LOCAL_MODEL
    assert gateway_model_for_route("local-fast") == DEFAULT_LOCAL_MODEL
    assert gateway_model_for_route("local-strong") == DEFAULT_LOCAL_MODEL
    assert DEFAULT_LOCAL_MODEL == "homelab-strong-long"


def test_homelab_local_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps._shared import gateway_routes

    monkeypatch.setattr(gateway_routes, "DEFAULT_LOCAL_MODEL", "custom-local")
    assert gateway_model_for_route("local") == "custom-local"
