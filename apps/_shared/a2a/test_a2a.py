"""Tests for the agent-to-agent message bus."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent
from unittest import mock

import pytest
import yaml

from apps._shared.a2a import (
    A2ANotAllowedError,
    ask_agent,
    await_reply,
    enqueue,
    ensure_dirs,
    make_tier2_executive_handler,
    reply_to_caller,
)
from apps._shared.a2a.envelope import A2AEnvelope
from apps._shared.a2a.routing import assert_callee_allowed, resolve_queue_dir
from apps._shared.registry import Registry, load_registry
from apps._shared.registry.loader import AgentManifest


def _scaffold_repo(root: Path) -> None:
    (root / "config" / "memory").mkdir(parents=True)
    principals = dedent(
        """
        principals:
          - id: agent:caller
            kind: agent
          - id: agent:callee
            kind: agent
          - id: agent:executive
            kind: agent
        """
    ).strip() + "\n"
    (root / "config" / "memory" / "principals.yaml").write_text(principals)
    (root / "config" / "agents").mkdir(parents=True)


def _write_manifest(
    root: Path,
    principal: str,
    *,
    queue_dir: str,
    callees: list[str] | None = None,
) -> str:
    body: dict = {
        "principal": principal,
        "display_name": principal,
        "domain": "test",
        "queue_dir": queue_dir,
        "references": {"memory_principal": "config/memory/principals.yaml"},
    }
    if callees is not None:
        body["a2a"] = {"allowed_callees": callees}
    name = principal.replace("agent:", "agent-")
    rel = f"config/agents/{name}.yaml"
    (root / rel).write_text(yaml.safe_dump(body, sort_keys=False))
    return rel


def _registry(root: Path, entries: list[tuple[str, str]]) -> Registry:
    index = root / "config/agents/registry.yaml"
    index.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "agents": [{"principal": p, "manifest": m} for p, m in entries],
            },
            sort_keys=False,
        )
    )
    return load_registry(index, repo_root=root)


def test_envelope_round_trip() -> None:
    env = A2AEnvelope.new_request(
        caller="agent:caller",
        callee="agent:callee",
        action="ping",
        payload={"x": 1},
        reply_to="/tmp/inbox",
    )
    restored = A2AEnvelope.from_dict(env.to_dict())
    assert restored.correlation_id == env.correlation_id
    assert restored.action == "ping"
    assert restored.payload == {"x": 1}


def test_ensure_dirs_and_enqueue(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    dirs = ensure_dirs(q, worktrees=True)
    assert dirs["inbox"].is_dir()
    assert dirs["worktrees"].is_dir()
    path = enqueue(q, "job.json", {"action": "test"})
    assert path.exists()
    assert json.loads(path.read_text())["action"] == "test"


def test_acl_denies_unknown_callee(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    caller_m = _write_manifest(
        tmp_path,
        "agent:caller",
        queue_dir=str(tmp_path / "caller"),
        callees=[],
    )
    callee_m = _write_manifest(
        tmp_path,
        "agent:callee",
        queue_dir=str(tmp_path / "callee"),
    )
    reg = _registry(tmp_path, [("agent:caller", caller_m), ("agent:callee", callee_m)])

    with pytest.raises(A2ANotAllowedError):
        assert_callee_allowed("agent:caller", "agent:callee", registry=reg)


def test_acl_allows_listed_callee(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    caller_m = _write_manifest(
        tmp_path,
        "agent:caller",
        queue_dir=str(tmp_path / "caller"),
        callees=["agent:callee"],
    )
    callee_m = _write_manifest(
        tmp_path,
        "agent:callee",
        queue_dir=str(tmp_path / "callee"),
    )
    reg = _registry(tmp_path, [("agent:caller", caller_m), ("agent:callee", callee_m)])
    assert_callee_allowed("agent:caller", "agent:callee", registry=reg)


def test_ask_and_reply_round_trip(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    caller_dir = tmp_path / "caller"
    callee_dir = tmp_path / "callee"
    caller_m = _write_manifest(
        tmp_path,
        "agent:caller",
        queue_dir=str(caller_dir),
        callees=["agent:callee"],
    )
    callee_m = _write_manifest(
        tmp_path,
        "agent:callee",
        queue_dir=str(callee_dir),
    )
    reg = _registry(tmp_path, [("agent:caller", caller_m), ("agent:callee", callee_m)])

    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg):
        result = ask_agent(
            "agent:caller",
            "agent:callee",
            "help_request",
            {"task_class": "test.task"},
            timeout_seconds=30,
        )

    job_files = list((callee_dir / "inbox").glob("*.json"))
    assert len(job_files) == 1
    job = json.loads(job_files[0].read_text(encoding="utf-8"))
    assert job["correlation_id"] == result.correlation_id
    assert job["action"] == "help_request"
    assert job["reply_to"] == str((caller_dir / "inbox").resolve())

    request = A2AEnvelope.from_dict(job)
    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg):
        reply_to_caller(request, success=True, outcome="ok", payload={"answer": 42})

    reply = await_reply(
        result.correlation_id,
        caller_dir / "inbox",
        timeout_seconds=1.0,
        poll_interval=0.05,
    )
    assert reply is not None
    assert reply.success is True
    assert reply.reply_payload == {"answer": 42}


def test_tier2_handler_success_when_executive_replies(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    principals = tmp_path / "config/memory/principals.yaml"
    principals.write_text(
        principals.read_text()
        + "  - id: agent:homelab-maintainer\n    kind: agent\n"
        + "  - id: agent:executive\n    kind: agent\n"
    )
    maint_dir = tmp_path / "maintainer"
    exec_dir = tmp_path / "executive"
    maint_m = _write_manifest(
        tmp_path,
        "agent:homelab-maintainer",
        queue_dir=str(maint_dir),
        callees=["agent:executive"],
    )
    exec_m = _write_manifest(
        tmp_path,
        "agent:executive",
        queue_dir=str(exec_dir),
    )
    reg = _registry(
        tmp_path,
        [
            ("agent:homelab-maintainer", maint_m),
            ("agent:executive", exec_m),
        ],
    )

    handler = make_tier2_executive_handler(
        caller="agent:homelab-maintainer",
        reply_queue_dir=maint_dir,
        ask_timeout_seconds=2.0,
        poll_interval=0.05,
    )

    def fake_ask(*args, **kwargs):
        result = ask_agent(*args, **kwargs)
        # Simulate executive replying immediately.
        job_path = next((exec_dir / "inbox").glob("a2a-*.json"))
        request = A2AEnvelope.from_path(job_path)
        reply_to_caller(
            request,
            success=True,
            outcome="rerouted",
            payload={"handled": True},
        )
        return result

    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg), mock.patch(
        "apps._shared.a2a.ask_agent", side_effect=fake_ask
    ):
        ok, payload, reason = handler({"task_class": "homelab.deploy", "urgent": False})

    assert ok is True
    assert payload == {"handled": True}
    assert "rerouted" in reason


def test_resolve_queue_dir_from_manifest(tmp_path: Path) -> None:
    manifest = AgentManifest(
        "agent:foo",
        tmp_path / "m.yaml",
        {"queue_dir": str(tmp_path / "q")},
    )
    reg = Registry(schema_version=1, agents={"agent:foo": manifest})
    assert resolve_queue_dir("agent:foo", registry=reg) == (tmp_path / "q").resolve()
