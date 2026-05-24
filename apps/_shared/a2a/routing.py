"""Registry-driven routing and ACL for A2A."""

from __future__ import annotations

import os
from pathlib import Path

from ..registry import Registry, load_registry

from .errors import A2ANotAllowedError, A2ARoutingError


def resolve_queue_dir(
    principal: str,
    registry: Registry | None = None,
) -> Path:
    """Return the expanded queue root for *principal* from the registry."""
    reg = registry or load_registry()
    try:
        manifest = reg.get(principal)
    except Exception as exc:
        raise A2ARoutingError(f"unknown principal: {principal}") from exc

    queue_dir = manifest.data.get("queue_dir")
    if not isinstance(queue_dir, str) or not queue_dir.strip():
        raise A2ARoutingError(
            f"{manifest.path}: principal {principal!r} has no queue_dir"
        )
    return Path(os.path.expanduser(queue_dir)).resolve()


def resolve_inbox(principal: str, registry: Registry | None = None) -> Path:
    return resolve_queue_dir(principal, registry) / "inbox"


def assert_callee_allowed(
    caller: str,
    callee: str,
    registry: Registry | None = None,
) -> None:
    """Raise :class:`A2ANotAllowedError` if *caller* may not invoke *callee*."""
    reg = registry or load_registry()
    try:
        manifest = reg.get(caller)
    except Exception as exc:
        raise A2ARoutingError(f"unknown caller principal: {caller}") from exc

    if caller == callee:
        raise A2ANotAllowedError(f"{caller} may not call itself via A2A")

    allowed = manifest.get("a2a", "allowed_callees", default=[]) or []
    if callee not in allowed:
        raise A2ANotAllowedError(
            f"{caller} is not allowed to call {callee}; "
            f"allowed_callees={allowed!r}"
        )

    # Callee must exist in registry (load_registry already validated callees).
    try:
        reg.get(callee)
    except Exception as exc:
        raise A2ARoutingError(f"unknown callee principal: {callee}") from exc
