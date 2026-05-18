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
    task_intent: str | None = None,
) -> dict:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["x-agent-principal"] = principal
    if request_id is not None:
        headers["x-litellm-call-id"] = request_id
    if task_intent is not None:
        headers["x-task-intent"] = task_intent
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
    assert record["task_intent"] is None
    assert "error" not in record


def test_build_record_carries_task_intent_when_header_present():
    record = build_record(
        _kwargs(task_intent="summarize"), _response(), 1700000000.0, 1700000000.5, status="success"
    )
    assert record["task_intent"] == "summarize"


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


# ---------------------------------------------------------------------------
# Phase 1 P1: local-only enforcement (pre-call hook)
# ---------------------------------------------------------------------------
#
# The hook intercepts every proxy call before dispatch, reads ``x-skill``
# from the incoming headers, looks up the skill in a JSON snapshot of
# config/skills/, and rejects with HTTP 403 if the skill is local_only=true
# AND the requested model is not on a local route. A rejection JSONL row is
# appended to the cost log so the cost relay surfaces it.
#
# We test the hook synchronously by driving the awaitable in a fresh event
# loop, so the test file remains pytest-stdlib (no pytest-asyncio).


import asyncio

from .custom_callbacks import (
    CostJsonlHandler,
    HTTPException,
    _reset_policy_snapshot_for_tests,
)


def _snapshot_payload(skills: dict) -> dict:
    return {"schema": 1, "skills": skills}


def _pre_call_data(*, model: str, headers: dict[str, str]) -> dict:
    """Build the dict-ish payload LiteLLM passes to async_pre_call_hook."""
    return {
        "model": model,
        "proxy_server_request": {"headers": headers},
    }


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_pre_call_hook_allows_when_no_x_skill_header(tmp_path, monkeypatch):
    snap_file = tmp_path / "policy.json"
    snap_file.write_text(
        json.dumps(_snapshot_payload({"finance-categorize": {"local_only": True}})),
        encoding="utf-8",
    )
    monkeypatch.setenv("SKILL_POLICY_SNAPSHOT", str(snap_file))
    monkeypatch.setenv("LLM_CALLS_JSONL", str(tmp_path / "calls.jsonl"))
    _reset_policy_snapshot_for_tests()

    handler = CostJsonlHandler()
    data = _pre_call_data(model="openai/gpt-4o", headers={})
    result = _run(handler.async_pre_call_hook(data=data))
    assert result is data  # passes through


def test_pre_call_hook_allows_local_only_skill_with_local_model(tmp_path, monkeypatch):
    snap_file = tmp_path / "policy.json"
    snap_file.write_text(
        json.dumps(_snapshot_payload({"intake-classify": {"local_only": True}})),
        encoding="utf-8",
    )
    monkeypatch.setenv("SKILL_POLICY_SNAPSHOT", str(snap_file))
    monkeypatch.setenv("LLM_CALLS_JSONL", str(tmp_path / "calls.jsonl"))
    _reset_policy_snapshot_for_tests()

    handler = CostJsonlHandler()
    data = _pre_call_data(
        model="homelab-strong",
        headers={"x-skill": "intake-classify"},
    )
    result = _run(handler.async_pre_call_hook(data=data))
    assert result is data


def test_pre_call_hook_rejects_local_only_skill_with_cloud_model(tmp_path, monkeypatch):
    snap_file = tmp_path / "policy.json"
    snap_file.write_text(
        json.dumps(_snapshot_payload({"intake-classify": {"local_only": True}})),
        encoding="utf-8",
    )
    calls_log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("SKILL_POLICY_SNAPSHOT", str(snap_file))
    monkeypatch.setenv("LLM_CALLS_JSONL", str(calls_log))
    _reset_policy_snapshot_for_tests()

    handler = CostJsonlHandler()
    data = _pre_call_data(
        model="openai/gpt-4o-mini",
        headers={
            "x-skill": "intake-classify",
            "x-agent-principal": "agent:finance",
            "x-litellm-call-id": "test-call-1",
        },
    )
    with pytest.raises(HTTPException) as exc_info:
        _run(handler.async_pre_call_hook(data=data))
    assert exc_info.value.status_code == 403
    assert "intake-classify" in exc_info.value.detail
    assert "openai/gpt-4o-mini" in exc_info.value.detail

    # An audit row was appended with status="rejected_local_only".
    line = calls_log.read_text(encoding="utf-8").splitlines()[-1]
    row = json.loads(line)
    assert row["status"] == "rejected_local_only"
    assert row["rejected_skill"] == "intake-classify"
    assert row["model"] == "openai/gpt-4o-mini"
    assert row["agent_principal"] == "agent:finance"
    assert row["request_id"] == "test-call-1"


