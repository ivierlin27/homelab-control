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
    A2ARoutingError,
    ask_agent,
    await_reply,
    enqueue,
    enqueue_inbox,
    enqueue_envelope,
    ensure_dirs,
    make_tier2_executive_handler,
    poll_reply,
    reply_to_caller,
)
from apps._shared.a2a.envelope import A2AEnvelope
from apps._shared.a2a.routing import assert_callee_allowed, resolve_queue_dir
from apps._shared.audit import AuditLog
from apps._shared.escalation import AttemptOutcome, Dispatcher
from apps._shared.escalation.policy import TierBudgets
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


def test_resolve_queue_dir_missing_raises(tmp_path: Path) -> None:
    manifest = AgentManifest(
        "agent:foo",
        tmp_path / "m.yaml",
        {"principal": "agent:foo", "display_name": "x", "domain": "t", "queue_dir": ""},
    )
    reg = Registry(schema_version=1, agents={"agent:foo": manifest})
    with pytest.raises(A2ARoutingError, match="no queue_dir"):
        resolve_queue_dir("agent:foo", registry=reg)


def test_assert_callee_allowed_rejects_self_call(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    m = _write_manifest(tmp_path, "agent:caller", queue_dir=str(tmp_path / "q"), callees=[])
    reg = _registry(tmp_path, [("agent:caller", m)])
    with pytest.raises(A2ANotAllowedError, match="may not call itself"):
        assert_callee_allowed("agent:caller", "agent:caller", registry=reg)


def test_enqueue_envelope_uses_correlation_filename(tmp_path: Path) -> None:
    env = A2AEnvelope.new_request(
        caller="agent:a",
        callee="agent:b",
        action="ping",
        payload={},
        reply_to="/tmp/inbox",
        correlation_id="corr-123",
    )
    path = enqueue_envelope(tmp_path / "q", env)
    assert path.name == "a2a-corr-123.json"
    assert json.loads(path.read_text())["correlation_id"] == "corr-123"


def test_envelope_new_reply_and_from_path(tmp_path: Path) -> None:
    req = A2AEnvelope.new_request(
        caller="agent:a",
        callee="agent:b",
        action="ping",
        payload={"n": 1},
        reply_to="/tmp/inbox",
        correlation_id="cid-1",
    )
    reply = A2AEnvelope.new_reply(req, success=False, outcome="nope", reply_payload={"detail": "busy"})
    assert reply.is_reply is True
    assert reply.caller == "agent:b"
    assert reply.callee == "agent:a"
    assert reply.success is False

    p = tmp_path / "saved.json"
    req.write(p)
    assert A2AEnvelope.from_path(p).correlation_id == "cid-1"


def test_poll_reply_timeout(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    assert poll_reply("missing-id", inbox, timeout_seconds=0.2, poll_interval=0.05) is None


def test_poll_reply_skips_malformed_and_non_matching(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "bad.json").write_text("not json", encoding="utf-8")
    (inbox / "request.json").write_text(
        json.dumps(
            {
                "id": "1",
                "correlation_id": "other",
                "caller": "a",
                "callee": "b",
                "action": "x",
                "payload": {},
                "reply_to": "",
                "created_at": "2026-01-01T00:00:00+00:00",
                "is_reply": False,
            }
        ),
        encoding="utf-8",
    )
    good = A2AEnvelope.new_reply(
        A2AEnvelope.new_request(
            caller="agent:a",
            callee="agent:b",
            action="x",
            payload={},
            reply_to=str(inbox),
            correlation_id="target-corr",
        ),
        success=True,
        outcome="ok",
    )
    enqueue_inbox(inbox, "a2a-reply-target-corr.json", good.to_dict())

    found = poll_reply("target-corr", inbox, timeout_seconds=0.5, poll_interval=0.05)
    assert found is not None
    assert found.success is True


def test_reply_to_caller_missing_reply_to_raises() -> None:
    req = A2AEnvelope.new_request(
        caller="agent:a",
        callee="agent:b",
        action="x",
        payload={},
        reply_to="",
    )
    with pytest.raises(A2ARoutingError, match="no reply_to"):
        reply_to_caller(req, success=True, outcome="ok")


def test_reply_to_caller_when_reply_to_is_file_path(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    req = A2AEnvelope.new_request(
        caller="agent:a",
        callee="agent:b",
        action="x",
        payload={},
        reply_to=str(inbox / "placeholder.json"),
    )
    path = reply_to_caller(req, success=True, outcome="ok", payload={"v": 1})
    assert path.parent == inbox
    assert path.name.startswith("a2a-reply-")


def test_await_reply_accepts_queue_root_or_inbox(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    corr = "await-root-test"
    reply = A2AEnvelope.new_reply(
        A2AEnvelope.new_request(
            caller="agent:a",
            callee="agent:b",
            action="x",
            payload={},
            reply_to=str(inbox),
            correlation_id=corr,
        ),
        success=True,
        outcome="ok",
    )
    enqueue_inbox(inbox, f"a2a-reply-{corr}.json", reply.to_dict())

    from_root = await_reply(corr, root, timeout_seconds=1.0, poll_interval=0.05)
    assert from_root is not None
    from_inbox = await_reply(corr, inbox, timeout_seconds=1.0, poll_interval=0.05)
    assert from_inbox is not None


def test_ask_agent_appends_audit_row(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    caller_dir = tmp_path / "caller"
    callee_dir = tmp_path / "callee"
    caller_m = _write_manifest(
        tmp_path, "agent:caller", queue_dir=str(caller_dir), callees=["agent:callee"]
    )
    callee_m = _write_manifest(tmp_path, "agent:callee", queue_dir=str(callee_dir))
    reg = _registry(tmp_path, [("agent:caller", caller_m), ("agent:callee", callee_m)])
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLog(audit_path)

    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg):
        result = ask_agent(
            "agent:caller",
            "agent:callee",
            "ping",
            {"k": "v"},
            audit=audit,
        )

    rows = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    assert rows[-1]["event"] == "a2a_ask"
    assert rows[-1]["correlation_id"] == result.correlation_id
    assert rows[-1]["caller"] == "agent:caller"
    assert audit.verify_chain().ok


def test_reply_to_caller_appends_audit_row(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    req = A2AEnvelope.new_request(
        caller="agent:a",
        callee="agent:b",
        action="x",
        payload={},
        reply_to=str(inbox),
        correlation_id="audit-reply-corr",
    )
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLog(audit_path)
    reply_to_caller(req, success=True, outcome="done", payload={"x": 1}, audit=audit)

    rows = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    assert rows[-1]["event"] == "a2a_reply"
    assert rows[-1]["correlation_id"] == "audit-reply-corr"
    assert rows[-1]["success"] is True


def _tier2_registry(tmp_path: Path) -> Registry:
    _scaffold_repo(tmp_path)
    principals = tmp_path / "config/memory/principals.yaml"
    principals.write_text(
        principals.read_text()
        + "  - id: agent:homelab-maintainer\n    kind: agent\n"
        + "  - id: agent:executive\n    kind: agent\n"
    )
    maint_m = _write_manifest(
        tmp_path,
        "agent:homelab-maintainer",
        queue_dir=str(tmp_path / "maintainer"),
        callees=["agent:executive"],
    )
    exec_m = _write_manifest(
        tmp_path,
        "agent:executive",
        queue_dir=str(tmp_path / "executive"),
    )
    return _registry(
        tmp_path,
        [("agent:homelab-maintainer", maint_m), ("agent:executive", exec_m)],
    )


def test_tier2_principal_unset() -> None:
    handler = make_tier2_executive_handler(caller=None)
    with mock.patch.dict("os.environ", {}, clear=True):
        ok, payload, reason = handler({"task_class": "x"})
    assert ok is False
    assert payload is None
    assert "AGENT_PRINCIPAL" in reason


def test_tier2_timeout_when_executive_never_replies(tmp_path: Path) -> None:
    reg = _tier2_registry(tmp_path)
    maint_dir = tmp_path / "maintainer"
    handler = make_tier2_executive_handler(
        caller="agent:homelab-maintainer",
        reply_queue_dir=maint_dir,
        ask_timeout_seconds=0.3,
        poll_interval=0.05,
    )
    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg):
        ok, payload, reason = handler({"task_class": "homelab.deploy"})
    assert ok is False
    assert payload is None
    assert "did not reply" in reason
    assert list((tmp_path / "executive" / "inbox").glob("a2a-*.json"))


def test_tier2_executive_declines(tmp_path: Path) -> None:
    reg = _tier2_registry(tmp_path)
    maint_dir = tmp_path / "maintainer"
    exec_dir = tmp_path / "executive"

    handler = make_tier2_executive_handler(
        caller="agent:homelab-maintainer",
        reply_queue_dir=maint_dir,
        ask_timeout_seconds=2.0,
        poll_interval=0.05,
    )

    def fake_ask(*args, **kwargs):
        result = ask_agent(*args, **kwargs)
        job_path = next((exec_dir / "inbox").glob("a2a-*.json"))
        request = A2AEnvelope.from_path(job_path)
        reply_to_caller(request, success=False, outcome="cannot help", payload={"code": 1})
        return result

    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg), mock.patch(
        "apps._shared.a2a.ask_agent", side_effect=fake_ask
    ):
        ok, payload, reason = handler({"task_class": "homelab.deploy"})

    assert ok is False
    assert payload == {"code": 1}
    assert "cannot help" in reason


def test_tier2_not_allowed_when_executive_not_in_acl(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    principals = tmp_path / "config/memory/principals.yaml"
    principals.write_text(
        principals.read_text()
        + "  - id: agent:homelab-maintainer\n    kind: agent\n"
        + "  - id: agent:executive\n    kind: agent\n"
    )
    maint_m = _write_manifest(
        tmp_path,
        "agent:homelab-maintainer",
        queue_dir=str(tmp_path / "maintainer"),
        callees=[],
    )
    exec_m = _write_manifest(
        tmp_path,
        "agent:executive",
        queue_dir=str(tmp_path / "executive"),
    )
    reg = _registry(
        tmp_path,
        [("agent:homelab-maintainer", maint_m), ("agent:executive", exec_m)],
    )
    handler = make_tier2_executive_handler(
        caller="agent:homelab-maintainer",
        reply_queue_dir=tmp_path / "maintainer",
    )
    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg):
        ok, payload, reason = handler({"task_class": "x"})
    assert ok is False
    assert "not allowed" in reason


class _FakeClock:
    def __init__(self, step: float = 1.0):
        self.step = step
        self.now = 0.0

    def __call__(self) -> float:
        out = self.now
        self.now += self.step
        return out


def test_dispatcher_tier2_with_a2a_executive_handler(tmp_path: Path) -> None:
    reg = _tier2_registry(tmp_path)
    maint_dir = tmp_path / "maintainer"
    exec_dir = tmp_path / "executive"

    def attempt(i, prev):
        return AttemptOutcome.HARD_FAIL, None, "ledger inconsistent"

    def fake_ask(*args, **kwargs):
        result = ask_agent(*args, **kwargs)
        job_path = next((exec_dir / "inbox").glob("a2a-*.json"))
        request = A2AEnvelope.from_path(job_path)
        reply_to_caller(
            request,
            success=True,
            outcome="executive handled",
            payload={"resolution": "fixed"},
        )
        return result

    tier2 = make_tier2_executive_handler(
        caller="agent:homelab-maintainer",
        reply_queue_dir=maint_dir,
        ask_timeout_seconds=2.0,
        poll_interval=0.05,
    )
    budgets = TierBudgets(
        tier1_budget_seconds=100,
        tier1_max_attempts=1,
        tier2_budget_seconds=1000,
        tier3_dm_after_seconds=2000,
    )
    audit: list[dict] = []

    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg), mock.patch(
        "apps._shared.a2a.ask_agent", side_effect=fake_ask
    ):
        dispatcher = Dispatcher(
            budgets=budgets,
            attempt=attempt,
            tier2=tier2,
            audit_hook=audit.append,
            clock=_FakeClock(),
        )
        result = dispatcher.execute(task_class="homelab.deploy", urgent=False)

    assert result.final_tier == 2
    assert result.outcome == "rerouted"
    assert result.payload == {"resolution": "fixed"}
    assert len(audit) == 1
    assert audit[0]["from_tier"] == 1
    assert audit[0]["to_tier"] == 2


def test_real_registry_allows_executive_from_maintainer() -> None:
    """Smoke: production registry permits maintainer → executive A2A."""
    reg = load_registry()
    assert_callee_allowed("agent:homelab-maintainer", "agent:executive", registry=reg)
