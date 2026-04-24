#!/usr/bin/env python3
"""Author-agent helpers and a small durable queue worker."""

from __future__ import annotations

import argparse
import json
import shutil
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE = ROOT / "config" / "planka" / "card-template.md"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def render_from_card(card_path: Path, template_path: Path) -> str:
    card = load_json(card_path)
    template = template_path.read_text()
    title = card.get("title") or card.get("name") or "Untitled task"
    repo = card.get("repo", "homelab-control")
    risk = card.get("risk", "safe-update")
    return (
        template.replace("<!-- one sentence outcome -->", title)
        .replace("- repo:", f"- repo: {repo}")
        .replace("- risk:", f"- risk: {risk}")
    )


def summarize_result(card_path: Path, pr_url: str) -> str:
    card = load_json(card_path)
    return json.dumps(
        {
            "principal": "agent:homelab",
            "title": card.get("title") or card.get("name") or "Untitled task",
            "pr_url": pr_url,
            "status": "author_review_ready",
        },
        indent=2,
    )


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


def default_output_path(done_dir: Path, job_path: Path, suffix: str) -> Path:
    return done_dir / f"{job_path.stem}{suffix}"


def build_result(job: dict[str, Any], job_path: Path, done_dir: Path) -> dict[str, Any]:
    action = normalize_action(job["action"])

    if action == "render-plan":
        template_path = Path(job.get("template", DEFAULT_TEMPLATE))
        content = render_from_card(Path(job["card"]), template_path)
        output_path = Path(job.get("output_path", default_output_path(done_dir, job_path, ".plan.md")))
        write_text(output_path, content)
        return {
            "action": action,
            "job_file": str(job_path),
            "output_path": str(output_path),
            "completed_at": utc_now(),
        }

    if action == "summarize-result":
        content = summarize_result(Path(job["card"]), job["pr_url"])
        output_path = Path(job.get("output_path", default_output_path(done_dir, job_path, ".summary.json")))
        write_text(output_path, content)
        return {
            "action": action,
            "job_file": str(job_path),
            "output_path": str(output_path),
            "completed_at": utc_now(),
        }

    raise ValueError(f"unsupported action: {job['action']}")


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
        "agent": "agent:homelab",
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

    render = subparsers.add_parser("render-plan")
    render.add_argument("--card", required=True)
    render.add_argument("--template", default=str(DEFAULT_TEMPLATE))

    summary = subparsers.add_parser("summarize-result")
    summary.add_argument("--card", required=True)
    summary.add_argument("--pr-url", required=True)

    process = subparsers.add_parser("process-job")
    process.add_argument("--job", required=True)
    process.add_argument("--queue-dir", required=True)

    worker = subparsers.add_parser("worker")
    worker.add_argument("--queue-dir", required=True)
    worker.add_argument("--heartbeat", required=True)
    worker.add_argument("--poll-interval", type=float, default=5.0)

    args = parser.parse_args()

    if args.command == "render-plan":
        print(render_from_card(Path(args.card), Path(args.template)))
        return 0

    if args.command == "summarize-result":
        print(summarize_result(Path(args.card), args.pr_url))
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
