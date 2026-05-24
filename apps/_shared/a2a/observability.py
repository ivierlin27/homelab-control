"""Read-only A2A queue and escalation observability for operators and the dashboard."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .queue import ensure_dirs, is_a2a_reply_path, is_a2a_reply_payload, worker_inbox_job_paths

DEFAULT_STATE_ROOT = Path.home() / ".local/state/homelab-control"

# Principals that participate in the A2A bus (queue dir names under state root).
A2A_QUEUE_DIRS: tuple[tuple[str, str], ...] = (
    ("executive", "agent-executive"),
    ("maintainer", "agent-homelab-maintainer"),
)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def queue_a2a_metrics(queue_dir: Path) -> dict[str, Any]:
    """Per-agent queue snapshot with A2A-specific inbox breakdown."""
    queue_dir = queue_dir.expanduser()
    if not queue_dir.is_dir():
        return {"present": False, "queue_dir": str(queue_dir)}

    dirs = ensure_dirs(queue_dir)
    inbox = dirs["inbox"]
    inbox_files = list(inbox.glob("*.json"))
    stuck_replies = 0
    a2a_requests = 0
    for path in inbox_files:
        if is_a2a_reply_path(path):
            stuck_replies += 1
            continue
        payload = _load_json(path)
        if is_a2a_reply_payload(payload):
            stuck_replies += 1
            continue
        if path.name.startswith("a2a-") or payload.get("correlation_id"):
            a2a_requests += 1

    counts = {
        name: len(list(stage.glob("*.json")))
        for name, stage in dirs.items()
        if name != "worktrees"
    }
    worker_jobs = len(worker_inbox_job_paths(inbox))

    return {
        "present": True,
        "queue_dir": str(queue_dir),
        "counts": counts,
        "inbox_total": len(inbox_files),
        "inbox_a2a_requests": a2a_requests,
        "inbox_stuck_replies": stuck_replies,
        "inbox_worker_jobs": worker_jobs,
        "failed_jobs": sorted(p.name for p in dirs["failed"].glob("*.json")),
        "dlq_jobs": sorted(p.name for p in dirs["dlq"].glob("*.json")),
    }


def recent_help_requests(
    executive_queue_dir: Path,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Recent ``a2a-help-request`` rows from the executive trust ledger."""
    ledger = executive_queue_dir.expanduser() / "trust-ledger.jsonl"
    if not ledger.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") != "a2a-help-request":
            continue
        rows.append(
            {
                "occurred_at": row.get("occurred_at", ""),
                "correlation_id": row.get("correlation_id", ""),
                "caller": row.get("caller", ""),
                "task_class": row.get("task_class", ""),
                "card_created": bool((row.get("card") or {}).get("created")),
                "dry_run": bool(row.get("dry_run")),
            }
        )
    return rows[-limit:]


def tier3_pending_summary(state_root: Path) -> dict[str, Any]:
    path = state_root.expanduser() / "escalation" / "tier3-pending.jsonl"
    if not path.is_file():
        return {"path": str(path), "open": 0, "unacknowledged": 0}
    open_count = 0
    unack = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("acknowledged"):
            continue
        open_count += 1
        if not row.get("dm_sent"):
            unack += 1
    return {"path": str(path), "open": open_count, "unacknowledged": unack}


def build_a2a_status(state_root: Path | None = None) -> dict[str, Any]:
    """Aggregate A2A health for dashboard tiles and ``platform-status.json``."""
    root = (state_root or DEFAULT_STATE_ROOT).expanduser()
    agents: dict[str, Any] = {}
    alerts: list[str] = []

    for label, dirname in A2A_QUEUE_DIRS:
        metrics = queue_a2a_metrics(root / dirname)
        agents[label] = metrics
        if not metrics.get("present"):
            continue
        if metrics.get("inbox_stuck_replies", 0) > 0:
            alerts.append(f"{label}: {metrics['inbox_stuck_replies']} reply file(s) in inbox")
        if metrics.get("dlq_jobs"):
            alerts.append(f"{label}: {len(metrics['dlq_jobs'])} job(s) in dlq/")
        if label == "executive" and metrics.get("inbox_a2a_requests", 0) > 15:
            alerts.append(
                f"executive: deep help_request backlog ({metrics['inbox_a2a_requests']} a2a inbox)"
            )

    exec_dir = root / "agent-executive"
    status = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state_root": str(root),
        "agents": agents,
        "tier3_pending": tier3_pending_summary(root),
        "recent_help_requests": recent_help_requests(exec_dir),
        "alerts": alerts,
        "healthy": not alerts,
    }
    return status
