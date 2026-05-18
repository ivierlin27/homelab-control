"""Maintenance-lock file format + load/save/check helpers."""

from __future__ import annotations

import getpass
import json
import os
import socket
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path


DEFAULT_LOCK_PATH = Path(os.environ.get(
    "MAINTENANCE_LOCK_FILE",
    str(Path.home() / ".config/homelab-control/maintenance.lock"),
))


@dataclass
class MaintenanceLock:
    started_at: float           # epoch seconds
    until: float                # epoch seconds; hard deadline
    reason: str
    started_by: str             # user@host
    # If non-empty, only checks whose ``name`` starts with one of these
    # prefixes are suppressed. Empty list = global maintenance (everything
    # is in scope).
    scope: list[str] = field(default_factory=list)

    def is_active(self, *, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return self.started_at <= now < self.until

    def covers(self, check_name: str) -> bool:
        """Is ``check_name`` in scope of this maintenance window?"""
        if not self.scope:
            return True
        return any(check_name.startswith(p) for p in self.scope)

    def remaining(self, *, now: float | None = None) -> timedelta:
        now = now if now is not None else time.time()
        return timedelta(seconds=max(0, int(self.until - now)))

    def as_dict(self) -> dict:
        d = asdict(self)
        d["started_at_iso"] = datetime.fromtimestamp(self.started_at, UTC).isoformat()
        d["until_iso"] = datetime.fromtimestamp(self.until, UTC).isoformat()
        return d


# ----- io -----------------------------------------------------------------

def load_lock(path: Path | str = DEFAULT_LOCK_PATH) -> MaintenanceLock | None:
    """Return the active lock, or None if no lock exists OR it has expired."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        lock = MaintenanceLock(
            started_at=float(data["started_at"]),
            until=float(data["until"]),
            reason=str(data.get("reason", "")),
            started_by=str(data.get("started_by", "")),
            scope=list(data.get("scope", []) or []),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not lock.is_active():
        return None
    return lock


def is_active(check_name: str | None = None, *, path: Path | str = DEFAULT_LOCK_PATH) -> bool:
    """Convenience: does an active lock cover ``check_name`` (or any if None)?"""
    lock = load_lock(path)
    if lock is None:
        return False
    return True if check_name is None else lock.covers(check_name)


def _save_lock(lock: MaintenanceLock, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(lock), sort_keys=True), encoding="utf-8")
    tmp.replace(path)


# ----- public API ---------------------------------------------------------

def start_maintenance(
    *,
    duration_hours: float,
    reason: str,
    scope: list[str] | None = None,
    started_by: str | None = None,
    path: Path | str = DEFAULT_LOCK_PATH,
    now: float | None = None,
) -> MaintenanceLock:
    if duration_hours <= 0 or duration_hours > 168:  # 1 week hard cap
        raise ValueError(f"duration_hours must be in (0, 168]; got {duration_hours}")
    if not reason or not reason.strip():
        raise ValueError("reason is required")
    now = now if now is not None else time.time()
    actor = started_by or f"{getpass.getuser()}@{socket.gethostname()}"
    lock = MaintenanceLock(
        started_at=now,
        until=now + duration_hours * 3600,
        reason=reason.strip(),
        started_by=actor,
        scope=list(scope or []),
    )
    _save_lock(lock, Path(path))
    return lock


def end_maintenance(*, path: Path | str = DEFAULT_LOCK_PATH) -> MaintenanceLock | None:
    """Remove the lock file; return the lock that was active (or None)."""
    p = Path(path)
    if not p.exists():
        return None
    lock = load_lock(p)  # may be None if expired
    try:
        p.unlink()
    except OSError:
        pass
    return lock
