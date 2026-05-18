"""Scratch-dir helper for sandbox callers.

Under SELinux enforcing (Fedora, RHEL) Podman's ``:Z`` bind-mount relabel
fails on paths under ``/tmp`` because the policy refuses to relabel
``/tmp``. That's a real design choice — ``/tmp`` is system-shared — and
the right answer is to put sandbox worktrees somewhere else.

Callers that need a transient scratch directory should use
:func:`make_scratch_dir` here instead of :mod:`tempfile`. The default
root is ``/var/lib/homelab-control/sandbox/`` which is provisioned with
the ``container_file_t`` SELinux type via ``semanage fcontext`` (see
``docs/runbooks/author-sandbox.md`` for the one-time host setup).

Anything else — a permanent worktree under the user's home, a git
checkout, a queue-managed dir — is fine as long as it isn't under
``/tmp``. The runner's ``__post_init__`` enforces that.

Tests and intentional debugging can override the root via the env var
``HOMELAB_SANDBOX_SCRATCH_ROOT``.
"""

from __future__ import annotations

import os
import shutil
import stat
import uuid
from pathlib import Path


SCRATCH_ROOT_ENV = "HOMELAB_SANDBOX_SCRATCH_ROOT"
DEFAULT_SCRATCH_ROOT = Path("/var/lib/homelab-control/sandbox")


class ScratchError(RuntimeError):
    """Raised when a scratch dir can't be allocated or cleaned safely."""


