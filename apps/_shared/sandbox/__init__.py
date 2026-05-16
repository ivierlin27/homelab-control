"""Sandboxed agent execution via rootless Podman.

This package wraps rootless Podman with a sandcastle-style API. Agents run in
ephemeral containers built from per-principal images that inherit from a
locked-down base. Network access is default-deny, mounts default to the
worktree only, and every run captures stdout/stderr + a session JSONL into
the audit log.

See `docs/plans/phase-0-platform.md` section 0.1 for the contract this package
implements.
"""

from .runner import (
    BranchStrategy,
    SandboxError,
    SandboxResult,
    SandboxRunner,
    run,
)

__all__ = [
    "BranchStrategy",
    "SandboxError",
    "SandboxResult",
    "SandboxRunner",
    "run",
]
