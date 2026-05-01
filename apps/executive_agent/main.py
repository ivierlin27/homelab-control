#!/usr/bin/env python3
"""Executive assistant coordinator with Shield, trust, Planka, and memory hooks."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error, request

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised when PyYAML is unavailable
    yaml = None


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps"))

from agentlib import load_json, slugify, write_json  # noqa: E402


DEFAULT_POLICY = ROOT / "config" / "policies" / "executive-assistant-policy.yaml"
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "homelab-control" / "agent-executive"
DEFAULT_PRINCIPAL = "agent:executive"


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
            parsed = parse_scalar(value)
            parent[key] = parsed
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
    dirs = {
        "inbox": queue_dir / "inbox",
        "processing": queue_dir / "processing",
        "done": queue_dir / "done",
        "failed": queue_dir / "failed",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def contains_pattern(text: str, patterns: list[str]) -> str | None:
    lowered = text.lower()
    for pattern in patterns:
        if pattern.lower() in lowered:
            return pattern
    return None


def shield_scan(policy: dict[str, Any], text: str) -> dict[str, Any]:
    if len(text) > int(policy.get("defaults", {}).get("max_request_chars", 12000)):
        return {"ok": False, "reason": "request exceeds maximum configured length", "category": "size"}

    shield = policy.get("shield", {})
    injection = contains_pattern(text, shield.get("prompt_injection_patterns", []))
    if injection:
        return {"ok": False, "reason": f"prompt-injection pattern matched: {injection}", "category": "prompt_injection"}

    secret = contains_pattern(text, shield.get("secret_patterns", []))
    if secret:
        return {"ok": False, "reason": f"secret-like pattern matched: {secret}", "category": "secret"}

    return {"ok": True, "reason": "passed", "category": "clean"}


def domain_policy(policy: dict[str, Any], domain: str) -> dict[str, Any]:
    domains = policy.get("domains", {})
    if domain not in domains:
        return {"known": False, "decision": policy.get("defaults", {}).get("unknown_domain", "escalate")}
    return {"known": True, **domains[domain]}


def classify_labels(task_type: str, requested_labels: list[str]) -> list[str]:
    labels = ["assistant-created"]
    if task_type:
        labels.append(f"type:{task_type}")
    labels.extend(requested_labels)
    return sorted(set(label for label in labels if label))


def evaluate_request(
    policy: dict[str, Any],
    *,
    text: str,
    domain: str,
    task_type: str,
    labels: list[str],
    request_plan_ready: bool,
) -> dict[str, Any]:
    scan = shield_scan(policy, text)
    if not scan["ok"]:
        return {
            "decision": "blocked",
            "reason": scan["reason"],
            "shield": scan,
            "labels": sorted(set(labels + ["shield-blocked"])),
            "can_create_card": False,
            "can_move_to_plan_ready": False,
        }

    dpolicy = domain_policy(policy, domain)
    if not dpolicy.get("known"):
        return {
            "decision": "escalate",
            "reason": f"unknown domain: {domain}",
            "shield": scan,
            "labels": sorted(set(labels + ["trust-escalation"])),
            "can_create_card": False,
            "can_move_to_plan_ready": False,
        }

    blocked = sorted(set(labels).intersection(dpolicy.get("blocked_labels", [])))
    if blocked:
        return {
            "decision": "escalate",
            "reason": f"blocked label requires human review: {', '.join(blocked)}",
            "shield": scan,
            "labels": sorted(set(labels + ["trust-escalation"])),
            "can_create_card": bool(dpolicy.get("can_create_cards", False)),
            "can_move_to_plan_ready": False,
        }

    if task_type not in dpolicy.get("allowed_task_types", []):
        return {
            "decision": "escalate",
            "reason": f"task type is not allowed for domain: {task_type}",
            "shield": scan,
            "labels": sorted(set(labels + ["trust-escalation"])),
            "can_create_card": bool(dpolicy.get("can_create_cards", False)),
            "can_move_to_plan_ready": False,
        }

    can_create = bool(dpolicy.get("can_create_cards", False))
    allowed_labels = set(dpolicy.get("plan_ready_allowed_labels", []))
    plan_ready_allowed = (
        request_plan_ready
        and bool(dpolicy.get("can_move_to_plan_ready", False))
        and bool(labels)
        and all(label in allowed_labels or label == "assistant-created" for label in labels)
    )
    decision = "plan_ready" if plan_ready_allowed else "create_card"
    return {
        "decision": decision,
        "reason": "request matches executive assistant trust policy",
        "shield": scan,
        "labels": labels,
        "can_create_card": can_create,
        "can_move_to_plan_ready": plan_ready_allowed,
        "trust_level": dpolicy.get("trust_level", 0),
    }


def planka_access_token() -> str:
    token = os.environ.get("PLANKA_API_TOKEN", "")
    if token:
        return token
    base_url = os.environ.get("PLANKA_BASE_URL", "").rstrip("/")
    username = os.environ.get("PLANKA_EMAIL_OR_USERNAME", "")
    password = os.environ.get("PLANKA_PASSWORD", "")
    if not base_url or not username or not password:
        return ""
    body = json.dumps({"emailOrUsername": username, "password": password}).encode("utf-8")
    req = request.Request(
        f"{base_url}/api/access-tokens",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))["item"]


def planka_request(api_path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    base_url = os.environ.get("PLANKA_BASE_URL", "").rstrip("/")
    token = planka_access_token()
    if not base_url or not token:
        raise ValueError("PLANKA_BASE_URL and PLANKA_API_TOKEN or Planka credentials are required")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        f"{base_url}/api/{api_path.lstrip('/')}",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
        method=method,
    )
    with request.urlopen(req, timeout=20) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def list_id_for_decision(decision: dict[str, Any]) -> str:
    if decision.get("can_move_to_plan_ready"):
        return os.environ.get("PLANKA_PLAN_READY_LIST_ID", "")
    return os.environ.get("PLANKA_INBOX_LIST_ID", "")


def render_card_description(request_text: str, decision: dict[str, Any], domain: str, task_type: str) -> str:
    return "\n".join(
        [
            request_text.strip(),
            "",
            "## Executive Assistant Intake",
            "",
            f"- principal: {os.environ.get('AGENT_PRINCIPAL', DEFAULT_PRINCIPAL)}",
            f"- domain: {domain}",
            f"- task_type: {task_type}",
            f"- decision: {decision['decision']}",
            f"- reason: {decision['reason']}",
            "- shield: passed" if decision.get("shield", {}).get("ok") else f"- shield: {decision.get('shield', {}).get('reason', '')}",
            "",
            "## Review Gate",
            "",
            "Execution remains behind existing author/review and human-review policy.",
        ]
    )


def create_planka_card(title: str, description: str, labels: list[str], decision: dict[str, Any]) -> dict[str, Any]:
    list_id = list_id_for_decision(decision)
    if not list_id:
        raise ValueError("PLANKA_INBOX_LIST_ID or PLANKA_PLAN_READY_LIST_ID is required")
    payload = {"name": title, "description": description, "position": 65536}
    created = planka_request(f"lists/{list_id}/cards", method="POST", payload=payload)
    card = created.get("item", created)
    card_id = str(card.get("id", ""))
    for label in labels:
        try:
            add_card_label(card_id, label)
        except Exception:
            continue
    return {"card": card, "list_id": list_id}


def board_labels() -> dict[str, str]:
    board = os.environ.get("PLANKA_BOARD_ID", "")
    if not board:
        return {}
    payload = planka_request(f"boards/{board}")
    labels = payload.get("included", {}).get("labels", [])
    return {label["name"]: label["id"] for label in labels if label.get("name")}


def add_card_label(card_id: str, label_name: str) -> None:
    label_id = board_labels().get(label_name)
    if not card_id or not label_id:
        return
    try:
        planka_request(f"cards/{card_id}/card-labels", method="POST", payload={"labelId": label_id})
    except error.HTTPError as exc:
        if exc.code != 409:
            raise


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


def search_memory(query: str, *, principal: str) -> dict[str, Any]:
    url = os.environ.get("MEMORY_ENGINE_SEARCH_URL", "")
    if not url:
        return {"searched": False, "reason": "MEMORY_ENGINE_SEARCH_URL is not set", "items": []}
    payload = {"query": query, "principal": principal, "limit": 5}
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw) if raw else {}
    items = data.get("items", data.get("results", [])) if isinstance(data, dict) else []
    return {"searched": True, "items": items[:5], "response": data}


def build_memory_payload(
    *,
    title: str,
    request_text: str,
    decision: dict[str, Any],
    card_url: str = "",
    source: str = "cli",
    source_ref: str = "",
    source_user: str = "",
    conversation_id: str = "",
) -> dict[str, Any]:
    content = "\n".join(
        [
            f"Executive assistant decision: {title}",
            "",
            request_text.strip(),
            "",
            f"- decision: {decision['decision']}",
            f"- reason: {decision['reason']}",
            f"- labels: {', '.join(decision.get('labels', []))}",
            f"- card: {card_url or '(not created)'}",
        ]
    )
    return {
        "type": "text",
        "content": content,
        "source": "executive-assistant",
        "principal": os.environ.get("AGENT_PRINCIPAL", DEFAULT_PRINCIPAL),
        "command_or_api": "executive_agent:handle-request",
        "artifact_url": card_url,
        "metadata": {
            "record_type": "executive_assistant_decision",
            "decision": decision["decision"],
            "shield_category": decision.get("shield", {}).get("category", ""),
            "source": source,
            "source_ref": source_ref,
            "source_user": source_user,
            "conversation_id": conversation_id,
        },
    }


def card_url(card_id: str) -> str:
    base_url = os.environ.get("PLANKA_BASE_URL", "").rstrip("/")
    return f"{base_url}/cards/{card_id}" if base_url and card_id else ""


def handle_request(args: argparse.Namespace) -> dict[str, Any]:
    policy = load_yaml(Path(args.policy))
    labels = classify_labels(args.task_type, args.label or [])
    source = getattr(args, "source", "cli")
    source_ref = getattr(args, "source_ref", "")
    source_user = getattr(args, "source_user", "")
    conversation_id = getattr(args, "conversation_id", "")
    decision = evaluate_request(
        policy,
        text=args.request,
        domain=args.domain,
        task_type=args.task_type,
        labels=labels,
        request_plan_ready=args.plan_ready,
    )
    title = args.title or args.request.strip().splitlines()[0][:80] or "Executive assistant request"
    state_dir = Path(args.state_dir).expanduser()
    memory_context = {"searched": False, "reason": "memory lookup skipped", "items": []}
    if args.search_memory:
        memory_context = search_memory(args.request, principal=os.environ.get("AGENT_PRINCIPAL", DEFAULT_PRINCIPAL))
    event = {
        "event": "request-evaluated",
        "occurred_at": utc_now(),
        "principal": os.environ.get("AGENT_PRINCIPAL", DEFAULT_PRINCIPAL),
        "title": title,
        "domain": args.domain,
        "task_type": args.task_type,
        "decision": decision["decision"],
        "reason": decision["reason"],
        "labels": decision["labels"],
        "dry_run": args.dry_run,
        "memory_context_count": len(memory_context.get("items", [])),
        "source": source,
        "source_ref": source_ref,
        "source_user": source_user,
        "conversation_id": conversation_id,
    }

    card_result: dict[str, Any] = {"created": False}
    if decision.get("can_create_card") and not args.dry_run:
        description = render_card_description(args.request, decision, args.domain, args.task_type)
        created = create_planka_card(title, description, decision["labels"], decision)
        card = created["card"]
        card_result = {
            "created": True,
            "card_id": str(card.get("id", "")),
            "url": card_url(str(card.get("id", ""))),
            "list_id": created["list_id"],
        }

    event["card"] = card_result
    append_jsonl(state_dir / "trust-ledger.jsonl", event)
    append_jsonl(state_dir / "lifecycle-events.jsonl", {**event, "event": "turn_end"})

    memory_result = {"posted": False, "reason": "memory logging skipped"}
    if args.write_memory and not args.dry_run:
        memory_result = post_memory(
            build_memory_payload(
                title=title,
                request_text=args.request,
                decision=decision,
                card_url=card_result.get("url", ""),
                source=source,
                source_ref=source_ref,
                source_user=source_user,
                conversation_id=conversation_id,
            )
        )

    return {
        "ok": decision["decision"] not in {"blocked"},
        "title": title,
        "decision": decision,
        "card": card_result,
        "memory_context": memory_context,
        "memory": memory_result,
    }


def weekly_review(state_dir: Path, days: int = 7) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    ledger = [
        row
        for row in read_jsonl(state_dir / "trust-ledger.jsonl")
        if datetime.fromisoformat(row.get("occurred_at", "1970-01-01T00:00:00+00:00")) >= since
    ]
    decisions: dict[str, int] = {}
    domains: dict[str, int] = {}
    for row in ledger:
        decisions[row.get("decision", "unknown")] = decisions.get(row.get("decision", "unknown"), 0) + 1
        domains[row.get("domain", "unknown")] = domains.get(row.get("domain", "unknown"), 0) + 1

    platform_status = {}
    platform_path = state_dir.parent / "platform-status.json"
    if platform_path.exists():
        platform_status = load_json(platform_path)

    return {
        "generated_at": utc_now(),
        "window_days": days,
        "totals": {
            "assistant_events": len(ledger),
            "decisions": decisions,
            "domains": domains,
            "blocked_or_escalated": decisions.get("blocked", 0) + decisions.get("escalate", 0),
        },
        "platform": {
            "healthy": platform_status.get("healthy"),
            "review_backlog_count": len(platform_status.get("review_backlog", [])),
            "stale_heartbeats": platform_status.get("stale_heartbeats", []),
        },
        "summary": render_weekly_summary(ledger, platform_status),
    }


def render_weekly_summary(ledger: list[dict[str, Any]], platform_status: dict[str, Any]) -> list[str]:
    lines = []
    blocked = [row for row in ledger if row.get("decision") in {"blocked", "escalate"}]
    created = [row for row in ledger if row.get("card", {}).get("created")]
    plan_ready = [row for row in ledger if row.get("decision") == "plan_ready"]
    lines.append(f"{len(created)} assistant-created card(s); {len(plan_ready)} request(s) qualified for Plan Ready.")
    if blocked:
        lines.append(f"{len(blocked)} request(s) were blocked or escalated by Shield/trust policy.")
    if platform_status:
        health = "healthy" if platform_status.get("healthy") else "needs attention"
        lines.append(f"Agent platform status is {health}.")
    if not lines:
        lines.append("No executive assistant activity was recorded in this window.")
    return lines


def process_job(job_path: Path, queue_dir: Path) -> dict[str, Any]:
    dirs = ensure_queue_dirs(queue_dir)
    processing_path = dirs["processing"] / job_path.name
    shutil.move(str(job_path), processing_path)
    try:
        job = load_json(processing_path)
        action = str(job.get("action", "")).strip().lower().replace("_", "-")
        if action != "handle-request":
            raise ValueError(f"unsupported executive-agent action: {action}")
        ns = argparse.Namespace(
            request=job["request"],
            title=job.get("title", ""),
            domain=job.get("domain", "homelab"),
            task_type=job.get("task_type", "research"),
            label=job.get("labels", []),
            plan_ready=bool(job.get("plan_ready", False)),
            dry_run=bool(job.get("dry_run", False)),
            search_memory=bool(job.get("search_memory", True)),
            write_memory=bool(job.get("write_memory", True)),
            policy=job.get("policy", str(DEFAULT_POLICY)),
            state_dir=job.get("state_dir", str(queue_dir)),
            source=job.get("source", "queue"),
            source_ref=job.get("source_ref", str(job_path)),
            source_user=job.get("source_user", ""),
            conversation_id=job.get("conversation_id", ""),
        )
        result = handle_request(ns)
        write_json(dirs["done"] / f"{processing_path.stem}.receipt.json", result)
        shutil.move(str(processing_path), dirs["done"] / processing_path.name)
        return result
    except Exception as exc:
        error_payload = {
            "job_file": str(processing_path),
            "failed_at": utc_now(),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        write_json(dirs["failed"] / f"{processing_path.stem}.error.json", error_payload)
        shutil.move(str(processing_path), dirs["failed"] / processing_path.name)
        raise


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
            "counts": {name: len(list(path.glob("*.json"))) for name, path in dirs.items()},
        },
    )


def queue_status(queue_dir: Path) -> dict[str, Any]:
    dirs = ensure_queue_dirs(queue_dir)
    return {
        "queue_dir": str(queue_dir),
        "counts": {name: len(list(path.glob("*.json"))) for name, path in dirs.items()},
        "failed_jobs": sorted(path.name for path in dirs["failed"].glob("*.json")),
    }


def run_worker(queue_dir: Path, heartbeat_path: Path, poll_interval: float) -> int:
    dirs = ensure_queue_dirs(queue_dir)
    processed_jobs = 0
    current_job: str | None = None
    while True:
        jobs = sorted(dirs["inbox"].glob("*.json"))
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
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    handle = subparsers.add_parser("handle-request")
    handle.add_argument("--request", required=True)
    handle.add_argument("--title", default="")
    handle.add_argument("--domain", default="homelab")
    handle.add_argument("--task-type", default="research")
    handle.add_argument("--label", action="append")
    handle.add_argument("--plan-ready", action="store_true")
    handle.add_argument("--dry-run", action="store_true")
    handle.add_argument("--search-memory", action="store_true")
    handle.add_argument("--write-memory", action="store_true")
    handle.add_argument("--policy", default=str(DEFAULT_POLICY))
    handle.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    handle.add_argument("--source", default="cli")
    handle.add_argument("--source-ref", default="")
    handle.add_argument("--source-user", default="")
    handle.add_argument("--conversation-id", default="")

    review = subparsers.add_parser("weekly-review")
    review.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    review.add_argument("--days", type=int, default=7)
    review.add_argument("--output")

    process = subparsers.add_parser("process-job")
    process.add_argument("--job", required=True)
    process.add_argument("--queue-dir", required=True)

    status = subparsers.add_parser("queue-status")
    status.add_argument("--queue-dir", required=True)

    worker = subparsers.add_parser("worker")
    worker.add_argument("--queue-dir", required=True)
    worker.add_argument("--heartbeat", required=True)
    worker.add_argument("--poll-interval", type=float, default=5.0)

    args = parser.parse_args()
    if args.command == "handle-request":
        print(json.dumps(handle_request(args), indent=2))
        return 0
    if args.command == "weekly-review":
        payload = weekly_review(Path(args.state_dir).expanduser(), args.days)
        if args.output:
            write_json(Path(args.output).expanduser(), payload)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "process-job":
        print(json.dumps(process_job(Path(args.job), Path(args.queue_dir)), indent=2))
        return 0
    if args.command == "queue-status":
        print(json.dumps(queue_status(Path(args.queue_dir)), indent=2))
        return 0
    if args.command == "worker":
        return run_worker(Path(args.queue_dir), Path(args.heartbeat), args.poll_interval)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
