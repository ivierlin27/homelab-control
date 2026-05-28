"""Unit tests for Planka test-card name matching (no API calls)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "planka_cleanup_test_cards",
    ROOT / "scripts" / "planka_cleanup_test_cards.py",
)
assert SPEC and SPEC.loader
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)


def test_matches_agent_smoke_names() -> None:
    patterns = cleanup.compile_patterns(cleanup.DEFAULT_TEST_NAME_PATTERNS)
    assert cleanup.card_matches("verify-executive", patterns)
    assert cleanup.card_matches("smoke-homelab-maintainer", patterns)
    assert cleanup.card_matches(
        "A2A help: live.smoke.help_request (agent:homelab-maintainer)",
        patterns,
    )
    assert not cleanup.card_matches("Immich deployment execution handoff", patterns)


def test_legacy_e2e_optional() -> None:
    patterns = cleanup.compile_patterns(
        cleanup.DEFAULT_TEST_NAME_PATTERNS,
        cleanup.LEGACY_E2E_PATTERNS,
    )
    assert cleanup.card_matches("Post-guard Planka end-to-end smoke test", patterns)
    assert cleanup.card_matches("Automated E2E after executable planning fix", patterns)
