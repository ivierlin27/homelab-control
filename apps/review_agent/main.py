#!/usr/bin/env python3
"""Review-agent policy evaluator and durable queue worker."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps"))

from agentlib import extract_links, forgejo_request, load_json, parse_pr_url, write_json  # noqa: E402


DEFAULT_POLICY = ROOT / "config" / "policies" / "review-policy.yaml"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text()) or {}


def starts_with_any(value: str, prefixes: list[str]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)


def has_plan_link(pr: dict[str, Any]) -> bool:
    if pr.get("has_plan_link") is not None:
        return bool(pr["has_plan_link"])
    body = pr.get("body", "")
    return any("plan" in link.lower() for link in extract_links(body))


def has_planka_card(pr: dict[str, Any]) -> bool:
    if pr.get("has_planka_card") is not None:
        return bool(pr["has_planka_card"])
    body = pr.get("body", "")
    return any("planka" in link.lower() for link in extract_links(body))


def checks_are_green(pr: dict[str, Any]) -> bool:
    if pr.get("checks_passed") is not None:
        return bool(pr["checks_passed"])
    return bool(pr.get("checks", [])) and all(check.get("state") == "success" for check in pr["checks"])


def evaluate(policy: dict[str, Any], pr: dict[str, Any]) -> dict[str, Any]:
    labels = set(pr.get("labels", []))
    changed_files = pr.get("changed_files", [])
    checks_passed = checks_are_green(pr)
    reasons: list[str] = []

    if policy["request_changes"].get("if_checks_failed", False) and not checks_passed:
        reasons.append("required checks are not green")
    if policy["request_changes"].get("if_missing_plan_link", False) and not has_plan_link(pr):
        reasons.append("missing plan link")
    if policy["request_changes"].get("if_missing_planka_card", False) and not has_planka_card(pr):
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


def fetch_pull_request(input_payload: dict[str, Any]) -> dict[str, Any]:
    base_url = input_payload.get("forgejo_base_url") or os.environ.get("FORGEJO_BASE_URL", "")
    token = input_payload.get("forgejo_api_token") or os.environ.get("FORGEJO_API_TOKEN", "")
    if not base_url or not token:
        return input_payload

    if input_payload.get("pr_url"):
        parsed = parse_pr_url(input_payload["pr_url"])
        base_url = parsed["base_url"]
        owner = parsed["owner"]
        repo = parsed["repo"]
        number = parsed["number"]
    else:
        owner = input_payload["repo_owner"]
        repo = input_payload["repo"]
        number = int(input_payload["pr_number"])

    pr = forgejo_request(base_url, f"repos/{owner}/{repo}/pulls/{number}", token=token)
    files = forgejo_request(base_url, f"repos/{owner}/{repo}/pulls/{number}/files", token=token)
    statuses = forgejo_request(base_url, f"repos/{owner}/{repo}/commits/{pr['head']['sha']}/status", token=token)

    labels = [label["name"] for label in pr.get("labels", [])]
    combined = {
        **input_payload,
        "forgejo_base_url": base_url,
        "repo_owner": owner,
        "repo": repo,
        "pr_number": number,
        "pr_url": pr.get("html_url", input_payload.get("pr_url", "")),
        "title": pr.get("title", input_payload.get("title", "")),
        "body": pr.get("body", input_payload.get("body", "")),
        "labels": labels or input_payload.get("labels", []),
        "changed_files": [item["filename"] for item in files] or input_payload.get("changed_files", []),
        "checks": statuses.get("statuses", []) if isinstance(statuses, dict) else [],
        "commit_sha": pr["head"]["sha"],
        "head_ref": pr["head"]["ref"],
        "base_ref": pr["base"]["ref"],
    }
    if "checks_passed" not in combined:
        combined["checks_passed"] = statuses.get("state") == "success" if isinstance(statuses, dict) else False
    return combined


def post_review_comment(pr: dict[str, Any], decision: dict[str, Any]) -> str | None:
    base_url = pr.get("forgejo_base_url") or os.environ.get("FORGEJO_BASE_URL", "")
    token = pr.get("forgejo_api_token") or os.environ.get("FORGEJO_API_TOKEN", "")
    if not base_url or not token or not pr.get("pr_number") or not pr.get("repo_owner") or not pr.get("repo"):
        return None

    body = "\n".join(
        [
            f"Review agent decision: `{decision['decision']}`",
            "",
            "Reasons:",
            *[f"- {reason}" for reason in decision["reasons"]],
        ]
    )
    comment = forgejo_request(
        base_url,
        f"repos/{pr['repo_owner']}/{pr['repo']}/issues/{pr['pr_number']}/comments",
        token=token,
        method="POST",
        payload={"body": body},
    )
    return comment.get("html_url")


def merge_pull_request(pr: dict[str, Any], decision: dict[str, Any]) -> bool:
    if decision["decision"] != "approve_and_merge":
        return False
    if os.environ.get("REVIEW_AGENT_ALLOW_AUTO_MERGE", "").lower() not in {"1", "true", "yes"}:
        return False

    base_url = pr.get("forgejo_base_url") or os.environ.get("FORGEJO_BASE_URL", "")
    token = pr.get("forgejo_api_token") or os.environ.get("FORGEJO_API_TOKEN", "")
    if not base_url or not token:
        return False

    forgejo_request(
        base_url,
        f"repos/{pr['repo_owner']}/{pr['repo']}/pulls/{pr['pr_number']}/merge",
        token=token,
        method="POST",
        payload={"Do": "merge", "merge_message_field": f"Auto-merged by {os.environ.get('AGENT_PRINCIPAL', 'agent:review')}"},
    )
    return True


def build_result(job: dict[str, Any], job_path: Path, done_dir: Path) -> dict[str, Any]:
    action = normalize_action(job["action"])
    if action not in {"evaluate", "evaluate-review", "review-pr"}:
        raise ValueError(f"unsupported action: {job['action']}")

    policy_path = Path(job.get("policy", DEFAULT_POLICY))
    policy = load_yaml(policy_path)
    if "pull_request" in job:
        pr_input = job["pull_request"]
    else:
        pr_input = load_json(Path(job["input"]))
    pr = fetch_pull_request(pr_input)
    decision = evaluate(policy, pr)
    decision["reviewed_at"] = utc_now()
    decision["pr_url"] = pr.get("pr_url", "")
    decision["pr_number"] = pr.get("pr_number")
    decision["comment_url"] = post_review_comment(pr, decision)
    decision["merged"] = merge_pull_request(pr, decision)
    output_path = Path(job.get("output_path", default_output_path(done_dir, job_path)))
    write_json(output_path, decision)
    return {
        "action": action,
        "job_file": str(job_path),
        "output_path": str(output_path),
        "completed_at": utc_now(),
        "decision": decision["decision"],
        "pr_url": decision.get("pr_url", ""),
        "merged": decision["merged"],
    }


def process_job(job_path: Path, queue_dir: Path) -> dict[str, Any]:
    dirs = ensure_queue_dirs(queue_dir)
    processing_path = dirs["processing"] / job_path.name
    shutil.move(str(job_path), processing_path)

    try:
        job = load_json(processing_path)
        result = build_result(job, processing_path, dirs["done"])
        receipt_path = dirs["done"] / f"{processing_path.stem}.receipt.json"
        write_json(receipt_path, result)
        shutil.move(str(processing_path), dirs["done"] / processing_path.name)
        return result
    except Exception as exc:
        error = {
            "job_file": str(processing_path),
            "failed_at": utc_now(),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        write_json(dirs["failed"] / f"{processing_path.stem}.error.json", error)
        shutil.move(str(processing_path), dirs["failed"] / processing_path.name)
        raise


def write_heartbeat(path: Path, queue_dir: Path, processed_jobs: int, current_job: str | None) -> None:
    dirs = ensure_queue_dirs(queue_dir)
    payload = {
        "agent": os.environ.get("AGENT_PRINCIPAL", "agent:review"),
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
    write_json(path, payload)


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

    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    evaluate_parser.add_argument("--input", required=True, help="JSON file describing the PR/MR")

    review_parser = subparsers.add_parser("review-pr")
    review_parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    review_parser.add_argument("--input", required=True)

    process = subparsers.add_parser("process-job")
    process.add_argument("--job", required=True)
    process.add_argument("--queue-dir", required=True)

    queue_status_parser = subparsers.add_parser("queue-status")
    queue_status_parser.add_argument("--queue-dir", required=True)

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

    if args.command == "review-pr":
        policy = load_yaml(Path(args.policy))
        pr = fetch_pull_request(load_json(Path(args.input)))
        print(json.dumps(evaluate(policy, pr), indent=2))
        return 0

    if args.command == "process-job":
        result = process_job(Path(args.job), Path(args.queue_dir))
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "queue-status":
        print(json.dumps(queue_status(Path(args.queue_dir)), indent=2))
        return 0

    if args.command == "worker":
        return run_worker(Path(args.queue_dir), Path(args.heartbeat), args.poll_interval)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
