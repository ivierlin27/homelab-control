"""Tests for the sub-agent spawner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps._shared.rlm.subcall import SubCallInvoker
from apps._shared.subagent import (
    RoutePolicy,
    SubagentRoleError,
    SubagentRouteError,
    spawn_subagent,
)
from apps._shared.subagent.personas import get_persona


def _transport(payload_dict: dict) -> SubCallInvoker:
    def transport(intent: str, model: str, payload: dict) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": f"done as {intent}",
                                "citations": [],
                                "confidence": "high",
                                "open_questions": [],
                            }
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }

    return SubCallInvoker(transport=transport)


def test_spawn_subagent_returns_distilled_result(tmp_path: Path) -> None:
    audit = tmp_path / "trust-ledger.jsonl"
    inv = _transport({})
    result = spawn_subagent(
        "tool-runner",
        "Summarize the grep output for ERROR lines.",
        ["grep", "read_file"],
        parent_correlation_id="parent-corr-1",
        audit_path=audit,
        route="local-fast",
        context={"handles": ["log-1"]},
        invoker=inv,
    )
    assert result.summary.startswith("done as")
    assert result.parent_correlation_id == "parent-corr-1"
    assert result.route == "local-fast"
    assert result.model == "homelab-fast"
    lines = audit.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    spawn_row = json.loads(lines[0])
    complete_row = json.loads(lines[1])
    assert spawn_row["event"] == "subagent_spawn"
    assert complete_row["event"] == "subagent_complete"
    assert "transcript" in complete_row
    assert complete_row["parent_correlation_id"] == "parent-corr-1"


def test_spawn_subagent_rejects_unknown_role(tmp_path: Path) -> None:
    with pytest.raises(SubagentRoleError):
        spawn_subagent(
            "executor",
            "nope",
            parent_correlation_id="p1",
            audit_path=tmp_path / "audit.jsonl",
            invoker=_transport({}),
        )


def test_spawn_subagent_enforces_route_policy(tmp_path: Path) -> None:
    policy = RoutePolicy(allowed_routes=frozenset({"local-fast"}), allow_cloud=False)
    with pytest.raises(SubagentRouteError):
        spawn_subagent(
            "planner",
            "plan deploy",
            parent_correlation_id="p1",
            audit_path=tmp_path / "audit.jsonl",
            route="local-strong",
            route_policy=policy,
            invoker=_transport({}),
        )


def test_planner_default_route_is_local_strong() -> None:
    assert get_persona("planner").default_route == "local-strong"


def test_route_policy_from_manifest() -> None:
    pol = RoutePolicy.from_manifest_routing(
        {"subagent_allowed_routes": ["local-fast", "local-strong"], "allow_cloud": False}
    )
    assert "local-strong" in pol.allowed_routes
