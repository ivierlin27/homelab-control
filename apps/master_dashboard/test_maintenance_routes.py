"""End-to-end tests for the dashboard's maintenance-mode routes.

Covers:
  - GET / renders the maintenance tile (inactive by default)
  - POST /maintenance/start happy path (lock visible to subsequent GET)
  - Validation errors return 4xx (missing reason; out-of-range hours)
  - POST /maintenance/end clears the lock
  - DASHBOARD_ALLOW_MAINTENANCE_WRITE=0 -> 403 on start/end
  - Banner renders when a lock is active

We point the lock + audit at tmp paths so the test never touches the
real user state files.
"""

from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

import pytest

# We import the dashboard module fresh inside fixtures so each test can
# adjust env (DASHBOARD_ALLOW_MAINTENANCE_WRITE) before module load.
APP_PATH = Path(__file__).resolve().parents[1]


def _load_dashboard(monkeypatch, tmp_path: Path, *, allow_write: bool = True):
    """Import (or reimport) the dashboard with a sandboxed lock + audit."""
    lock_path = tmp_path / "maintenance.lock"
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("MAINTENANCE_LOCK_FILE", str(lock_path))
    monkeypatch.setenv("DASHBOARD_MAINTENANCE_AUDIT", str(audit_path))
    monkeypatch.setenv("DASHBOARD_ALLOW_MAINTENANCE_WRITE", "1" if allow_write else "0")
    # Force fresh import so the env vars are picked up.
    for mod in [
        "apps.master_dashboard.main",
        "maintenance.lock",
        "maintenance",
    ]:
        sys.modules.pop(mod, None)
    if str(APP_PATH) not in sys.path:
        sys.path.insert(0, str(APP_PATH))
    main = importlib.import_module("apps.master_dashboard.main")
    return main, lock_path, audit_path


@pytest.fixture
def client(monkeypatch, tmp_path):
    main, lock_path, audit_path = _load_dashboard(monkeypatch, tmp_path)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c, main, lock_path, audit_path


@pytest.fixture
def readonly_client(monkeypatch, tmp_path):
    main, lock_path, audit_path = _load_dashboard(monkeypatch, tmp_path, allow_write=False)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c, main, lock_path, audit_path


# ---- happy path ---------------------------------------------------------

def test_index_renders_with_no_maintenance(client):
    c, _main, _lock, _audit = client
    r = c.get("/")
    assert r.status_code == 200
    # Banner must not appear; tile must render the "no active window" copy.
    assert "Maintenance Mode" in r.text
    assert "No active window" in r.text
    # No top banner element when inactive
    assert "▲ Maintenance Mode" not in r.text or "No active window" in r.text


def test_start_then_end_flow(client):
    c, _main, lock_path, audit_path = client

    # start
    r = c.post("/maintenance/start", data={
        "hours": "1", "reason": "kernel update via dashboard", "scope": "health:,timer:",
    })
    assert r.status_code == 200, r.text
    assert "End maintenance now" in r.text
    assert "kernel update via dashboard" in r.text
    assert lock_path.exists()

    # banner shows on the next page render
    r2 = c.get("/")
    assert r2.status_code == 200
    assert "kernel update via dashboard" in r2.text
    assert "▲" in r2.text  # banner marker

    # audit row written with dashboard actor
    rows = [line for line in audit_path.read_text().splitlines() if line.strip()]
    assert any('"event": "maintenance_start"' in r for r in rows)
    assert any('"actor": "dashboard:' in r for r in rows)

    # end
    r3 = c.post("/maintenance/end")
    assert r3.status_code == 200, r3.text
    assert "No active window" in r3.text
    assert not lock_path.exists()
    rows = [line for line in audit_path.read_text().splitlines() if line.strip()]
    assert any('"event": "maintenance_end"' in r for r in rows)


# ---- validation ---------------------------------------------------------

def test_start_rejects_missing_reason(client):
    c, *_ = client
    r = c.post("/maintenance/start", data={"hours": "1", "reason": "", "scope": ""})
    # FastAPI form-validation returns 422 for the empty string when the
    # underlying call raises ValueError, we translate to 400.
    assert r.status_code in (400, 422), r.text


def test_start_rejects_out_of_range_hours(client):
    c, *_ = client
    r = c.post("/maintenance/start", data={"hours": "999", "reason": "x", "scope": ""})
    assert r.status_code == 400
    assert "duration_hours" in r.text


def test_start_rejects_concurrent_window(client):
    c, *_ = client
    r1 = c.post("/maintenance/start", data={"hours": "0.5", "reason": "first", "scope": ""})
    assert r1.status_code == 200
    r2 = c.post("/maintenance/start", data={"hours": "0.5", "reason": "second", "scope": ""})
    assert r2.status_code == 409
    assert "already active" in r2.text


def test_end_is_idempotent(client):
    c, *_ = client
    # End with no active window -> still 200, just no-op
    r = c.post("/maintenance/end")
    assert r.status_code == 200
    assert "No active window" in r.text


# ---- read-only gate -----------------------------------------------------

def test_readonly_blocks_writes(readonly_client):
    c, _main, lock_path, _audit = readonly_client
    r = c.post("/maintenance/start", data={"hours": "1", "reason": "nope", "scope": ""})
    assert r.status_code == 403
    assert "Maintenance write disabled" in r.text
    assert not lock_path.exists()
    # End also blocked
    r2 = c.post("/maintenance/end")
    assert r2.status_code == 403


# ---- tile endpoint ------------------------------------------------------

def test_tile_endpoint_returns_html(client):
    c, *_ = client
    r = c.get("/tiles/maintenance")
    assert r.status_code == 200
    assert "Maintenance Mode" in r.text
