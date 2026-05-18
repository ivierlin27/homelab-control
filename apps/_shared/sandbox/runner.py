"""Rootless Podman wrapper that gives every agent a least-privilege sandbox.

Design goals (see docs/plans/phase-0-platform.md section 0.1):

- Default deny: no network egress unless the agent's manifest lists hosts.
- Minimal mounts: only the worktree by default; everything else opt-in.
- Per-agent image: tag matches `sandbox.base_image` from the registry.
- Capture stdout, stderr, exit code, and a session JSONL into the audit log.

This module does not import the registry directly; the caller resolves an
``AgentManifest`` and passes the relevant fields. That keeps the runner
unit-testable without a registry on disk.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping, Sequence


class SandboxError(Exception):
    """Raised on any failure to launch or run a sandbox."""


class BranchStrategy(str, Enum):
    """Where the agent's work lands relative to git branches."""

    HEAD = "head"
    MERGE_TO_HEAD = "merge-to-head"
    BRANCH = "branch"


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of one sandbox run."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    container_name: str
    image: str
    correlation_id: str
    network_mode: str
    egress_allowed: tuple[str, ...]


@dataclass
class SandboxRunner:
    """Configured runner for one agent's sandbox.

    Construct with the manifest-derived values, then call ``run()`` per task.
    The runner is stateless across runs; each ``run()`` produces a fresh
    container.
    """

    principal: str
    image: str
    worktree_path: Path
    allowed_hosts: Sequence[str] = field(default_factory=tuple)
    extra_mounts: Sequence[tuple[Path, Path, bool]] = field(default_factory=tuple)
    """Tuples of (host_path, container_path, read_only)."""
    env: Mapping[str, str] = field(default_factory=dict)
    podman_path: str = "podman"
    branch_strategy: BranchStrategy = BranchStrategy.MERGE_TO_HEAD

    def __post_init__(self) -> None:
        if not self.principal.startswith("agent:"):
            raise SandboxError(f"principal must start with 'agent:', got {self.principal!r}")
        if not self.image:
            raise SandboxError("image is required")
        if not isinstance(self.worktree_path, Path):
            object.__setattr__(self, "worktree_path", Path(self.worktree_path))
        if not self.worktree_path.is_dir():
            raise SandboxError(f"worktree_path is not a directory: {self.worktree_path}")

    # ------------------------------------------------------------------
    # public surface
    # ------------------------------------------------------------------

    def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float = 600.0,
        correlation_id: str | None = None,
        capture_session_to: Path | None = None,
    ) -> SandboxResult:
        """Run ``command`` inside a fresh sandbox.

        ``command`` is the argv passed to the container's ENTRYPOINT (tini).
        The container is removed on exit. ``timeout_seconds`` aborts the run
        if it exceeds the budget; the container is force-stopped and the
        outcome is reported with ``exit_code=124`` (matching `timeout(1)`).
        """

        if not command:
            raise SandboxError("command must be a non-empty sequence")
        if not _which(self.podman_path):
            raise SandboxError(
                f"podman binary not found on PATH (looked up {self.podman_path!r})"
            )

        cid = correlation_id or str(uuid.uuid4())
        container_name = f"sb-{self.principal.replace(':', '-')}-{cid[:8]}"
        network_mode, egress_args = self._network_args()

        argv: list[str] = [
            self.podman_path,
            "run",
            "--rm",
            "--name",
            container_name,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=512",
            "--memory=2g",
            "--workdir=/work",
            # Rootless podman: map container UID 1000 (the image's `agent`
            # user) to the host UID running this process, so the bind-mounted
            # worktree is readable/writable without chown gymnastics. Without
            # this the container's `agent` user maps to a host subuid in the
            # /etc/subuid range and can't read host-owned mounts.
            "--userns=keep-id",
            *self._mount_args(),
            *self._env_args(cid),
            *egress_args,
            self.image,
            *command,
        ]

        start = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603 (controlled argv, no shell)
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            self._force_stop(container_name)
            exit_code = 124
            stdout = exc.stdout.decode("utf-8", "replace") if exc.stdout else ""
            stderr = (
                exc.stderr.decode("utf-8", "replace") if exc.stderr else ""
            ) + f"\nsandbox: timed out after {timeout_seconds:.1f}s"
        duration = time.monotonic() - start

        result = SandboxResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            container_name=container_name,
            image=self.image,
            correlation_id=cid,
            network_mode=network_mode,
            egress_allowed=tuple(self.allowed_hosts),
        )

        if capture_session_to is not None:
            _write_session_jsonl(capture_session_to, self.principal, command, result)

        return result

    # ------------------------------------------------------------------
    # internal: argv builders
    # ------------------------------------------------------------------

    def _mount_args(self) -> list[str]:
        # The worktree mounts read-write so the agent can produce diffs.
        mounts: list[tuple[Path, Path, bool]] = [(self.worktree_path, Path("/work"), False)]
        mounts.extend(self.extra_mounts)
        out: list[str] = []
        for host, sandbox, read_only in mounts:
            spec = f"type=bind,source={host},target={sandbox}"
            if read_only:
                spec += ",ro=true"
            out.extend(["--mount", spec])
        return out

    def _env_args(self, correlation_id: str) -> list[str]:
        out: list[str] = ["-e", f"SANDBOX_CORRELATION_ID={correlation_id}",
                          "-e", f"SANDBOX_PRINCIPAL={self.principal}"]
        for key, value in sorted(self.env.items()):
            if "=" in key:
                raise SandboxError(f"invalid env key {key!r}")
            out.extend(["-e", f"{key}={value}"])
        return out

    def _network_args(self) -> tuple[str, list[str]]:
        """Return (network_mode_label, podman args).

        With no allowed hosts, the container is launched with --network=none.
        With one or more, we use Podman's pasta/slirp4netns DNS interception
        via --network=slirp4netns and host-mapped DNS; egress is then
        further constrained by adding --add-host entries plus an iptables
        deny rule injected via the agent image's entrypoint (deferred to a
        later ticket; see TODO below).

        TODO(0.1): wire --network=slirp4netns + per-host DNS allowlist.
        Until then, declaring allowed_hosts switches to the host network and
        relies on the OS-level firewall for enforcement. The runner records
        the chosen mode in SandboxResult.network_mode for audit clarity.
        """

        if not self.allowed_hosts:
            return "none", ["--network=none"]
        # TODO: replace with strict per-host DNS allowlist.
        return "slirp4netns", ["--network=slirp4netns:enable_ipv6=false"]

    def _force_stop(self, container_name: str) -> None:
        try:
            subprocess.run(  # noqa: S603
                [self.podman_path, "kill", container_name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


# ---------------------------------------------------------------------------
# convenience top-level helper
# ---------------------------------------------------------------------------


def run(
    *,
    principal: str,
    image: str,
    worktree_path: Path | str,
    command: Sequence[str],
    allowed_hosts: Iterable[str] = (),
    extra_mounts: Iterable[tuple[Path, Path, bool]] = (),
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = 600.0,
    correlation_id: str | None = None,
    capture_session_to: Path | None = None,
) -> SandboxResult:
    """One-shot helper that constructs a ``SandboxRunner`` and runs once."""

    runner = SandboxRunner(
        principal=principal,
        image=image,
        worktree_path=Path(worktree_path),
        allowed_hosts=tuple(allowed_hosts),
        extra_mounts=tuple(extra_mounts),
        env=dict(env or {}),
    )
    return runner.run(
        command=tuple(command),
        timeout_seconds=timeout_seconds,
        correlation_id=correlation_id,
        capture_session_to=capture_session_to,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _which(name: str) -> str | None:
    return shutil.which(name)


def _write_session_jsonl(
    path: Path,
    principal: str,
    command: Sequence[str],
    result: SandboxResult,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "principal": principal,
        "correlation_id": result.correlation_id,
        "container_name": result.container_name,
        "image": result.image,
        "command": list(command),
        "command_quoted": shlex.join(command),
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "network_mode": result.network_mode,
        "egress_allowed": list(result.egress_allowed),
        "stdout_len": len(result.stdout),
        "stderr_len": len(result.stderr),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