def default_scratch_root() -> Path:
    """Where new scratch dirs are created. Override via ``HOMELAB_SANDBOX_SCRATCH_ROOT``."""
    override = os.environ.get(SCRATCH_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_SCRATCH_ROOT


def make_scratch_dir(correlation_id: str | None = None, *, mode: int = 0o700) -> Path:
    """Allocate a fresh scratch dir under :func:`default_scratch_root`.

    Refuses to operate if the resolved root is under ``/tmp`` because
    that path can't be SELinux-relabeled and the sandbox runner will
    reject the worktree on launch. Use the env override for tests that
    don't go through podman.
    """
    root = default_scratch_root()
    _refuse_tmp(root, "scratch root")
    root.mkdir(parents=True, exist_ok=True)

    cid = correlation_id or uuid.uuid4().hex
    # Slugify just enough: replace path separators so a UUID-looking ID
    # can't escape the root, but otherwise leave the operator-supplied
    # string readable in audit logs.
    slug = cid.replace(os.sep, "-").replace("..", "-")
    target = (root / slug).resolve()
    if root not in target.parents and target != root:
        # Defence in depth — if normalization put us outside root, bail.
        raise ScratchError(
            f"refusing to create scratch dir outside root: {target} not under {root}"
        )
    target.mkdir(parents=True, exist_ok=False)
    target.chmod(mode)
    return target


def cleanup_scratch_dir(path: Path | str, *, missing_ok: bool = True) -> None:
    """Delete a directory previously returned by :func:`make_scratch_dir`.

    Safety-checked: only removes paths under the configured scratch root.
    That prevents a buggy caller from passing ``Path("/")`` and asking us
    to ``rmtree`` it.
    """
    target = Path(path).resolve()
    root = default_scratch_root()
    if not target.exists():
        if missing_ok:
            return
        raise ScratchError(f"scratch dir does not exist: {target}")
    if root not in target.parents and target != root:
        raise ScratchError(
            f"refusing to clean up path outside scratch root: {target} not under {root}"
        )
    shutil.rmtree(target, ignore_errors=False)


def _path_under(prefix: str, value: str) -> bool:
    return value == prefix or value.startswith(prefix.rstrip("/") + "/")


def _refuse_tmp(path: Path, label: str) -> None:
    """Raise if ``path`` is under ``/tmp`` (the operative root cause of the
    2026-05-18 SELinux incident).

    Honours ``HOMELAB_SANDBOX_ALLOW_TMP=1`` for tests + intentional debugging.
    """
    if _is_under_tmp(path):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        raise ScratchError(
            f"{label} is under /tmp ({resolved}); SELinux policy refuses to relabel "
            f"/tmp paths, which breaks Podman's :Z bind-mount. Use a path under "
            f"{DEFAULT_SCRATCH_ROOT} (default) or set "
            f"{SCRATCH_ROOT_ENV}=/some/other/path. To override (tests only) set "
            f"HOMELAB_SANDBOX_ALLOW_TMP=1."
        )


def _is_under_tmp(path: Path) -> bool:
    """Public-ish helper used by the runner's __post_init__ guard.

    Returns True iff ``path`` is under ``/tmp`` — checked against both the
    literal text the caller supplied AND the realpath, so tests on macOS
    (where ``/tmp`` symlinks to ``/private/tmp``) catch the case identically
    to Linux production. Honours the ``HOMELAB_SANDBOX_ALLOW_TMP`` escape.
    """
    if os.environ.get("HOMELAB_SANDBOX_ALLOW_TMP", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    literal = str(path)
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = literal
    for candidate in (literal, resolved):
        if _path_under("/tmp", candidate) or _path_under("/private/tmp", candidate):
            return True
    return False


# stat helper kept here so callers can sanity-check perms without
# pulling in a second import surface.
def perms_octal(path: Path | str) -> int:
    """Return the file-mode bits of ``path`` (e.g. ``0o700``)."""
    return stat.S_IMODE(Path(path).stat().st_mode)


# ---------------------------------------------------------------------------
# DNS-isolation files
#
# The sandbox runner's strict DNS allowlist (FU3c, 2026-05-18) bind-mounts
# two files into every container that declares ``sandbox.network.allowed_hosts``:
#   - /etc/nsswitch.conf -> hosts: files (only)
#   - /etc/resolv.conf -> empty
# The combination means every libc hostname lookup is satisfied EXCLUSIVELY
# by /etc/hosts, and the runner pre-populates /etc/hosts via ``--add-host``
# entries for the manifest's allowed hosts. Any other name fails with
# NXDOMAIN, defeating accidental egress.
#
# The files live under the scratch root so they inherit the
# ``container_file_t`` SELinux fcontext (set up by the operator once) and
# Podman's ``relabel=shared`` can re-categorize them on each run. We cache
# them across runs since their content never changes per host.
# ---------------------------------------------------------------------------


def dns_isolation_files() -> tuple[Path, Path]:
    """Return ``(nsswitch_path, empty_resolv_path)``, creating them if missing.

    Both live under ``default_scratch_root() / "_dns"``. Safe to call
    repeatedly — files are created with 0644 and content-stable so a
    second caller sees the same bytes.
    """
    dns_dir = default_scratch_root() / "_dns"
    _refuse_tmp(dns_dir, "dns isolation dir")
    dns_dir.mkdir(parents=True, exist_ok=True)

    nsswitch = dns_dir / "nsswitch.conf"
    if not nsswitch.exists():
        nsswitch.write_text(
            "# Generated by apps._shared.sandbox.scratch.dns_isolation_files\n"
            "# Forces /etc/hosts as the ONLY source for hostname lookups.\n"
            "# The sandbox runner's --add-host flags populate /etc/hosts with\n"
            "# the agent manifest's sandbox.network.allowed_hosts; everything\n"
            "# else NXDOMAINs.\n"
            "hosts: files\n"
        )
        nsswitch.chmod(0o644)

    empty_resolv = dns_dir / "empty-resolv.conf"
    if not empty_resolv.exists():
        empty_resolv.write_text(
            "# Intentionally empty. The sandbox uses /etc/hosts only — see\n"
            "# nsswitch.conf in the same dir. No upstream DNS server is\n"
            "# reachable for resolution and that is the point.\n"
        )
        empty_resolv.chmod(0o644)

    return nsswitch, empty_resolv
