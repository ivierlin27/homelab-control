"""Unit tests for the LiteLLM cost JSONL callback.

These run without the ``litellm`` package — only the pure-function
``build_record`` and append helpers are exercised.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from .custom_callbacks import (
    SCHEMA_VERSION,
    _append,
    _epoch,
    build_record,
)


def _kwargs(
    *,
    model: str = "homelab-strong",
    principal: str | None = "agent:executive",
    request_id: str | None = "call-123",
    response_cost: float | None = 0.0,
) -> dict:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["x-agent-principal"] = principal
    if request_id is not None:
        headers["x-litellm-call-id"] = request_id
    return {
        "model": model,
        "user": "kevin",
        "response_cost": response_cost,
        "litellm_params": {
            "proxy_server_request": {"headers": headers},
        },
    }


def _response(prompt: int = 12, completion: int = 34) -> dict:
    return {
        "id": "resp-abc",
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


def test_build_record_happy_path():
    start = datetime(2026, 5, 17, 17, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 5, 17, 17, 0, 1, 250_000, tzinfo=timezone.utc)
    record = build_record(_kwargs(), _response(), start, end, status="success")
    assert record["schema"] == SCHEMA_VERSION
    assert record["status"] == "success"
    assert record["model"] == "homelab-strong"
    assert record["agent_principal"] == "agent:executive"
    assert record["request_id"] == "call-123"
    assert record["prompt_tokens"] == 12
    assert record["completion_tokens"] == 34
    assert record["total_tokens"] == 46
    assert record["cost_usd"] == 0.0
    assert record["latency_ms"] == 1250
    assert record["ts"] == pytest.approx(end.timestamp())
    assert "error" not in record


def test_build_record_defaults_principal_when_header_missing():
    record = build_record(_kwargs(principal=None), _response(), 1700000000.0, 1700000000.5, status="success")
    assert record["agent_principal"] == "unknown"
    assert record["latency_ms"] == 500


def test_build_record_falls_back_to_response_id_for_request_id():
    record = build_record(_kwargs(request_id=None), _response(), None, None, status="success")
    assert record["request_id"] == "resp-abc"
    assert record["latency_ms"] is None


def test_build_record_records_failure_with_error_truncated():
    long_err = "boom " * 500
    record = build_record(
        _kwargs(), None, 1700000000.0, 1700000001.0,
        status="failure", error=long_err,
    )
    assert record["status"] == "failure"
    assert record["prompt_tokens"] is None
    assert record["completion_tokens"] is None
    assert len(record["error"]) <= 512


def test_build_record_handles_object_response_with_usage_attr():
    class Usage:
        prompt_tokens = 5
        completion_tokens = 7
        total_tokens = 12

    class Resp:
        id = "obj-resp"
        usage = Usage()

    record = build_record(_kwargs(request_id=None), Resp(), 1700000000.0, 1700000000.1, status="success")
    assert record["prompt_tokens"] == 5
    assert record["completion_tokens"] == 7
    assert record["request_id"] == "obj-resp"


def test_epoch_accepts_numeric_and_datetime():
    assert _epoch(None) is None
    assert _epoch(123.4) == 123.4
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert _epoch(dt) == dt.timestamp()


def test_append_writes_jsonl_under_env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "subdir" / "calls.jsonl"
    monkeypatch.setenv("LLM_CALLS_JSONL", str(target))
    record = {"schema": SCHEMA_VERSION, "model": "x", "status": "success"}
    _append(record)
    _append({**record, "ts": 1.0})
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["model"] == "x"
    assert json.loads(lines[1])["ts"] == 1.0


def test_append_never_raises_on_unwritable_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_CALLS_JSONL", "/dev/full/cannot-write/calls.jsonl")
    _append({"schema": SCHEMA_VERSION})
