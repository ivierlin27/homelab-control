"""Tests for escalation factory wiring."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from apps._shared.escalation import AttemptOutcome, build_dispatcher_for_principal
from apps._shared.escalation.policy import load_config, resolve_budgets


def test_build_dispatcher_finance_skips_tier2(tmp_path: Path) -> None:
    config = load_config()
    budgets = resolve_budgets(
        task_class="finance.ingest",
        config=config,
        manifest_overrides={
            "tier1_budget_seconds": 30,
            "skip_tier2": True,
        },
    )
    assert budgets.skip_tier2 is True

    dispatcher = build_dispatcher_for_principal(
        "agent:finance",
        task_class="finance.ingest",
        queue_dir=tmp_path / "agent-finance",
        dm_only_tier3=True,
    )
    assert dispatcher.tier2 is None
    assert dispatcher.tier3 is not None


def test_run_escalation_tier1_success_without_tier2(tmp_path: Path) -> None:
    dispatcher = build_dispatcher_for_principal(
        "agent:homelab-maintainer",
        task_class="homelab.record_note",
        queue_dir=tmp_path / "agent-homelab-maintainer",
    )

    def attempt(attempt_index: int, previous):
        return (AttemptOutcome.SUCCESS, {"ok": True}, "done")

    dispatcher.attempt = attempt
    result = dispatcher.execute(task_class="homelab.record_note")
    assert result.succeeded()
    assert result.final_tier == 1
