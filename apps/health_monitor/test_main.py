"""Integration-ish tests for the orchestrator + Discord formatter."""

from __future__ import annotations

from pathlib import Path

from .core import CheckResult, Status, Transition
from .main import collect_all, format_discord, run


def test_format_discord_empty_returns_empty_string():
    assert format_discord([]) == ""


def test_format_discord_includes_arrow_and_runbook():
    t = Transition(
        name="audit:foo", previous=Status.HEALTHY, current=Status.UNHEALTHY,
        detail="chain broken at seq 4", runbook="docs/runbooks/AUDIT_RECOVERY.md", ts=0,
    )
    msg = format_discord([t])
    assert "🔴" in msg
    assert "audit:foo" in msg
    assert "healthy → **unhealthy**" in msg
    assert "AUDIT_RECOVERY" in msg


def test_format_discord_recovery_uses_green():
    t = Transition(
        name="x", previous=Status.UNHEALTHY, current=Status.HEALTHY,
        detail="back", runbook=None, ts=0,
    )
    msg = format_discord([t])
    assert "🟢" in msg and "back" in msg


def test_collect_all_isolates_one_broken_check_from_others():
    def broken_check():
        raise RuntimeError("kaboom")

    def good_check():
        return [CheckResult(name="g", status=Status.HEALTHY, detail="ok")]

    results = collect_all([broken_check, good_check])
    names = [r.name for r in results]
    assert "g" in names
    assert any(n.startswith("check_fn:broken_check") for n in names)


def test_run_writes_state_and_audit_in_dry_run_does_not(tmp_path):
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.jsonl"

    def fake_check():
        return [CheckResult(name="t1", status=Status.UNHEALTHY, detail="bad")]

    summary = run(state_path=state_path, audit_path=audit_path,
                  discord_webhook="", dry_run=True, checks=[fake_check])
    assert summary["checks"] == 1
    assert summary["unhealthy"] == 1
    assert not state_path.exists()
    assert not audit_path.exists()


def test_run_persists_state_and_audits_transition(tmp_path):
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.jsonl"
    results_seq = [
        [CheckResult(name="t1", status=Status.HEALTHY, detail="ok")],
        [CheckResult(name="t1", status=Status.UNHEALTHY, detail="broke")],
    ]
    calls = {"n": 0}

    def fake_check():
        out = results_seq[calls["n"]]
        calls["n"] += 1
        return out

    s1 = run(state_path=state_path, audit_path=audit_path,
             discord_webhook="", checks=[fake_check])
    assert s1["transitions"] == 0
    s2 = run(state_path=state_path, audit_path=audit_path,
             discord_webhook="", checks=[fake_check])
    assert s2["transitions"] == 1

    audit_lines = [l for l in audit_path.read_text().splitlines() if l.strip()]
    # one health_run per call (2) + one health_transition for the flip = 3
    assert len(audit_lines) == 3
