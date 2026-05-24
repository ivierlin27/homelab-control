"""Shared filesystem queue helpers for agent workers and A2A."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .envelope import A2AEnvelope


DEFAULT_MAX_RETRIES = 3


def ensure_dirs(queue_dir: Path, *, worktrees: bool = False) -> dict[str, Path]:
    """Create standard queue stage directories; return path map."""
    dirs: dict[str, Path] = {
        "inbox": queue_dir / "inbox",
        "processing": queue_dir / "processing",
        "done": queue_dir / "done",
        "failed": queue_dir / "failed",
        "dlq": queue_dir / "dlq",
    }
    if worktrees:
        dirs["worktrees"] = queue_dir / "worktrees"
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def enqueue_inbox(inbox: Path, name: str, payload: dict[str, Any]) -> Path:
    """Write *payload* directly into an inbox directory (no extra ``inbox/`` segment)."""
    inbox = inbox.expanduser().resolve()
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    write_json(path, payload)
    return path


def enqueue(queue_dir: Path, name: str, payload: dict[str, Any]) -> Path:
    """Write *payload* to ``{queue_dir}/inbox/{name}``."""
    return enqueue_inbox(queue_dir / "inbox", name, payload)


def enqueue_envelope(queue_dir: Path, envelope: A2AEnvelope) -> Path:
    """Write an :class:`A2AEnvelope` to the callee inbox."""
    name = f"a2a-{envelope.correlation_id}.json"
    return enqueue(queue_dir, name, envelope.to_dict())


def poll_reply(
    correlation_id: str,
    reply_inbox: Path,
    *,
    poll_interval: float = 2.0,
    timeout_seconds: float = 300.0,
) -> A2AEnvelope | None:
    """Poll *reply_inbox* for a reply matching *correlation_id*.

    Returns ``None`` on timeout. Scans ``*.json`` in the inbox directory.
    """
    deadline = time.monotonic() + timeout_seconds
    reply_inbox = reply_inbox.expanduser().resolve()
    reply_inbox.mkdir(parents=True, exist_ok=True)

    while time.monotonic() < deadline:
        for path in sorted(reply_inbox.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not data.get("is_reply"):
                continue
            if str(data.get("correlation_id", "")) != correlation_id:
                continue
            return A2AEnvelope.from_dict(data)
        time.sleep(poll_interval)
    return None


def resolve_max_retries(job: dict[str, Any], *, default: int = DEFAULT_MAX_RETRIES) -> int:
    """Max processing attempts before a job moves to ``dlq/``."""
    raw = job.get("_max_retries", job.get("max_retries", default))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def requeue_for_retry(
    processing_path: Path,
    queue_dir: Path,
    job: dict[str, Any],
    error: str,
    *,
    max_retries: int | None = None,
) -> tuple[str, int]:
    """Re-enqueue *job* from *processing* or move to ``dlq/`` when retries are exhausted.

    Returns ``("retry", new_count)`` or ``("dlq", last_count)``.
    """
    from datetime import datetime, timezone

    limit = max_retries if max_retries is not None else resolve_max_retries(job)
    retry_count = int(job.get("_retry_count", 0)) + 1
    job["_retry_count"] = retry_count
    job["_last_error"] = error
    job["_retry_at"] = datetime.now(timezone.utc).isoformat()

    dirs = ensure_dirs(queue_dir)
    if retry_count >= limit:
        dlq_path = dirs["dlq"] / processing_path.name
        write_json(dlq_path, job)
        sidecar = dlq_path.with_suffix(".error.json")
        write_json(
            sidecar,
            {
                "error": error,
                "retry_count": retry_count,
                "max_retries": limit,
                "moved_at": job["_retry_at"],
            },
        )
        if processing_path.exists():
            processing_path.unlink()
        return ("dlq", retry_count)

    inbox_path = dirs["inbox"] / processing_path.name
    write_json(inbox_path, job)
    if processing_path.exists():
        processing_path.unlink()
    return ("retry", retry_count)
