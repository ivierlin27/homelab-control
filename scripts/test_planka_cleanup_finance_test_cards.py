"""Unit tests for finance Planka test-card cleanup (no API calls)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "planka_cleanup_finance_test_cards",
    ROOT / "scripts" / "planka_cleanup_finance_test_cards.py",
)
assert SPEC and SPEC.loader
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)


def test_matches_finance_smoke_card_title() -> None:
    patterns = cleanup.compile_patterns(cleanup.DEFAULT_FINANCE_TEST_PATTERNS)
    card = {"name": "2099-01-01 — smoke-finance-defer", "description": ""}
    assert cleanup.card_matches(card, patterns)


def test_matches_correlation_in_description() -> None:
    patterns = cleanup.compile_patterns(cleanup.DEFAULT_FINANCE_TEST_PATTERNS)
    card = {
        "name": "2099-01-01 — smoke-finance-defer",
        "description": "**Run:** `live.smoke.finance-planka-123`",
    }
    assert cleanup.card_matches(card, patterns)


def test_does_not_match_normal_defer_card() -> None:
    patterns = cleanup.compile_patterns(cleanup.DEFAULT_FINANCE_TEST_PATTERNS)
    card = {
        "name": "2024-06-01 — MYSTERY VENDOR XYZ",
        "description": "**Reason:** below threshold",
    }
    assert not cleanup.card_matches(card, patterns)


def test_finance_board_id_prefers_finance_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PLANKA_FINANCE_BOARD_ID", "finance-board")
    monkeypatch.setenv("PLANKA_BOARD_ID", "homelab-board")
    assert cleanup.finance_board_id("") == "finance-board"
