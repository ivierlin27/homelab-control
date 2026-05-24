"""Dedupe executive ``help_request`` A2A jobs by ``correlation_id``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from apps._shared.a2a.queue import archive_inbox_json

HELP_REQUEST_EVENT = "a2a-help-request"


def _normalize_action(action: str) -> str:
    return action.strip().lower().replace("_", "-")


def is_help_request_job(job: dict[str, Any]) -> bool:
    if job.get("is_reply"):
        return False
    required = {"correlation_id", "caller", "callee", "action", "reply_to"}
    if not required.issubset(job.keys()):
        return False
    return _normalize_action(str(job.get("action", ""))) == "help-request"


def read_trust_ledger_correlation_ids(state_dir: Path) -> set[str]:
    """Correlation IDs already recorded for ``help_request`` in the trust ledger."""
    path = state_dir / "trust-ledger.jsonl"
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") != HELP_REQUEST_EVENT:
            continue
        cid = str(row.get("correlation_id", "")).strip()
        if cid:
            ids.add(cid)
    return ids


def scan_queue_stage_correlation_ids(stage_dir: Path) -> set[str]:
    ids: set[str] = set()
    if not stage_dir.is_dir():
        return ids
    for path in stage_dir.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not is_help_request_job(job):
            continue
        cid = str(job.get("correlation_id", "")).strip()
        if cid:
            ids.add(cid)
    return ids


def completed_help_request_correlation_ids(state_dir: Path, queue_dir: Path) -> set[str]:
    """IDs with a finished ``help_request`` (trust ledger and ``done/`` queue)."""
    known = read_trust_ledger_correlation_ids(state_dir)
    known |= scan_queue_stage_correlation_ids(queue_dir / "done")
    return known


def inflight_help_request_correlation_ids(queue_dir: Path) -> set[str]:
    return scan_queue_stage_correlation_ids(queue_dir / "processing")


def known_help_request_correlation_ids(state_dir: Path, queue_dir: Path) -> set[str]:
    """IDs already completed or currently in ``processing/`` (for inbox dedupe)."""
    known = completed_help_request_correlation_ids(state_dir, queue_dir)
    known |= inflight_help_request_correlation_ids(queue_dir)
    return known


def help_request_already_handled(
    correlation_id: str,
    *,
    state_dir: Path,
    queue_dir: Path,
) -> bool:
    cid = correlation_id.strip()
    if not cid:
        return False
    return cid in completed_help_request_correlation_ids(state_dir, queue_dir)


def dedupe_help_request_inbox(
    jobs: list[Path],
    *,
    queue_dir: Path,
    state_dir: Path,
) -> list[Path]:
    """Drop duplicate ``help_request`` inbox files; archive extras to ``done/``."""
    known = known_help_request_correlation_ids(state_dir, queue_dir)
    seen_inbox: set[str] = set()
    kept: list[Path] = []

    for path in jobs:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            kept.append(path)
            continue
        if not is_help_request_job(job):
            kept.append(path)
            continue
        cid = str(job.get("correlation_id", "")).strip()
        if not cid:
            kept.append(path)
            continue
        if cid in known or cid in seen_inbox:
            archive_inbox_json(path, queue_dir, stage="done")
            continue
        seen_inbox.add(cid)
        kept.append(path)

    return kept


def duplicate_help_request_result(correlation_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    return {
        "ok": True,
        "outcome": "executive acknowledged (duplicate help_request skipped)",
        "deduped": True,
        "reply_payload": {
            "correlation_id": correlation_id,
            "deduped": True,
            "dry_run": dry_run,
        },
    }
