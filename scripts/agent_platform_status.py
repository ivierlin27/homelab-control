#!/usr/bin/env python3
"""Aggregate queue, heartbeat, and review backlog status for the agent platform."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import request


def utc_now() -> datetime:
    return datetime.now(UTC)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def queue_snapshot(queue_dir: Path) -> dict[str, Any]:
    counts = {}
    for name in ("inbox", "processing", "done", "failed"):
        path = queue_dir / name
        counts[name] = len(list(path.glob("*.json")))
    failed_jobs = sorted(path.name for path in (queue_dir / "failed").glob("*.json"))
    return {"queue_dir": str(queue_dir), "counts": counts, "failed_jobs": failed_jobs}


def heartbeat_snapshot(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if not payload:
        return {"path": str(path), "present": False}

    updated_at = payload.get("updated_at", "")
    age_seconds: float | None = None
    if updated_at:
        updated = datetime.fromisoformat(updated_at)
        age_seconds = round((utc_now() - updated).total_seconds(), 1)
    return {
        "path": str(path),
        "present": True,
        "updated_at": updated_at,
        "age_seconds": age_seconds,
        "processed_jobs": payload.get("processed_jobs", 0),
        "current_job": payload.get("current_job"),
        "counts": payload.get("counts", {}),
    }


def forgejo_json(base_url: str, api_path: str, token: str) -> Any:
    req = request.Request(
        f"{base_url.rstrip('/')}/api/v1/{api_path.lstrip('/')}",
        headers={"Authorization": f"token {token}", "Accept": "application/json"},
    )
    with request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def review_backlog(base_url: str, repo_owner: str, repo_name: str, token: str) -> list[dict[str, Any]]:
    if not base_url or not repo_owner or not repo_name or not token:
        return []
    pulls = forgejo_json(base_url, f"repos/{repo_owner}/{repo_name}/pulls?state=open", token)
    backlog: list[dict[str, Any]] = []
    for pr in pulls:
        comments = forgejo_json(base_url, f"repos/{repo_owner}/{repo_name}/issues/{pr['number']}/comments", token)
        latest_decision = ""
        for comment in reversed(comments):
            body = comment.get("body", "")
            if "Review agent decision:" in body:
                if "`needs_human_review`" in body:
                    latest_decision = "needs_human_review"
                elif "`request_changes`" in body:
                    latest_decision = "request_changes"
                elif "`approve_and_merge`" in body:
                    latest_decision = "approve_and_merge"
                if latest_decision:
                    break
        if latest_decision in {"needs_human_review", "request_changes"}:
            backlog.append(
                {
                    "pr_number": pr["number"],
                    "title": pr["title"],
                    "url": pr["html_url"],
                    "latest_decision": latest_decision,
                }
            )
    return backlog


def build_status(args: argparse.Namespace) -> dict[str, Any]:
    author_queue = Path(args.author_queue).expanduser()
    review_queue = Path(args.review_queue).expanduser()
    author_heartbeat = heartbeat_snapshot(Path(args.author_heartbeat).expanduser())
    review_heartbeat = heartbeat_snapshot(Path(args.review_heartbeat).expanduser())
    backlog = review_backlog(args.forgejo_base_url, args.repo_owner, args.repo_name, args.forgejo_api_token)
    stale_threshold = float(args.stale_after_seconds)

    stale_heartbeats = [
        item["path"]
        for item in (author_heartbeat, review_heartbeat)
        if item.get("present") and item.get("age_seconds") is not None and item["age_seconds"] > stale_threshold
    ]
    failed_jobs = {
        "author": queue_snapshot(author_queue)["failed_jobs"],
        "review": queue_snapshot(review_queue)["failed_jobs"],
    }
    status = {
        "generated_at": utc_now().isoformat(),
        "queues": {
            "author": queue_snapshot(author_queue),
            "review": queue_snapshot(review_queue),
        },
        "heartbeats": {
            "author": author_heartbeat,
            "review": review_heartbeat,
        },
        "review_backlog": backlog,
        "stale_heartbeats": stale_heartbeats,
        "healthy": not stale_heartbeats and not failed_jobs["author"] and not failed_jobs["review"],
    }
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--author-queue", required=True)
    parser.add_argument("--review-queue", required=True)
    parser.add_argument("--author-heartbeat", required=True)
    parser.add_argument("--review-heartbeat", required=True)
    parser.add_argument("--forgejo-base-url", default=os.environ.get("FORGEJO_BASE_URL", ""))
    parser.add_argument("--repo-owner", default=os.environ.get("FORGEJO_REPO_OWNER", ""))
    parser.add_argument("--repo-name", default=os.environ.get("FORGEJO_REPO_NAME", ""))
    parser.add_argument("--forgejo-api-token", default=os.environ.get("FORGEJO_API_TOKEN", ""))
    parser.add_argument("--stale-after-seconds", type=float, default=600)
    parser.add_argument("--output")
    args = parser.parse_args()

    status = build_status(args)
    rendered = json.dumps(status, indent=2) + "\n"
    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
