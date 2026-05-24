"""Shared helpers for A2A / escalation end-to-end tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from apps._shared.a2a import ask_agent
from apps._shared.a2a.test_a2a import _registry, _scaffold_repo, _write_manifest
from apps._shared.registry import Registry, load_registry
from apps.executive_agent import main as executive_main


def build_maintainer_executive_registry(tmp_path: Path) -> tuple[Registry, Path, Path]:
    """Minimal registry with maintainer → executive A2A ACL and isolated queue dirs."""
    _scaffold_repo(tmp_path)
    principals = tmp_path / "config/memory/principals.yaml"
    principals.write_text(
        principals.read_text()
        + "  - id: agent:homelab-maintainer\n    kind: agent\n"
        + "  - id: agent:executive\n    kind: agent\n"
    )
    maint_dir = tmp_path / "maintainer"
    exec_dir = tmp_path / "executive"
    maint_m = _write_manifest(
        tmp_path,
        "agent:homelab-maintainer",
        queue_dir=str(maint_dir),
        callees=["agent:executive"],
    )
    exec_m = _write_manifest(
        tmp_path,
        "agent:executive",
        queue_dir=str(exec_dir),
    )
    reg = _registry(
        tmp_path,
        [("agent:homelab-maintainer", maint_m), ("agent:executive", exec_m)],
    )
    return reg, maint_dir, exec_dir


def process_executive_inbox_job(exec_dir: Path) -> dict[str, Any]:
    """Process one A2A job from the executive inbox (mirrors the systemd worker)."""
    inbox = exec_dir / "inbox"
    jobs = sorted(inbox.glob("a2a-*.json"))
    if not jobs:
        raise FileNotFoundError(f"no A2A jobs in {inbox}")
    return executive_main.process_job(jobs[0], exec_dir)


def ask_agent_with_executive_worker(
    exec_dir: Path,
    *args: Any,
    **kwargs: Any,
):
    """Enqueue to executive and process the job synchronously (worker stand-in)."""
    result = ask_agent(*args, **kwargs)
    process_executive_inbox_job(exec_dir)
    return result


def production_registry_available() -> bool:
    """True when the homelab-control repo registry is loadable from cwd."""
    try:
        load_registry()
        return True
    except Exception:
        return False
