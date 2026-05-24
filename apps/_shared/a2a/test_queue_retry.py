"""Tests for queue retry / DLQ helpers."""

from __future__ import annotations

import json
from pathlib import Path

from apps._shared.a2a.queue import ensure_dirs, enqueue, requeue_for_retry, resolve_max_retries


def test_requeue_for_retry_moves_back_to_inbox(tmp_path: Path) -> None:
    queue_dir = tmp_path / "agent-test"
    processing = queue_dir / "processing" / "job.json"
    processing.parent.mkdir(parents=True)
    processing.write_text(json.dumps({"action": "triage-intake"}), encoding="utf-8")

    disposition, count = requeue_for_retry(
        processing,
        queue_dir,
        {"action": "triage-intake"},
        "transient error",
        max_retries=3,
    )
    assert disposition == "retry"
    assert count == 1
    assert (queue_dir / "inbox" / "job.json").is_file()
    assert not processing.exists()


def test_requeue_for_retry_moves_to_dlq_after_limit(tmp_path: Path) -> None:
    queue_dir = tmp_path / "agent-test"
    processing = queue_dir / "processing" / "job.json"
    processing.parent.mkdir(parents=True)
    job = {"action": "triage-intake", "_retry_count": 2}
    processing.write_text(json.dumps(job), encoding="utf-8")

    disposition, count = requeue_for_retry(
        processing,
        queue_dir,
        job,
        "still failing",
        max_retries=3,
    )
    assert disposition == "dlq"
    assert count == 3
    assert (queue_dir / "dlq" / "job.json").is_file()
    assert (queue_dir / "dlq" / "job.error.json").is_file()


def test_ensure_dirs_includes_dlq(tmp_path: Path) -> None:
    dirs = ensure_dirs(tmp_path / "q")
    assert "dlq" in dirs
    assert dirs["dlq"].is_dir()


def test_resolve_max_retries_from_job() -> None:
    assert resolve_max_retries({"_max_retries": 5}) == 5
    assert resolve_max_retries({}) == 3