def test_pre_call_hook_allows_non_local_only_skill_with_cloud_model(tmp_path, monkeypatch):
    snap_file = tmp_path / "policy.json"
    snap_file.write_text(
        json.dumps(_snapshot_payload({"execute-task": {"local_only": False}})),
        encoding="utf-8",
    )
    monkeypatch.setenv("SKILL_POLICY_SNAPSHOT", str(snap_file))
    monkeypatch.setenv("LLM_CALLS_JSONL", str(tmp_path / "calls.jsonl"))
    _reset_policy_snapshot_for_tests()

    handler = CostJsonlHandler()
    data = _pre_call_data(
        model="openai/gpt-4o",
        headers={"x-skill": "execute-task"},
    )
    result = _run(handler.async_pre_call_hook(data=data))
    assert result is data


def test_pre_call_hook_unknown_skill_id_fails_open(tmp_path, monkeypatch):
    """An x-skill we don't recognize is allowed (companion mitigation: the
    skills_for_agent boot-time gate already restricts which skill_ids can
    legitimately reach here)."""
    snap_file = tmp_path / "policy.json"
    snap_file.write_text(json.dumps(_snapshot_payload({})), encoding="utf-8")
    monkeypatch.setenv("SKILL_POLICY_SNAPSHOT", str(snap_file))
    monkeypatch.setenv("LLM_CALLS_JSONL", str(tmp_path / "calls.jsonl"))
    _reset_policy_snapshot_for_tests()

    handler = CostJsonlHandler()
    data = _pre_call_data(
        model="openai/gpt-4o",
        headers={"x-skill": "skill-that-does-not-exist"},
    )
    result = _run(handler.async_pre_call_hook(data=data))
    assert result is data


def test_pre_call_hook_missing_snapshot_fails_open(tmp_path, monkeypatch):
    """No snapshot file => empty policy => every call allowed."""
    monkeypatch.setenv("SKILL_POLICY_SNAPSHOT", str(tmp_path / "does-not-exist.json"))
    monkeypatch.setenv("LLM_CALLS_JSONL", str(tmp_path / "calls.jsonl"))
    _reset_policy_snapshot_for_tests()

    handler = CostJsonlHandler()
    data = _pre_call_data(
        model="openai/gpt-4o",
        headers={"x-skill": "intake-classify"},
    )
    result = _run(handler.async_pre_call_hook(data=data))
    assert result is data


def test_pre_call_hook_respects_local_prefix_env(tmp_path, monkeypatch):
    """A custom LITELLM_LOCAL_MODEL_PREFIXES makes an otherwise-cloud-looking
    model name local."""
    snap_file = tmp_path / "policy.json"
    snap_file.write_text(
        json.dumps(_snapshot_payload({"intake-classify": {"local_only": True}})),
        encoding="utf-8",
    )
    monkeypatch.setenv("SKILL_POLICY_SNAPSHOT", str(snap_file))
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_PREFIXES", "secretcorp-")
    monkeypatch.setenv("LLM_CALLS_JSONL", str(tmp_path / "calls.jsonl"))
    _reset_policy_snapshot_for_tests()

    handler = CostJsonlHandler()
    data = _pre_call_data(
        model="secretcorp-strong",
        headers={"x-skill": "intake-classify"},
    )
    result = _run(handler.async_pre_call_hook(data=data))
    assert result is data
