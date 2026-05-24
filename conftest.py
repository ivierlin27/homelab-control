"""Pytest defaults for homelab-control."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _disable_live_escalation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent unit tests from posting to Discord during ingest/job failures."""
    monkeypatch.setenv("HOMELAB_ESCALATION_DISABLE", "1")
