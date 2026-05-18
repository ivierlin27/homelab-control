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
import os
import shlex
import shutil
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


class SandboxError(Exception):
    """Raised on any failure to launch or run a sandbox."""


# Type alias for the resolver injection point. Returns the dotted-quad IP
# string for a given hostname, or raises socket.gaierror on failure. Real
# callers use socket.gethostbyname; tests inject a dict-based fake.
Resolver = Callable[[str], str]


SKIP_UNRESOLVED_ENV = "HOMELAB_SANDBOX_SKIP_UNRESOLVED"


def _default_resolver(hostname: str) -> str:
    """Resolve ``hostname`` to its first IPv4 address.

    We deliberately prefer IPv4: the runner forces ``enable_ipv6=false`` in
    slirp4netns, so AAAA-only entries would be unreachable inside the
    sandbox even if libc handed them back. AF_INET filters them out
    cleanly. Raises ``socket.gaierror`` on failure (or
    ``OSError`` subclasses like ``EAI_NONAME``).
    """
    infos = socket.getaddrinfo(hostname, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    if not infos:
        raise socket.gaierror(socket.EAI_NONAME, f"no IPv4 record for {hostname}")
    return infos[0][4][0]


def _resolve_allowed_hosts(
    allowed_hosts: Sequence[str],
    *,
    resolver: Resolver | None = None,
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Resolve every host in ``allowed_hosts`` to a single IPv4 address.

    Returns ``(resolved, unresolved)`` where ``resolved`` is a tuple of
    ``(hostname, ip)`` pairs and ``unresolved`` lists hostnames that
    couldn't be looked up.

    Default behaviour is **fail loud**: a single resolution failure raises
    ``SandboxError`` before the container starts. Setting the env var
    ``HOMELAB_SANDBOX_SKIP_UNRESOLVED=1`` flips to soft-skip: the failed
    host is omitted from the allowlist, the run continues, and the
    auditing surface reports it via ``unresolved_egress``.

    The soft-skip path exists for the LAN-DNS-briefly-down case the user
    flagged when picking this option (2026-05-18). It is NOT a production
    setting by default — operators should set it deliberately when they
    know upstream resolution is flaky and would rather let the agent fail
    on the actual request than on launch.

    ``resolver`` defaults to None, in which case the function looks up
    the module-level ``_default_resolver`` at CALL TIME. That's critical
    for test monkeypatching: a default-arg of ``_default_resolver``
    would capture the original function object at def time and make
    ``monkeypatch.setattr(runner_mod, '_default_resolver', ...)`` a
    no-op (see CI failure 26045493776 on 2026-05-18).
    """
    if resolver is None:
        # Late-bound lookup so monkeypatching the module-level name works.
        resolver = _default_resolver
    skip = os.environ.get(SKIP_UNRESOLVED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    resolved: list[tuple[str, str]] = []
    unresolved: list[str] = []
    for host in allowed_hosts:
        try:
            ip = resolver(host)
        except (OSError, socket.gaierror) as exc:
            if not skip:
                raise SandboxError(
                    f"failed to resolve allowed host {host!r}: {exc}. "
                    f"Set {SKIP_UNRESOLVED_ENV}=1 to soft-skip (the run "
                    f"will continue without this host in the allowlist)."
                ) from exc
            unresolved.append(host)
            continue
        resolved.append((host, ip))
    return tuple(resolved), tuple(unresolved)


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
    # Strict-DNS allowlist (FU3c, 2026-05-18). Resolved (name, ip) tuples
    # that the runner injected via --add-host. ``unresolved_egress`` is
    # populated only when HOMELAB_SANDBOX_SKIP_UNRESOLVED=1 lets a failed
    # lookup soft-skip; otherwise resolution failure raises SandboxError
    # before the container starts. Both default to () for back-compat.
    resolved_egress: tuple[tuple[str, str], ...] = ()
    unresolved_egress: tuple[str, ...] = ()


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
        # SELinux re-tightening guard (2026-05-18). The bind mount uses :Z to
        # let Podman relabel the worktree to match the container's MCS
        # categories. Fedora SELinux policy refuses to relabel /tmp paths,
        # so we reject them up front with a pointer at the supported root.
        # Tests can set HOMELAB_SANDBOX_ALLOW_TMP=1 to bypass.
        from apps._shared.sandbox.scratch import _is_under_tmp  # local import: avoid cycle on import-time
        if _is_under_tmp(self.worktree_path):
            raise SandboxError(
                f"worktree_path resolves under /tmp ({self.worktree_path}); "
                "SELinux policy will refuse Podman's :Z relabel on /tmp. "
                "Use apps._shared.sandbox.scratch.make_scratch_dir() or any "
                "path under /var/lib/homelab-control/sandbox/. Override for "
                "tests with HOMELAB_SANDBOX_ALLOW_TMP=1."
            )

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
        network_mode, egress_args, dns_mount_args, resolved, unresolved = self._egress_plan()

        argv: list[str] = [
            self.podman_path,
            "run",
            "--rm",
            "--name",
            container_name,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            # SELinux MCS labels are kept on: the worktree mount uses :Z so
            # Podman relabels the host path with the container's per-run
            # category set. Required setup: scratch dirs live under
            # /var/lib/homelab-control/sandbox/ (container_file_t via
            # `semanage fcontext`). /tmp is refused at __post_init__ — see
            # apps._shared.sandbox.scratch.
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
            *dns_mount_args,
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
            resolved_egress=resolved,
            unresolved_egress=unresolved,
        )

        if capture_session_to is not None:
            _write_session_jsonl(capture_session_to, self.principal, command, result)

        return result

    # ------------------------------------------------------------------
    # internal: argv builders
    # ------------------------------------------------------------------

    def _mount_args(self) -> list[str]:
        """Build the --mount specs.

        Every bind mount adds ``relabel=shared`` (the long form of Podman's
        ``:Z`` flag for ``--mount`` syntax — ``--volume`` accepts ``:Z`` but
        ``--mount`` wants ``relabel=shared|private``). Without it, the
        container's MCS categories (``container_t:s0:cN,cM``) won't match the
        host bind target's label and SELinux denies access. With it, Podman
        ``chcon``s the host path to match the container's per-run categories.

        Note: Podman's ``relabel=shared`` (equivalent to ``:z``) sets a single
        ``container_file_t:s0`` label that any container can read. We chose
        the shared variant over ``relabel=private`` (``:Z``) because callers
        with long-lived worktrees would otherwise have their labels reset on
        every fresh container, hurting concurrent access. Security outcome is
        identical for our 'one runner per worktree' usage; the choice is
        operational.
        """
        # (host, container, read_only, relabel)
        mounts: list[tuple[Path, Path, bool, bool]] = [
            (self.worktree_path, Path("/work"), False, True)
        ]
        for entry in self.extra_mounts:
            # Backward-compat: accept the legacy 3-tuple (host, container, ro).
            if len(entry) == 3:
                host, sandbox, read_only = entry  # type: ignore[misc]
                relabel = True
            elif len(entry) == 4:
                host, sandbox, read_only, relabel = entry  # type: ignore[misc]
            else:
                raise SandboxError(
                    f"extra_mounts entries must be 3- or 4-tuples, got {entry!r}"
                )
            mounts.append((Path(host), Path(sandbox), bool(read_only), bool(relabel)))

        out: list[str] = []
        for host, sandbox, read_only, relabel in mounts:
            spec = f"type=bind,source={host},target={sandbox}"
            if read_only:
                spec += ",ro=true"
            if relabel:
                spec += ",relabel=shared"
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

    def _egress_plan(self) -> tuple[
        str,
        list[str],
        list[str],
        tuple[tuple[str, str], ...],
        tuple[str, ...],
    ]:
        """Compute the network mode + podman flags + DNS-isolation mounts.

        Returns
        -------
        ``(network_mode, podman_network_args, dns_isolation_mount_args,
        resolved, unresolved)``

        With no allowed hosts: ``--network=none`` and no DNS mounts.

        With one or more allowed hosts (FU3c, 2026-05-18): strict DNS
        allowlist enforced via three layers:
          1. Each manifest-declared host is resolved on the HOST via libc
             getaddrinfo and injected into the container's /etc/hosts via
             ``--add-host=<name>:<ip>``.
          2. /etc/nsswitch.conf is bind-mounted from the shared
             ``apps._shared.sandbox.scratch.dns_isolation_files()`` set,
             configured as ``hosts: files`` only — no DNS, no mdns.
          3. /etc/resolv.conf is bind-mounted to an empty file so that any
             code that bypasses nsswitch (e.g., direct UDP to a nameserver
             from /etc/resolv.conf) has nothing to talk to.

        Failure-mode: a single unresolvable allowed host raises
        SandboxError unless ``HOMELAB_SANDBOX_SKIP_UNRESOLVED=1`` is set —
        see :func:`_resolve_allowed_hosts`.

        Known limitation: slirp4netns still NATs to the host LAN. An agent
        with a hardcoded LAN/WAN IP can still reach it. Closing that gap
        requires host-side nftables/eBPF egress filtering and is tracked
        separately (sandbox_followup_nftables).
        """
        if not self.allowed_hosts:
            return "none", ["--network=none"], [], (), ()

        resolved, unresolved = _resolve_allowed_hosts(self.allowed_hosts)
        net_args: list[str] = ["--network=slirp4netns:enable_ipv6=false"]
        for host, ip in resolved:
            net_args.append(f"--add-host={host}:{ip}")

        # Lazy import so unit tests on dev laptops without /var/lib access
        # don't fail at import time. The function creates files under the
        # scratch root if absent; the SELinux fcontext is set on the dir.
        from apps._shared.sandbox.scratch import dns_isolation_files

        nsswitch_path, empty_resolv_path = dns_isolation_files()
        dns_mounts: list[str] = [
            "--mount",
            f"type=bind,source={nsswitch_path},target=/etc/nsswitch.conf,ro=true,relabel=shared",
            "--mount",
            f"type=bind,source={empty_resolv_path},target=/etc/resolv.conf,ro=true,relabel=shared",
        ]

        return "slirp4netns-dns-allowlist", net_args, dns_mounts, resolved, unresolved

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
        "resolved_egress": [list(pair) for pair in result.resolved_egress],
        "unresolved_egress": list(result.unresolved_egress),
        "stdout_len": len(result.stdout),
        "stderr_len": len(result.stderr),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
