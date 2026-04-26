#!/usr/bin/env python3
"""Author-agent helpers and a durable queue worker."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps"))

from agentlib import forgejo_request, load_json, repo_name_from_path, slugify, write_json, write_text  # noqa: E402


DEFAULT_TEMPLATE = ROOT / "config" / "planka" / "card-template.md"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        "worktrees": queue_dir / "worktrees",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def normalize_action(action: str) -> str:
    return action.strip().lower().replace("_", "-")


def default_output_path(done_dir: Path, job_path: Path, suffix: str) -> Path:
    return done_dir / f"{job_path.stem}{suffix}"


def repo_root_from_job(job: dict[str, Any]) -> Path:
    repo_path = Path(job.get("repo_path", ROOT)).expanduser().resolve()
    if repo_path != ROOT:
        raise ValueError(f"unsupported repo path for author agent: {repo_path}")
    return repo_path


def require_allowed_path(path: Path, worktree: Path, allowed_paths: list[str]) -> str:
    resolved = path.resolve()
    try:
        relpath = str(resolved.relative_to(worktree.resolve()))
    except ValueError as exc:
        raise ValueError(f"path escapes worktree: {path}") from exc

    if allowed_paths and not any(relpath == prefix or relpath.startswith(f"{prefix.rstrip('/')}/") for prefix in allowed_paths):
        raise ValueError(f"path outside allowed scope: {relpath}")
    return relpath


def run_command(command: str, *, cwd: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=merged_env,
        shell=True,
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def ensure_success(result: dict[str, Any]) -> None:
    if result["returncode"] != 0:
        raise RuntimeError(
            f"command failed: {result['command']}\nstdout:\n{result['stdout']}\nstderr:\n{result['stderr']}"
        )


def post_lifecycle_callback(job: dict[str, Any], payload: dict[str, Any]) -> str | None:
    url = job.get("lifecycle_callback_url", "")
    if not url:
        return None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = job.get("lifecycle_callback_token", "")
    if token:
        headers["X-Agent-Dispatch-Token"] = token
    req = request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=20) as response:
            return response.read().decode("utf-8")
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


def apply_operations(operations: dict[str, Any], *, worktree: Path, allowed_paths: list[str]) -> list[str]:
    touched_paths: list[str] = []

    for item in operations.get("replacements", []):
        target = worktree / item["path"]
        relpath = require_allowed_path(target, worktree, allowed_paths)
        content = target.read_text()
        old = item["old_string"]
        new = item["new_string"]
        if old not in content:
            raise ValueError(f"old_string not found in {relpath}")
        if item.get("replace_all", False):
            content = content.replace(old, new)
        else:
            content = content.replace(old, new, 1)
        target.write_text(content)
        touched_paths.append(relpath)

    for item in operations.get("append_text", []):
        target = worktree / item["path"]
        relpath = require_allowed_path(target, worktree, allowed_paths)
        content = target.read_text() if target.exists() else ""
        if content and not content.endswith("\n"):
            content += "\n"
        content += item["text"]
        if not content.endswith("\n"):
            content += "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        touched_paths.append(relpath)

    for item in operations.get("write_files", []):
        target = worktree / item["path"]
        relpath = require_allowed_path(target, worktree, allowed_paths)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["content"])
        touched_paths.append(relpath)

    for item in operations.get("delete_files", []):
        target = worktree / item["path"]
        relpath = require_allowed_path(target, worktree, allowed_paths)
        if target.exists():
            target.unlink()
            touched_paths.append(relpath)

    return sorted(set(touched_paths))


def git_lines(repo_path: Path, *args: str) -> list[str]:
    env = git_env()
    completed = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def git_env() -> dict[str, str]:
    env = os.environ.copy()
    if os.environ.get("AGENT_GIT_SSH_COMMAND"):
        env["GIT_SSH_COMMAND"] = os.environ["AGENT_GIT_SSH_COMMAND"]

    author_name = os.environ.get("AGENT_GIT_AUTHOR_NAME", "")
    author_email = os.environ.get("AGENT_GIT_AUTHOR_EMAIL", "")
    if author_name and author_email:
        env["GIT_AUTHOR_NAME"] = author_name
        env["GIT_AUTHOR_EMAIL"] = author_email
        env["GIT_COMMITTER_NAME"] = author_name
        env["GIT_COMMITTER_EMAIL"] = author_email
    return env


def create_worktree(job: dict[str, Any], queue_dir: Path, job_path: Path) -> tuple[Path, str, str]:
    repo_root = repo_root_from_job(job)
    repo_name = repo_name_from_path(repo_root)
    branch_name = job.get("branch_name") or f"agent/{job_path.stem}-{slugify(job.get('title', repo_name))}"
    base_branch = job.get("base_branch", "main")
    remote_name = job.get("git_remote", os.environ.get("AGENT_GIT_REMOTE", "forgejo"))
    worktree = (queue_dir / "worktrees" / job_path.stem).resolve()

    if worktree.exists():
        subprocess.run(
            ["git", "-C", str(repo_root), "worktree", "remove", "--force", str(worktree)],
            check=False,
            capture_output=True,
            text=True,
            env=git_env(),
        )
        if worktree.exists():
            shutil.rmtree(worktree)
    subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "prune", "--expire", "now"],
        check=True,
        capture_output=True,
        text=True,
        env=git_env(),
    )

    refs = set(git_lines(repo_root, "for-each-ref", "--format=%(refname:short)"))
    if branch_name in refs or f"{remote_name}/{branch_name}" in refs:
        branch_name = f"{branch_name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    base_ref = f"{remote_name}/{base_branch}" if f"{remote_name}/{base_branch}" in refs else base_branch
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "add", "-B", branch_name, str(worktree), base_ref],
        check=False,
        capture_output=True,
        text=True,
        env=git_env(),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git worktree add failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}")
    return worktree, branch_name, remote_name


def build_pr_body(job: dict[str, Any], changed_files: list[str], checks: list[dict[str, Any]]) -> str:
    summary_lines = job.get("summary_lines") or [
        job.get("summary")
        or job.get("title")
        or "Apply the requested homelab-control change through the author-agent execution path."
    ]
    test_lines = []
    for result in checks:
        status = "pass" if result["returncode"] == 0 else "fail"
        test_lines.append(f"- [{ 'x' if status == 'pass' else ' ' }] `{result['command']}`")

    labels = ", ".join(job.get("labels", [])) or "(none)"
    plan_link = job.get("plan_link", "")
    planka_card = job.get("planka_card", "")
    next_planka_list = job.get("next_planka_list", "")

    body = [
        "## Summary",
        *[f"- {line}" for line in summary_lines if line],
        "",
        "## Changed files",
        *[f"- `{path}`" for path in changed_files],
        "",
        "## Test plan",
        *(test_lines or ["- [ ] No automated checks were configured"]),
        "",
        "## Metadata",
        f"- Risk labels: {labels}",
        f"- Plan: {plan_link or '(none)'}",
        f"- Planka card: {planka_card or '(none)'}",
        *(["", f"Next Planka list: {next_planka_list}"] if next_planka_list else []),
    ]
    return "\n".join(body).strip() + "\n"


def build_review_context(
    job: dict[str, Any],
    *,
    pr_url: str,
    pr_number: int,
    branch_name: str,
    changed_files: list[str],
    checks: list[dict[str, Any]],
    commit_sha: str,
) -> dict[str, Any]:
    return {
        "pr_url": pr_url,
        "pr_number": pr_number,
        "card_id": job.get("card_id", ""),
        "repo": job.get("repo_name", repo_name_from_path(repo_root_from_job(job))),
        "labels": job.get("labels", []),
        "checks_passed": all(result["returncode"] == 0 for result in checks),
        "has_plan_link": bool(job.get("plan_link")),
        "has_planka_card": bool(job.get("planka_card")),
        "plan_link": job.get("plan_link", ""),
        "planka_card": job.get("planka_card", ""),
        "changed_files": changed_files,
        "commit_sha": commit_sha,
        "branch_name": branch_name,
        "lifecycle_callback_url": job.get("lifecycle_callback_url", ""),
        "lifecycle_callback_token": job.get("lifecycle_callback_token", ""),
    }


def create_execution_job_from_card(card_path: Path, output_path: Path) -> dict[str, Any]:
    card = load_json(card_path)
    execution = card.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("card is missing an execution object")
    card_id = str(card.get("id", "")).strip()
    title = card.get("title") or card.get("name") or "Untitled task"
    branch_name = execution.get("branch_name")
    if not branch_name:
        slug_root = slugify(title)
        branch_name = f"agent/card-{card_id}-{slug_root}" if card_id else f"agent/{slug_root}"
    payload = {
        "action": "execute-task",
        "title": title,
        "summary": card.get("summary", ""),
        "labels": card.get("labels", [card.get("risk", "safe-update")]),
        "plan_link": str(card_path),
        "planka_card": card.get("planka_card") or card.get("url", ""),
        "branch_name": branch_name,
        "card_id": card_id,
        **execution,
    }
    write_json(output_path, payload)
    return payload


def execute_task(job: dict[str, Any], *, job_path: Path, queue_dir: Path, done_dir: Path) -> dict[str, Any]:
    worktree, branch_name, remote_name = create_worktree(job, queue_dir, job_path)
    repo_root = repo_root_from_job(job)
    allowed_paths = job.get("allowed_paths", [])
    operations = job.get("operations", {})
    execution_env = git_env()
    touched_paths = apply_operations(operations, worktree=worktree, allowed_paths=allowed_paths)

    checks = [run_command(command, cwd=worktree, env=execution_env) for command in job.get("checks", [])]
    for result in checks:
        ensure_success(result)

    diff_paths = git_lines(worktree, "status", "--short")
    if not diff_paths:
        raise ValueError("execute-task produced no changes")

    add_args = ["git", "-C", str(worktree), "add", "--"]
    add_targets = sorted(set(allowed_paths or touched_paths))
    subprocess.run(add_args + add_targets, check=True, capture_output=True, text=True, env=execution_env)

    commit_message = job.get("commit_message") or job.get("title") or "Apply author agent task."
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-m", commit_message],
        check=True,
        capture_output=True,
        text=True,
        env=execution_env,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "push", "-u", remote_name, branch_name],
        check=True,
        capture_output=True,
        text=True,
        env=execution_env,
    )

    commit_sha = git_lines(worktree, "rev-parse", "HEAD")[0]
    changed_files = git_lines(worktree, "diff", "--name-only", f"{job.get('base_branch', 'main')}...HEAD")
    if not changed_files:
        changed_files = sorted(set(touched_paths))

    forgejo_base_url = job.get("forgejo_base_url") or os.environ.get("FORGEJO_BASE_URL", "")
    forgejo_token = job.get("forgejo_api_token") or os.environ.get("FORGEJO_API_TOKEN", "")
    repo_name = job.get("repo_name", repo_name_from_path(repo_root))
    repo_owner = job.get("repo_owner") or os.environ.get("FORGEJO_REPO_OWNER", "")
    if not forgejo_base_url or not forgejo_token or not repo_owner:
        raise ValueError("execute-task requires FORGEJO_BASE_URL, FORGEJO_API_TOKEN, and FORGEJO_REPO_OWNER")

    pr_title = job.get("pr_title") or job.get("title") or commit_message
    pr_body = job.get("pr_body") or build_pr_body(job, changed_files, checks)
    pr_payload = {
        "base": job.get("base_branch", "main"),
        "head": branch_name,
        "title": pr_title,
        "body": pr_body,
    }
    pr = forgejo_request(
        forgejo_base_url,
        f"repos/{repo_owner}/{repo_name}/pulls",
        token=forgejo_token,
        method="POST",
        payload=pr_payload,
    )
    pr_number = int(pr["number"])
    pr_url = pr["html_url"]

    review_context = build_review_context(
        job,
        pr_url=pr_url,
        pr_number=pr_number,
        branch_name=branch_name,
        changed_files=changed_files,
        checks=checks,
        commit_sha=commit_sha,
    )
    review_context_path = Path(
        job.get("review_context_path", default_output_path(done_dir, job_path, ".review-context.json"))
    )
    write_json(review_context_path, review_context)

    review_queue_dir = job.get("review_queue_dir")
    review_job_path: str | None = None
    if review_queue_dir:
        target = Path(review_queue_dir).expanduser().resolve() / "inbox" / f"{job_path.stem}.json"
        write_json(target, {"action": "review-pr", "input": str(review_context_path)})
        review_job_path = str(target)

    lifecycle_callback_response = post_lifecycle_callback(
        job,
        {
            "event": "author-pr-opened",
            "card_id": job.get("card_id", ""),
            "pr_url": pr_url,
            "pr_number": pr_number,
            "branch_name": branch_name,
            "review_context_path": str(review_context_path),
            "review_job_path": review_job_path,
        },
    )

    result = {
        "action": "execute-task",
        "job_file": str(job_path),
        "branch_name": branch_name,
        "git_remote": remote_name,
        "repo_root": str(repo_root),
        "worktree": str(worktree),
        "changed_files": changed_files,
        "checks": checks,
        "commit_sha": commit_sha,
        "pr_url": pr_url,
        "pr_number": pr_number,
        "review_context_path": str(review_context_path),
        "review_job_path": review_job_path,
        "lifecycle_callback_response": lifecycle_callback_response,
        "completed_at": utc_now(),
    }
    summary_path = Path(job.get("output_path", default_output_path(done_dir, job_path, ".summary.json")))
    write_json(summary_path, result)
    result["output_path"] = str(summary_path)
    return result


def build_result(job: dict[str, Any], job_path: Path, done_dir: Path, queue_dir: Path) -> dict[str, Any]:
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

    if action == "create-execution-job":
        output_path = Path(job.get("output_path", default_output_path(done_dir, job_path, ".execution.json")))
        payload = create_execution_job_from_card(Path(job["card"]), output_path)
        return {
            "action": action,
            "job_file": str(job_path),
            "output_path": str(output_path),
            "title": payload.get("title", ""),
            "completed_at": utc_now(),
        }

    if action == "execute-task":
        return execute_task(job, job_path=job_path, queue_dir=queue_dir, done_dir=done_dir)

    raise ValueError(f"unsupported action: {job['action']}")


def process_job(job_path: Path, queue_dir: Path) -> dict[str, Any]:
    dirs = ensure_queue_dirs(queue_dir)
    processing_path = dirs["processing"] / job_path.name
    shutil.move(str(job_path), processing_path)

    try:
        job = load_json(processing_path)
        result = build_result(job, processing_path, dirs["done"], queue_dir)
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
        "agent": os.environ.get("AGENT_PRINCIPAL", "agent:homelab"),
        "updated_at": utc_now(),
        "queue_dir": str(queue_dir),
        "processed_jobs": processed_jobs,
        "current_job": current_job,
        "counts": {
            "inbox": len(list(dirs["inbox"].glob("*.json"))),
            "processing": len(list(dirs["processing"].glob("*.json"))),
            "done": len(list(dirs["done"].glob("*.json"))),
            "failed": len(list(dirs["failed"].glob("*.json"))),
            "worktrees": len(list(dirs["worktrees"].glob("*"))),
        },
    }
    write_json(path, payload)


def queue_status(queue_dir: Path) -> dict[str, Any]:
    dirs = ensure_queue_dirs(queue_dir)
    failed_jobs = sorted(path.name for path in dirs["failed"].glob("*.json"))
    return {
        "queue_dir": str(queue_dir),
        "counts": {
            name: len(list(path.glob("*.json"))) if path.is_dir() else 0
            for name, path in dirs.items()
            if name != "worktrees"
        },
        "worktrees": sorted(path.name for path in dirs["worktrees"].glob("*")),
        "failed_jobs": failed_jobs,
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

    render = subparsers.add_parser("render-plan")
    render.add_argument("--card", required=True)
    render.add_argument("--template", default=str(DEFAULT_TEMPLATE))

    summary = subparsers.add_parser("summarize-result")
    summary.add_argument("--card", required=True)
    summary.add_argument("--pr-url", required=True)

    create_execution = subparsers.add_parser("create-execution-job")
    create_execution.add_argument("--card", required=True)
    create_execution.add_argument("--output-path", required=True)

    execute_task_parser = subparsers.add_parser("execute-task")
    execute_task_parser.add_argument("--job", required=True)
    execute_task_parser.add_argument("--queue-dir", required=True)

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

    if args.command == "render-plan":
        print(render_from_card(Path(args.card), Path(args.template)))
        return 0

    if args.command == "summarize-result":
        print(summarize_result(Path(args.card), args.pr_url))
        return 0

    if args.command == "create-execution-job":
        payload = create_execution_job_from_card(Path(args.card), Path(args.output_path))
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "execute-task":
        job = load_json(Path(args.job))
        result = execute_task(job, job_path=Path(args.job), queue_dir=Path(args.queue_dir), done_dir=Path(args.queue_dir) / "done")
        print(json.dumps(result, indent=2))
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
