#!/usr/bin/env python3
"""Homelab project agent that triages intake and delegates to author/review queues."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, request

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps"))

from agentlib import boot_principal, load_json, write_json  # noqa: E402


DEFAULT_POLICY = ROOT / "config" / "policies" / "homelab-maintainer-policy.yaml"
DEFAULT_QUEUE_DIR = Path.home() / ".local" / "state" / "homelab-control" / "agent-homelab-maintainer"
DEFAULT_PRINCIPAL = "agent:homelab-maintainer"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "false"}:
        return value == "true"
    if value.isdigit():
        return int(value)
    if value.startswith("[") and value.endswith("]"):
        raw_items = value[1:-1].strip()
        if not raw_items:
            return []
        return [parse_scalar(item.strip()) for item in raw_items.split(",")]
    return value.strip("\"'")


def simple_yaml_load(text: str) -> dict[str, Any]:
    lines = []
    raw_lines = text.splitlines()
    for index, raw in enumerate(raw_lines):
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((index, indent, raw.strip()))

    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    for pos, (idx, indent, stripped) in enumerate(lines):
        while indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if stripped.startswith("- "):
            if not isinstance(parent, list):
                raise ValueError(f"unsupported YAML list item at line {idx + 1}")
            parent.append(parse_scalar(stripped[2:]))
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            raise ValueError(f"unsupported YAML line {idx + 1}: {stripped}")
        key = key.strip()
        value = value.strip()
        if value:
            parent[key] = parse_scalar(value)
            continue
        next_is_list = False
        for _, next_indent, next_stripped in lines[pos + 1 :]:
            if next_indent <= indent:
                continue
            next_is_list = next_stripped.startswith("- ")
            break
        container: dict[str, Any] | list[Any] = [] if next_is_list else {}
        parent[key] = container
        stack.append((indent, container))
    return root


def load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text()
    if yaml is not None:
        return yaml.safe_load(text) or {}
    return simple_yaml_load(text)


def ensure_queue_dirs(queue_dir: Path) -> dict[str, Path]:
    from apps._shared.a2a import ensure_dirs

    return ensure_dirs(queue_dir)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """Append one JSONL record, hash-chained for audit (Phase 0.3).

    Any pre-existing un-chained lines are preserved as a legacy prefix; the
    chain starts fresh on top. See ``apps/_shared/audit/`` for details.
    """
    import sys
    from pathlib import Path as _P
    _root = _P(__file__).resolve().parents[2]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from apps._shared.audit import AuditLog

    AuditLog(path).append(payload)


def board_labels() -> dict[str, str]:
    from apps._shared.planka_client import planka_request

    board = os.environ.get("PLANKA_BOARD_ID", "")
    if not board:
        return {}
    payload = planka_request(f"boards/{board}")
    labels = payload.get("included", {}).get("labels", [])
    return {label["name"]: label["id"] for label in labels if label.get("name")}


def add_card_label(card_id: str, label_name: str) -> None:
    from apps._shared.planka_client import planka_request

    label_id = board_labels().get(label_name)
    if not card_id or not label_id:
        return
    try:
        planka_request(f"cards/{card_id}/card-labels", method="POST", payload={"labelId": label_id})
    except error.HTTPError as exc:
        if exc.code != 409:
            raise


def create_planka_card(title: str, description: str, labels: list[str]) -> dict[str, Any]:
    from apps._shared.planka_client import planka_new_card_payload, planka_request

    list_id = os.environ.get("PLANKA_HOMELAB_LIST_ID", "") or os.environ.get("PLANKA_INBOX_LIST_ID", "")
    if not list_id:
        raise ValueError("PLANKA_HOMELAB_LIST_ID or PLANKA_INBOX_LIST_ID is required")
    payload = planka_new_card_payload(name=title, description=description)
    created = planka_request(f"lists/{list_id}/cards", method="POST", payload=payload)
    card = created.get("item", created)
    card_id = str(card.get("id", ""))
    for label in labels:
        add_card_label(card_id, label)
    return {"card": card, "list_id": list_id}


def card_url(card_id: str) -> str:
    base_url = os.environ.get("PLANKA_BASE_URL", "").rstrip("/")
    return f"{base_url}/cards/{card_id}" if base_url and card_id else ""


def post_memory(payload: dict[str, Any]) -> dict[str, Any]:
    url = os.environ.get("MEMORY_ENGINE_INGEST_URL", "")
    if not url:
        return {"posted": False, "reason": "MEMORY_ENGINE_INGEST_URL is not set"}
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
    return {"posted": True, "response": json.loads(raw) if raw else {}}


def build_memory_payload(title: str, content: str, metadata: dict[str, Any], artifact_url: str = "") -> dict[str, Any]:
    return {
        "type": "text",
        "content": content,
        "source": "homelab-maintainer",
        "principal": os.environ.get("AGENT_PRINCIPAL", DEFAULT_PRINCIPAL),
        "command_or_api": "homelab_maintainer_agent",
        "artifact_url": artifact_url,
        "metadata": {"record_type": "homelab_maintainer_event", "title": title, **metadata},
    }


def require_allowed_prefixes(prefixes: list[str], values: list[str], *, label: str) -> None:
    if not values or not prefixes:
        return
    for value in values:
        if not any(value == prefix or value.startswith(f"{prefix.rstrip('/')}/") for prefix in prefixes):
            raise ValueError(f"{label} outside allowed prefixes: {value}")


def render_triage_description(job: dict[str, Any]) -> str:
    lines = [
        job.get("content", "").strip(),
        "",
        "## Homelab Maintainer Intake",
        "",
        f"- principal: {os.environ.get('AGENT_PRINCIPAL', DEFAULT_PRINCIPAL)}",
        f"- intake_id: {job.get('intake_id', '')}",
        f"- task_class: {job.get('task_class', '')}",
        f"- symbolic_intent: {job.get('symbolic_intent', '')}",
        f"- route: {(job.get('routing') or {}).get('route', '')}",
        f"- source_kind: {job.get('source_kind', '')}",
        f"- source_ref: {job.get('source_ref', '')}",
        "",
        "## Review Gate",
        "",
        "Execution remains behind author/review agent and human review policy.",
    ]
    return "\n".join(lines)


def triage_intake(job: dict[str, Any], *, queue_dir: Path, policy: dict[str, Any]) -> dict[str, Any]:
    title = job.get("title") or "Homelab intake"
    labels = sorted(set(["assistant-created", "project:homelab", "type:intake", f"type:{job.get('task_class', 'research')}"]))
    card_result = {"created": False}
    subagent_meta: dict[str, Any] | None = None
    description = render_triage_description(job)
    if not bool(job.get("dry_run", False)):
        from apps._shared.gateway_routes import normalize_logical_route
        from apps._shared.subagent.wiring import append_subagent_section, try_researcher_summary

        intake_id = str(job.get("intake_id", "")).strip() or "maintainer-intake"
        route_name = normalize_logical_route(
            str((job.get("routing") or {}).get("route", "local"))
        )
        sub = try_researcher_summary(
            "Summarize this homelab maintainer intake for a Planka triage card. "
            "Highlight risks, suggested next steps, and what should go to author vs review.\n\n"
            f"{job.get('content', '')}",
            parent_correlation_id=intake_id,
            audit_path=queue_dir / "trust-ledger.jsonl",
            policy=policy,
            route=route_name,
            context={
                "intake_id": intake_id,
                "task_class": job.get("task_class", ""),
                "source_kind": job.get("source_kind", ""),
            },
            dry_run=bool(job.get("dry_run", False)),
        )
        if sub is not None:
            subagent_meta = sub.as_dict()
            description = append_subagent_section(description, sub, heading="Researcher summary")
        created = create_planka_card(title, description, labels)
        card = created["card"]
        card_result = {
            "created": True,
            "card_id": str(card.get("id", "")),
            "url": card_url(str(card.get("id", ""))),
            "list_id": created["list_id"],
        }

    delegate_results: dict[str, Any] = {}
    if job.get("author_job"):
        delegate_results["author"] = delegate_author_job(job["author_job"], policy=policy)
    if job.get("review_job"):
        delegate_results["review"] = delegate_review_job(job["review_job"], policy=policy)

    memory_result = {"posted": False, "reason": "memory logging skipped"}
    if bool(job.get("write_memory", True)) and not bool(job.get("dry_run", False)):
        memory_result = post_memory(
            build_memory_payload(
                title,
                render_triage_description(job),
                {
                    "task_class": job.get("task_class", ""),
                    "route": (job.get("routing") or {}).get("route", ""),
                    "record_key": f"homelab.intake.{job.get('intake_id', '')}",
                    "intake_id": job.get("intake_id", ""),
                },
                artifact_url=card_result.get("url", ""),
            )
        )

    event = {
        "event": "triage-intake",
        "occurred_at": utc_now(),
        "principal": os.environ.get("AGENT_PRINCIPAL", DEFAULT_PRINCIPAL),
        "project_domain": "homelab",
        "task_class": job.get("task_class", ""),
        "route": (job.get("routing") or {}).get("route", ""),
        "model_tier": (job.get("routing") or {}).get("model_tier", ""),
        "source_kind": job.get("source_kind", ""),
        "source_ref": job.get("source_ref", ""),
        "intake_id": job.get("intake_id", ""),
        "card": card_result,
        "delegated": delegate_results,
        "requires_human_review": True,
    }
    if subagent_meta:
        event["subagent"] = subagent_meta
    append_jsonl(queue_dir / "trust-ledger.jsonl", event)
    append_jsonl(queue_dir / "lifecycle-events.jsonl", {**event, "event": "turn_end"})
    return {
        "ok": True,
        "card": card_result,
        "memory": memory_result,
        "delegated": delegate_results,
        "subagent": subagent_meta,
    }


def delegate_author_job(author_job: dict[str, Any], *, policy: dict[str, Any]) -> dict[str, Any]:
    from apps._shared.a2a import enqueue, resolve_queue_dir

    prefixes = policy.get("delegate", {}).get("author_allowed_paths", [])
    require_allowed_prefixes(prefixes, author_job.get("allowed_paths", []), label="author path")
    queue_dir = resolve_queue_dir("agent:homelab")
    job_name = author_job.get("job_name", f"maintainer-author-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json")
    path = enqueue(queue_dir, job_name, author_job)
    return {"enqueued": True, "queue_dir": str(queue_dir), "job_path": str(path)}


def delegate_review_job(review_job: dict[str, Any], *, policy: dict[str, Any]) -> dict[str, Any]:
    from apps._shared.a2a import enqueue, resolve_queue_dir

    queue_dir = resolve_queue_dir("agent:review")
    job_name = review_job.get("job_name", f"maintainer-review-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json")
    path = enqueue(queue_dir, job_name, review_job)
    return {"enqueued": True, "queue_dir": str(queue_dir), "job_path": str(path)}


def record_note(job: dict[str, Any], *, queue_dir: Path) -> dict[str, Any]:
    payload = build_memory_payload(
        job.get("title", "Homelab note"),
        job.get("content", ""),
        {
            "record_key": job.get("record_key", "homelab.notes.adhoc"),
            "project_domain": "homelab",
        },
        artifact_url=job.get("artifact_url", ""),
    )
    result = {"posted": False, "reason": "dry_run"}
    if not bool(job.get("dry_run", False)):
        result = post_memory(payload)
    event = {
        "event": "record-note",
        "occurred_at": utc_now(),
        "principal": os.environ.get("AGENT_PRINCIPAL", DEFAULT_PRINCIPAL),
        "project_domain": "homelab",
        "record_key": job.get("record_key", "homelab.notes.adhoc"),
        "artifact_url": job.get("artifact_url", ""),
    }
    append_jsonl(queue_dir / "trust-ledger.jsonl", event)
    append_jsonl(queue_dir / "lifecycle-events.jsonl", event)
    return {"ok": True, "memory": result}


def _task_class_for_job(job: dict[str, Any], action: str) -> str:
    explicit = str(job.get("task_class", "")).strip()
    if explicit:
        return explicit.replace("-", ".")
    slug = action.replace("-", "_")
    return f"homelab.{slug}" if slug else "homelab.unknown"


def execute_job_body(job: dict[str, Any], *, queue_dir: Path) -> dict[str, Any]:
    """Run one maintainer job (raises on failure)."""
    action = str(job.get("action", "")).strip().lower().replace("_", "-")
    policy = load_yaml(Path(job.get("policy", str(DEFAULT_POLICY))))
    if action == "triage-intake":
        return triage_intake(job, queue_dir=queue_dir, policy=policy)
    if action == "delegate-author-job":
        return {"ok": True, "author": delegate_author_job(job["author_job"], policy=policy)}
    if action == "delegate-review-job":
        return {"ok": True, "review": delegate_review_job(job["review_job"], policy=policy)}
    if action == "record-note":
        return record_note(job, queue_dir=queue_dir)
    raise ValueError(f"unsupported homelab-maintainer action: {action}")


def process_job(job_path: Path, queue_dir: Path) -> dict[str, Any]:
    from apps._shared.a2a.queue import (
        archive_inbox_json,
        is_a2a_reply_payload,
        is_a2a_reply_path,
        requeue_for_retry,
    )
    from apps._shared.escalation import AttemptOutcome
    from apps._shared.escalation.factory import build_dispatcher_for_principal

    dirs = ensure_queue_dirs(queue_dir)
    processing_path = dirs["processing"] / job_path.name
    shutil.move(str(job_path), processing_path)
    job = load_json(processing_path)

    # A2A replies are for await_reply only; treating them as jobs re-fired Tier 2.
    if is_a2a_reply_path(processing_path) or is_a2a_reply_payload(job):
        archive_inbox_json(processing_path, queue_dir, stage="done")
        return {
            "ok": True,
            "archived": "a2a_reply",
            "reason": "not a maintainer queue job",
        }
    action = str(job.get("action", "")).strip().lower().replace("_", "-")
    task_class = _task_class_for_job(job, action)
    principal = os.environ.get("AGENT_PRINCIPAL", DEFAULT_PRINCIPAL)
    context: dict[str, str] = {"planka_url": ""}
    if isinstance(job.get("card"), dict):
        context["planka_url"] = str(job["card"].get("url", ""))

    def attempt(attempt_index: int, previous: dict[str, Any] | None) -> tuple[AttemptOutcome, Any, str]:
        try:
            payload = execute_job_body(job, queue_dir=queue_dir)
            if isinstance(payload, dict):
                card = payload.get("card") or {}
                if isinstance(card, dict) and card.get("url"):
                    context["planka_url"] = str(card["url"])
            return (AttemptOutcome.SUCCESS, payload, "job completed")
        except Exception as exc:
            return (AttemptOutcome.HARD_FAIL, None, str(exc))

    dispatcher = build_dispatcher_for_principal(
        principal,
        task_class=task_class,
        attempt=attempt,
        queue_dir=queue_dir,
    )
    try:
        escalation = dispatcher.execute(
            task_class=task_class,
            urgent=bool(job.get("urgent", False)),
            envelope_extra={
                "job_action": action,
                "intake_id": job.get("intake_id", ""),
                "planka_card_url": context["planka_url"],
                "card_url": context["planka_url"],
            },
        )
    except Exception as exc:
        disposition, _ = requeue_for_retry(processing_path, queue_dir, job, str(exc))
        error_payload = {
            "job_file": str(processing_path),
            "failed_at": utc_now(),
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "disposition": disposition,
        }
        target = dirs["dlq"] if disposition == "dlq" else dirs["failed"]
        write_json(target / f"{processing_path.stem}.error.json", error_payload)
        if disposition != "dlq" and processing_path.exists():
            shutil.move(str(processing_path), target / processing_path.name)
        raise

    if escalation.succeeded() or escalation.outcome in {"rerouted", "human_intervention"}:
        result = {
            "ok": True,
            "result": escalation.payload,
            "escalation": {
                "final_tier": escalation.final_tier,
                "outcome": escalation.outcome,
                "transitions": [t.as_audit_dict() for t in escalation.transitions],
            },
        }
        write_json(dirs["done"] / f"{processing_path.stem}.receipt.json", result)
        if processing_path.exists():
            shutil.move(str(processing_path), dirs["done"] / processing_path.name)
        return result

    disposition, retry_count = requeue_for_retry(
        processing_path,
        queue_dir,
        job,
        escalation.blocked_reason or "escalation exhausted",
    )
    error_payload = {
        "job_file": str(processing_path.name),
        "failed_at": utc_now(),
        "error": escalation.blocked_reason,
        "escalation": {
            "final_tier": escalation.final_tier,
            "outcome": escalation.outcome,
            "transitions": [t.as_audit_dict() for t in escalation.transitions],
        },
        "disposition": disposition,
        "retry_count": retry_count,
    }
    target = dirs["dlq"] if disposition == "dlq" else dirs["failed"]
    write_json(target / f"{processing_path.stem}.error.json", error_payload)
    if disposition == "dlq":
        raise RuntimeError(escalation.blocked_reason or "job moved to dlq")
    if processing_path.exists():
        shutil.move(str(processing_path), target / processing_path.name)
    raise RuntimeError(escalation.blocked_reason or "job failed after escalation")


def write_heartbeat(path: Path, queue_dir: Path, processed_jobs: int, current_job: str | None) -> None:
    dirs = ensure_queue_dirs(queue_dir)
    write_json(
        path,
        {
            "agent": os.environ.get("AGENT_PRINCIPAL", DEFAULT_PRINCIPAL),
            "updated_at": utc_now(),
            "queue_dir": str(queue_dir),
            "processed_jobs": processed_jobs,
            "current_job": current_job,
            "counts": {name: len(list(folder.glob("*.json"))) for name, folder in dirs.items()},
        },
    )


def queue_status(queue_dir: Path) -> dict[str, Any]:
    dirs = ensure_queue_dirs(queue_dir)
    return {
        "queue_dir": str(queue_dir),
        "counts": {name: len(list(folder.glob("*.json"))) for name, folder in dirs.items()},
        "failed_jobs": sorted(path.name for path in dirs["failed"].glob("*.json")),
    }


def run_worker(queue_dir: Path, heartbeat_path: Path, poll_interval: float) -> int:
    from apps._shared.a2a.queue import worker_inbox_job_paths

    dirs = ensure_queue_dirs(queue_dir)
    processed_jobs = 0
    current_job: str | None = None
    while True:
        jobs = worker_inbox_job_paths(dirs["inbox"])
        if jobs:
            current_job = jobs[0].name
            write_heartbeat(heartbeat_path, queue_dir, processed_jobs, current_job)
            result = process_job(jobs[0], queue_dir)
            processed_jobs += 1
            current_job = None
            print(json.dumps(result))
            write_heartbeat(heartbeat_path, queue_dir, processed_jobs, current_job)
            continue
        write_heartbeat(heartbeat_path, queue_dir, processed_jobs, current_job)
        time.sleep(poll_interval)


def main() -> int:
    boot_principal(DEFAULT_PRINCIPAL)

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    triage = subparsers.add_parser("triage-intake")
    triage.add_argument("--intake-id", required=True)
    triage.add_argument("--title", default="")
    triage.add_argument("--content", required=True)
    triage.add_argument("--source-kind", default="text")
    triage.add_argument("--source-ref", default="")
    triage.add_argument("--task-class", default="summarize")
    triage.add_argument("--symbolic-intent", default="summarize")
    triage.add_argument("--route", default="local")
    triage.add_argument("--model-tier", default="local")
    triage.add_argument("--dry-run", action="store_true")
    triage.add_argument("--write-memory", action="store_true")
    triage.add_argument("--policy", default=str(DEFAULT_POLICY))
    triage.add_argument("--queue-dir", default=str(DEFAULT_QUEUE_DIR))

    note = subparsers.add_parser("record-note")
    note.add_argument("--title", required=True)
    note.add_argument("--content", required=True)
    note.add_argument("--record-key", default="homelab.notes.adhoc")
    note.add_argument("--artifact-url", default="")
    note.add_argument("--dry-run", action="store_true")
    note.add_argument("--queue-dir", default=str(DEFAULT_QUEUE_DIR))

    status = subparsers.add_parser("queue-status")
    status.add_argument("--queue-dir", default=str(DEFAULT_QUEUE_DIR))

    process = subparsers.add_parser("process-job")
    process.add_argument("--job", required=True)
    process.add_argument("--queue-dir", default=str(DEFAULT_QUEUE_DIR))

    worker = subparsers.add_parser("worker")
    worker.add_argument("--queue-dir", default=str(DEFAULT_QUEUE_DIR))
    worker.add_argument("--heartbeat", required=True)
    worker.add_argument("--poll-interval", type=float, default=5.0)

    args = parser.parse_args()
    if args.command == "triage-intake":
        job = {
            "action": "triage-intake",
            "intake_id": args.intake_id,
            "title": args.title,
            "content": args.content,
            "source_kind": args.source_kind,
            "source_ref": args.source_ref,
            "task_class": args.task_class,
            "symbolic_intent": args.symbolic_intent,
            "routing": {"route": args.route, "model_tier": args.model_tier},
            "dry_run": args.dry_run,
            "write_memory": args.write_memory,
            "policy": args.policy,
        }
        print(json.dumps(triage_intake(job, queue_dir=Path(args.queue_dir).expanduser(), policy=load_yaml(Path(args.policy))), indent=2))
        return 0
    if args.command == "record-note":
        job = {
            "action": "record-note",
            "title": args.title,
            "content": args.content,
            "record_key": args.record_key,
            "artifact_url": args.artifact_url,
            "dry_run": args.dry_run,
        }
        print(json.dumps(record_note(job, queue_dir=Path(args.queue_dir).expanduser()), indent=2))
        return 0
    if args.command == "queue-status":
        print(json.dumps(queue_status(Path(args.queue_dir).expanduser()), indent=2))
        return 0
    if args.command == "process-job":
        print(json.dumps(process_job(Path(args.job).expanduser(), Path(args.queue_dir).expanduser()), indent=2))
        return 0
    if args.command == "worker":
        return run_worker(Path(args.queue_dir).expanduser(), Path(args.heartbeat).expanduser(), args.poll_interval)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
