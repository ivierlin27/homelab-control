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
