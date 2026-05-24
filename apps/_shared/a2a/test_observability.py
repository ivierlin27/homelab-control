"""Tests for A2A observability snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from apps._shared.a2a.observability import build_a2a_status, queue_a2a_metrics
from apps._shared.a2a.queue import enqueue_inbox, ensure_dirs


def test_queue_a2a_metrics_counts_stuck_replies(tmp_path: Path) -> None:
    dirs = ensure_dirs(tmp_path / "agent-executive")
    enqueue_inbox(
        dirs["inbox"],
        "a2a-reply-x.json",
        {
            "correlation_id": "x",
            "caller": "agent:executive",
            "callee": "agent:homelab-maintainer",
            "action": "help_request",
            "is_reply": True,
            "payload": {},
        },
    )
    enqueue_inbox(
        dirs["inbox"],
        "job-1.json",
        {"action": "handle-request", "request": "hello"},
    )
    metrics = queue_a2a_metrics(tmp_path / "agent-executive")
    assert metrics["inbox_stuck_replies"] == 1
    assert metrics["inbox_worker_jobs"] == 1


def test_build_a2a_status_marks_unhealthy_on_stuck_replies(tmp_path: Path) -> None:
    root = tmp_path / "state"
    exec_dir = root / "agent-executive"
    maint_dir = root / "agent-homelab-maintainer"
    for path in (exec_dir, maint_dir):
        ensure_dirs(path)
    enqueue_inbox(
        ensure_dirs(maint_dir)["inbox"],
        "a2a-reply-stuck.json",
        {"correlation_id": "c", "is_reply": True, "action": "x"},
    )
    ledger = exec_dir / "trust-ledger.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "event": "a2a-help-request",
                "occurred_at": "2026-05-24T12:00:00+00:00",
                "correlation_id": "cid-1",
                "caller": "agent:homelab-maintainer",
                "task_class": "live.smoke",
                "card": {"created": False, "dry_run": True},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    status = build_a2a_status(root)
    assert status["healthy"] is False
    assert any("reply" in alert for alert in status["alerts"])
    assert status["recent_help_requests"][0]["correlation_id"] == "cid-1"
