"""Sandboxed check-command execution for the author agent.

Phase 0.1 integration. The author agent's job format includes a ``checks``
list — arbitrary shell strings (think ``pytest tests/`` or ``mypy .``) that
must succeed before the agent commits a change. Until now those strings
ran with ``shell=True`` on the host, inheriting the host's environment,
filesystem, and network.

This module ships :func:`run_command_sandboxed` — a drop-in replacement
that returns the same dict shape but routes the command through
:class:`apps._shared.sandbox.SandboxRunner`. The shell-string contract is
preserved by wrapping each command as ``["sh", "-c", command]`` inside the
agent's per-principal image.

Default behaviour is *unchanged* on the production host until you set
``AUTHOR_AGENT_SANDBOX_CHECKS=1`` in the systemd unit. The wiring in
:mod:`apps.author_agent.main` reads that env at task-execution time.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Make apps._shared.* importable when invoked from inside the author_agent
# package (which appends apps/ but not the repo root to sys.path).
_THIS = Path(__file__).resolve()
_REPO_ROOT = _THIS.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from apps._shared.audit import AuditLog  # noqa: E402
from apps._shared.registry import RegistryError, load_registry  # noqa: E402
from apps._shared.sandbox import SandboxError, SandboxRunner  # noqa: E402


# How the env-flag is spelled. Kept here so docs/tests reference one symbol.
SANDBOX_FLAG_ENV = "AUTHOR_AGENT_SANDBOX_CHECKS"


class SandboxedCheckError(RuntimeError):
    """Raised when the sandbox cannot even be launched (image missing,
    podman not on PATH, manifest malformed, etc.).

    Distinct from a check that *ran* and exited non-zero — that case is
    surfaced through the returned ``returncode`` field, identical to the
    host-execution path."""


def sandbox_checks_enabled() -> bool:
    """Honor ``AUTHOR_AGENT_SANDBOX_CHECKS`` env (default off).

    Off by default so that flipping the import of this module does not
    change production behaviour. Set ``=1`` in the agent's systemd unit
    once the image is built and a live smoke has succeeded.
    """
    raw = os.environ.get(SANDBOX_FLAG_ENV, "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def run_command_sandboxed(
    command: str,
    *,
    worktree: Path,
    principal: str = "agent:homelab",
    audit: AuditLog | None = None,
    timeout_seconds: float = 600.0,
    registry_loader=load_registry,
    runner_cls=SandboxRunner,
) -> dict[str, Any]:
    """Run a job's check string inside the principal's sandbox.

    Returns the same ``{command, returncode, stdout, stderr}`` shape the
    legacy :func:`apps.author_agent.main.run_command` returns, so callers
    in ``execute_task`` can swap implementations behind an env flag.

    The image tag and egress allowlist are read from the principal's
    manifest (``sandbox.base_image`` / ``sandbox.network.allowed_hosts``).
    The worktree is bind-mounted read-write at ``/work``; the container
    runs as the manifest's UID, with ``--read-only`` rootfs, ``--cap-drop=ALL``,
    ``--security-opt=no-new-privileges``, ``--memory=2g``, and ``--pids-limit=512``.

    ``registry_loader`` and ``runner_cls`` are injectable for tests.
    """
    try:
        registry = registry_loader()
    except RegistryError as exc:
        raise SandboxedCheckError(f"registry load failed: {exc}") from exc

    try:
        manifest = registry.get(principal)
    except RegistryError as exc:
        raise SandboxedCheckError(
            f"principal {principal!r} not in registry"
        ) from exc

    base_image = manifest.get("sandbox", "base_image")
    if not base_image:
        raise SandboxedCheckError(
            f"{principal}: manifest.sandbox.base_image is required for sandboxed checks"
        )
    image = f"{base_image}:latest"
    allowed_hosts = tuple(
        manifest.get("sandbox", "network", "allowed_hosts", default=[]) or []
    )

    try:
        runner = runner_cls(
            principal=principal,
            image=image,
            worktree_path=Path(worktree),
            allowed_hosts=allowed_hosts,
        )
        result = runner.run(
            command=("sh", "-c", command),
            timeout_seconds=timeout_seconds,
        )
    except SandboxError as exc:
        raise SandboxedCheckError(f"sandbox launch failed: {exc}") from exc

    if audit is not None:
        try:
            audit.append({
                "principal": principal,
                "event": "sandbox_check",
                "command": command,
                "image": image,
                "network_mode": result.network_mode,
                "egress_allowed": list(result.egress_allowed),
                "resolved_egress": [list(pair) for pair in result.resolved_egress],
                "unresolved_egress": list(result.unresolved_egress),
                "container_name": result.container_name,
                "correlation_id": result.correlation_id,
                "exit_code": result.exit_code,
                "duration_seconds": round(result.duration_seconds, 3),
                "stdout_len": len(result.stdout),
                "stderr_len": len(result.stderr),
                "timeout_seconds": timeout_seconds,
            })
        except Exception as exc:  # noqa: BLE001
            # Do not let an audit-append problem mask the underlying check
            # outcome — the check did run and the caller needs the result.
            print(
                f"warning: sandbox_check audit append failed: {exc}",
                file=sys.stderr,
            )

    return {
        "command": command,
        "returncode": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
