"""Smoke tests for the master dashboard.

Exercises the pure-Python helpers without needing PG / restic / systemctl
to be alive. Endpoints are exercised via FastAPI's TestClient with the
external fetchers stubbed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# fastapi is optional in some envs; skip cleanly when missing
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from apps.master_dashboard import main as dash


def test_read_tail_returns_recent_jsonl_records(tmp_path: Path):
    ledger = tmp_path / "trust-ledger.jsonl"
    lines = [
        json.dumps({"ts": f"2026-05-17T17:00:{i:02d}Z", "event": "probe", "n": i})
        for i in range(60)
    ]
    ledger.write_text("\n".join(lines) + "\n")
    out = dash._read_tail(ledger, max_lines=10)
    assert len(out) == 10
    assert out[-1]["n"] == 59
    assert all(rec["_source"] == ledger.parent.name for rec in out)


def test_read_tail_handles_missing_file(tmp_path: Path):
    assert dash._read_tail(tmp_path / "nope.jsonl") == []


def test_read_recent_events_sorts_across_ledgers(tmp_path: Path, monkeypatch):
    a = tmp_path / "agent-a" / "trust-ledger.jsonl"
    b = tmp_path / "agent-b" / "trust-ledger.jsonl"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text(json.dumps({"ts": "2026-05-17T17:00:00Z", "event": "older"}) + "\n")
    b.write_text(json.dumps({"ts": "2026-05-17T17:05:00Z", "event": "newer"}) + "\n")
    monkeypatch.setattr(dash, "AUDIT_ROOT", tmp_path)
    events = dash._read_recent_events()
    assert events[0]["event"] == "newer"
    assert events[1]["event"] == "older"


@pytest.mark.asyncio
async def test_ttlcache_caches_and_recovers(monkeypatch):
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"v": 1}
        raise RuntimeError("boom")

    cache = dash.TTLCache(ttl_seconds=100, fetcher=fetch)
    first = await cache.get()
    assert first.value == {"v": 1} and first.error is None
    # Within TTL, no re-fetch
    again = await cache.get()
    assert again.value == {"v": 1}
    assert calls["n"] == 1
    # Force expiry and verify graceful degradation on error
    cache.ttl = 0
    degraded = await cache.get()
    assert degraded.error == "boom"
    assert degraded.value == {"v": 1}  # stale value preserved


def test_index_renders_with_stubbed_fetchers(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(dash, "AUDIT_ROOT", tmp_path)

    async def _ok_cost():
        return {
            "summary": {"calls": 7, "total_tokens": 1234, "p95_ms": 250},
            "by_agent_intent": [
                {"agent_principal": "agent:test", "task_intent": "classify",
                 "calls": 3, "total_tokens": 200, "avg_latency_ms": 80},
            ],
        }

    async def _ok_backup():
        return [{"repo": "/tmp/r", "snapshots": [
            {"hostname": "h1", "tags": ["hot"], "time": "2026-05-17T17:00:00Z"}
        ], "error": None}]

    async def _ok_presence():
        return [{"label": "Test", "rows": [
            {"unit": "x.service", "active": "active", "sub": "running",
             "result": "success", "since": "Sun 2026-05-17 17:00:00 PDT", "description": "x"},
        ]}]

    async def _ok_schedule():
        return [
            {"host": "alienware", "unit": "alienware-backup-hot.timer",
             "activates": "alienware-backup-hot.service",
             "next_epoch": 9e9, "last_epoch": 9e9 - 600,
             "next_in_seconds": 600, "last_ago_seconds": 60},
        ]

    monkeypatch.setattr(dash, "_cost_cache", dash.TTLCache(60, _ok_cost))
    monkeypatch.setattr(dash, "_backup_cache", dash.TTLCache(60, _ok_backup))
    monkeypatch.setattr(dash, "_presence_cache", dash.TTLCache(60, _ok_presence))
    monkeypatch.setattr(dash, "_schedule_cache", dash.TTLCache(60, _ok_schedule))

    with TestClient(dash.app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.text
        assert "homelab control" in body
        assert "agent:test" in body
        assert "classify" in body
        assert "x.service" in body
        assert "hot" in body

        assert "alienware-backup-hot.timer" in body

        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/tiles/cost").status_code == 200
        assert client.get("/tiles/presence").status_code == 200
        assert client.get("/tiles/backup").status_code == 200
        assert client.get("/tiles/audit").status_code == 200
        assert client.get("/tiles/schedule").status_code == 200


def test_parse_timer_json_handles_real_systemd_output():
    blob = (
        '[{"next":1779065778436277,"left":1779065778436277,'
        '"last":1779065478434873,"passed":97233505326,'
        '"unit":"alienware-backup-hot.timer",'
        '"activates":"alienware-backup-hot.service"},'
        '{"next":0,"left":0,"last":0,"passed":0,'
        '"unit":"unscheduled.timer","activates":"unscheduled.service"}]'
    )
    rows = dash._parse_timer_json(blob, host="alienware")
    assert len(rows) == 2
    assert rows[0]["host"] == "alienware"
    assert rows[0]["unit"] == "alienware-backup-hot.timer"
    assert rows[0]["next_epoch"] == pytest.approx(1779065778.436277)
    assert rows[0]["last_epoch"] == pytest.approx(1779065478.434873)
    assert rows[1]["next_epoch"] is None
    assert rows[1]["last_epoch"] is None


def test_parse_timer_json_returns_empty_on_garbage():
    assert dash._parse_timer_json("not json", host="x") == []
