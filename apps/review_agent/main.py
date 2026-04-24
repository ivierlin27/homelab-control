#!/usr/bin/env python3
"""Review-agent policy evaluator and durable queue worker."""

from __future__ import annotations

import argparse
import json
import shutil
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / "config" / "policies" / "review-policy.yaml"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text()) or {}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def starts_with_any(value: str, prefixes: list[str]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)


def evaluate(policy: dict[str, Any], pr: dict[str, Any]) -> dict[str, Any]:
    labels = set(pr.get("labels", []))
    changed_files = pr.get("changed_files", [])
    checks_passed = bool(pr.get("checks_passed", False))
    reasons: list[str] = []

    if policy["request_changes"].get("if_checks_failed", False) and not checks_passed:
        reasons.append("required checks are not green")
    if policy["request_changes"].get("if_missing_plan_link", False) and not pr.get("has_plan_link", False):
        reasons.append("missing plan link")
    if policy["request_changes"].get("if_missing_planka_card", False) and not pr.get("has_planka_card", False):
        reasons.append("missing Planka card")
    if policy["request_changes"].get("if_missing_risk_marker", False) and not labels:
        reasons.append("missing risk label")
    if reasons:
        return {"decision": "request_changes", "reasons": reasons}

    human = policy["human_review"]
    if labels.intersection(human.get("required_labels", [])):
        return {
            "decision": "needs_human_review",
            "reasons": [f"matched human-review label(s): {sorted(labels.intersection(human['required_labels']))}"],
        }

    for path in changed_files:
        if starts_with_any(path, human.get("required_path_prefixes", [])):
            return {
                "decision": "needs_human_review",
                "reasons": [f"path requires human review: {path}"],
            }
        if Path(path).name in human.get("new_service_files", []):
            return {
                "decision": "needs_human_review",
                "reasons": [f"new service manifest touched: {path}"],
            }

    auto = policy["auto_merge"]
    forbidden = auto.get("forbidden_path_prefixes", [])
    if any(starts_with_any(path, forbidden) for path in changed_files):
        return {
            "decision": "needs_human_review",
            "reasons": ["forbidden path for auto-merge touched"],
        }

    allowed_prefixes = auto.get("allowed_path_prefixes", [])
    if changed_files and all(starts_with_any(path, allowed_prefixes) for path in changed_files):
        if labels.intersection(auto.get("allowed_labels", [])) or all(
            starts_with_any(path, allowed_prefixes) for path in changed_files
        ):
            return {
                "decision": "approve_and_merge",
                "reasons": ["all changed files are inside auto-merge-safe prefixes and checks passed"],
            }

    return {
        "decision": policy["defaults"].get("on_ambiguity", "needs_human_review"),
        "reasons": ["change did not match any explicit safe auto-merge policy"],
    }


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


def normalize_action(action: str) -> str:
    return action.strip().lower().replace("_", "-")


def default_output_path(done_dir: Path, job_path: Path) -> Path:
    return done_dir / f"{job_path.stem}.decision.json"


def build_result(job: dict[str, Any], job_path: Path, done_dir: Path) -> dict[str, Any]:
    action = normalize_action(job["action"])
    if action not in {"evaluate", "evaluate-review"}:
        raise ValueError(f"unsupported action: {job['action']}")

    policy_path = Path(job.get("policy", DEFAULT_POLICY))
    policy = load_yaml(policy_path)
    if "pull_request" in job:
        pr = job["pull_request"]
    else:
        pr = load_json(Path(job["input"]))
    result = evaluate(policy, pr)
    result["reviewed_at"] = utc_now()
    output_path = Path(job.get("output_path", default_output_path(done_dir, job_path)))
    write_text(output_path, json.dumps(result, indent=2))
    return {
        "action": action,
        "job_file": str(job_path),
        "output_path": str(output_path),
        "completed_at": utc_now(),
        "decision": result["decision"],
    }


def process_job(job_path: Path, queue_dir: Path) -> dict[str, Any]:
    dirs = ensure_queue_dirs(queue_dir)
    processing_path = dirs["processing"] / job_path.name
    shutil.move(str(job_path), processing_path)

    try:
        job = load_json(processing_path)
        result = build_result(job, processing_path, dirs["done"])
        receipt_path = dirs["done"] / f"{processing_path.stem}.receipt.json"
        write_text(receipt_path, json.dumps(result, indent=2))
        shutil.move(str(processing_path), dirs["done"] / processing_path.name)
        return result
    except Exception as exc:
        error = {
            "job_file": str(processing_path),
            "failed_at": utc_now(),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        write_text(dirs["failed"] / f"{processing_path.stem}.error.json", json.dumps(error, indent=2))
        shutil.move(str(processing_path), dirs["failed"] / processing_path.name)
        raise


def write_heartbeat(path: Path, queue_dir: Path, processed_jobs: int, current_job: str | None) -> None:
    dirs = ensure_queue_dirs(queue_dir)
    payload = {
        "agent": "agent:review",
        "updated_at": utc_now(),
        "queue_dir": str(queue_dir),
        "processed_jobs": processed_jobs,
        "current_job": current_job,
        "counts": {
            "inbox": len(list(dirs["inbox"].glob("*.json"))),
            "processing": len(list(dirs["processing"].glob("*.json"))),
            "done": len(list(dirs["done"].glob("*.json"))),
            "failed": len(list(dirs["failed"].glob("*.json"))),
        },
    }
    write_text(path, json.dumps(payload, indent=2))


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

    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    evaluate_parser.add_argument("--input", required=True, help="JSON file describing the PR/MR")

    process = subparsers.add_parser("process-job")
    process.add_argument("--job", required=True)
    process.add_argument("--queue-dir", required=True)

    worker = subparsers.add_parser("worker")
    worker.add_argument("--queue-dir", required=True)
    worker.add_argument("--heartbeat", required=True)
    worker.add_argument("--poll-interval", type=float, default=5.0)

    args = parser.parse_args()

    if args.command == "evaluate":
        policy = load_yaml(Path(args.policy))
        pr = load_json(Path(args.input))
        print(json.dumps(evaluate(policy, pr), indent=2))
        return 0

    if args.command == "process-job":
        result = process_job(Path(args.job), Path(args.queue_dir))
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "worker":
        return run_worker(Path(args.queue_dir), Path(args.heartbeat), args.poll_interval)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
