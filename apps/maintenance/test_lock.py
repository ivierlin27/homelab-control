"""Tests for the maintenance-mode lock + CLI surface."""

from __future__ import annotations

import json
import time

import pytest

from .lock import (
    MaintenanceLock,
    end_maintenance,
    is_active,
    load_lock,
    start_maintenance,
)


def test_start_writes_a_readable_lock(tmp_path):
    lock_path = tmp_path / "maint.lock"
    lock = start_maintenance(
        duration_hours=2.0, reason="proxmox kernel update", path=lock_path,
    )
    assert lock.is_active()
    assert lock.until > lock.started_at
    on_disk = json.loads(lock_path.read_text())
    assert on_disk["reason"] == "proxmox kernel update"
    # round-trip
    loaded = load_lock(lock_path)
    assert loaded is not None and loaded.reason == "proxmox kernel update"


def test_expired_lock_is_not_active(tmp_path):
    lock_path = tmp_path / "maint.lock"
    now = time.time() - 7200  # 2h ago
    lock = MaintenanceLock(started_at=now, until=now + 3600, reason="r", started_by="t")
    lock_path.write_text(json.dumps({**lock.__dict__}))
    assert load_lock(lock_path) is None
    assert is_active(path=lock_path) is False


def test_end_removes_lock_file(tmp_path):
    lock_path = tmp_path / "maint.lock"
    start_maintenance(duration_hours=1, reason="r", path=lock_path)
    assert lock_path.exists()
    ended = end_maintenance(path=lock_path)
    assert ended is not None
    assert not lock_path.exists()
    assert end_maintenance(path=lock_path) is None  # idempotent


def test_scope_covers_only_matching_prefixes():
    lock = MaintenanceLock(
        started_at=time.time(), until=time.time() + 3600,
        reason="r", started_by="t",
        scope=["health:memory-engine", "timer:alienware-backup"],
    )
    assert lock.covers("health:memory-engine:khoj")
    assert lock.covers("timer:alienware-backup-hot.timer")
    assert not lock.covers("service:alienware-master-dashboard.service")
    assert not lock.covers("health:forgejo:http")


def test_empty_scope_is_global():
    lock = MaintenanceLock(
        started_at=time.time(), until=time.time() + 3600,
        reason="r", started_by="t", scope=[],
    )
    assert lock.covers("anything")
    assert lock.covers("health:foo")


def test_start_rejects_invalid_duration(tmp_path):
    p = tmp_path / "l"
    with pytest.raises(ValueError):
        start_maintenance(duration_hours=0, reason="r", path=p)
    with pytest.raises(ValueError):
        start_maintenance(duration_hours=200, reason="r", path=p)  # > 168 cap


def test_start_rejects_empty_reason(tmp_path):
    p = tmp_path / "l"
    with pytest.raises(ValueError):
        start_maintenance(duration_hours=1, reason="   ", path=p)


def test_load_lock_handles_corrupt_file(tmp_path):
    lock_path = tmp_path / "maint.lock"
    lock_path.write_text("not json at all { ")
    assert load_lock(lock_path) is None


def test_load_lock_returns_none_for_missing_fields(tmp_path):
    lock_path = tmp_path / "maint.lock"
    lock_path.write_text(json.dumps({"reason": "r"}))  # missing started_at/until
    assert load_lock(lock_path) is None
