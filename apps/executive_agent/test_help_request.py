"""Tests for executive A2A help_request handling."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from apps._shared.a2a import A2AEnvelope, ask_agent, await_reply
from apps._shared.a2a.test_a2a import _registry, _scaffold_repo, _write_manifest
from apps.executive_agent import main as executive_main
from apps._shared.a2a.queue import enqueue_inbox, ensure_dirs
from apps.executive_agent.help_request import handle_help_request, render_help_request_description
from apps.executive_agent.help_request_dedup import dedupe_help_request_inbox


def test_render_help_request_description_includes_transitions() -> None:
    envelope = A2AEnvelope.new_request(
        caller="agent:homelab-maintainer",
        callee="agent:executive",
        action="help_request",
        payload={
            "task_class": "homelab.deploy",
            "blocked_reason": "ledger inconsistent",
            "transitions": [{"from_tier": 1, "to_tier": 2, "reason": "tier1 hard_fail"}],
        },
        reply_to="/tmp/inbox",
    )
    text = render_help_request_description(envelope)
    assert "homelab.deploy" in text
    assert "ledger inconsistent" in text
    assert "Tier 1 → 2" in text


def test_handle_help_request_creates_card_and_audit(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    envelope = A2AEnvelope.new_request(
        caller="agent:homelab-maintainer",
        callee="agent:executive",
        action="help_request",
        payload={"task_class": "homelab.deploy", "blocked_reason": "x"},
        reply_to=str(tmp_path / "reply-inbox"),
    )
    created = {
        "card": {"id": "card-99"},
        "list_id": "list-1",
    }

    with mock.patch.object(executive_main, "create_intake_card", return_value=created) as create_card:
        result = handle_help_request(envelope, state_dir=state_dir)

    assert result["ok"] is True
    assert "card created" in result["outcome"]
    assert result["reply_payload"]["card"]["card_id"] == "card-99"
    create_card.assert_called_once()

    ledger = (state_dir / "trust-ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(ledger) == 1
    row = json.loads(ledger[0])
    assert row["event"] == "a2a-help-request"
    assert row["correlation_id"] == envelope.correlation_id


def test_process_job_help_request_round_trip(tmp_path: Path) -> None:
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
        [("agent:homelab-maintainer", maint_m), ("agent:executive", exec_m)],
    )

    created = {"card": {"id": "c-1"}, "list_id": "l-1"}

    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg), mock.patch.object(
        executive_main, "create_intake_card", return_value=created
    ):
        ask = ask_agent(
            "agent:homelab-maintainer",
            "agent:executive",
            "help_request",
            {"task_class": "homelab.deploy", "blocked_reason": "failed"},
            timeout_seconds=30,
        )
        job_path = next((exec_dir / "inbox").glob("a2a-*.json"))
        executive_main.process_job(job_path, exec_dir)

        reply = await_reply(
            ask.correlation_id,
            maint_dir / "inbox",
            timeout_seconds=2.0,
            poll_interval=0.05,
        )

    assert reply is not None
    assert reply.success is True
    assert reply.reply_payload.get("card", {}).get("card_id") == "c-1"
    assert list((exec_dir / "done").glob("a2a-*.json"))


def test_handle_help_request_dedupes_by_correlation_id(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    queue_dir = tmp_path / "queue"
    ensure_dirs(queue_dir)
    envelope = A2AEnvelope.new_request(
        caller="agent:homelab-maintainer",
        callee="agent:executive",
        action="help_request",
        payload={"task_class": "homelab.deploy"},
        reply_to=str(tmp_path / "reply"),
        correlation_id="dup-corr-1",
    )
    created = {"card": {"id": "card-1"}, "list_id": "list-1"}
    with mock.patch.object(executive_main, "create_intake_card", return_value=created) as create_card:
        first = handle_help_request(envelope, state_dir=state_dir, queue_dir=queue_dir)
        second = handle_help_request(envelope, state_dir=state_dir, queue_dir=queue_dir)

    assert first["ok"] is True
    assert not first.get("deduped")
    assert second.get("deduped") is True
    create_card.assert_called_once()


def test_dedupe_help_request_inbox_archives_duplicates(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    queue_dir = tmp_path / "queue"
    dirs = ensure_dirs(queue_dir)
    inbox = dirs["inbox"]
    base = {
        "caller": "agent:homelab-maintainer",
        "callee": "agent:executive",
        "action": "help_request",
        "reply_to": "/tmp/r",
        "payload": {"task_class": "homelab.deploy"},
        "is_reply": False,
    }
    enqueue_inbox(inbox, "a2a-first.json", {**base, "correlation_id": "same-corr"})
    enqueue_inbox(inbox, "a2a-second.json", {**base, "correlation_id": "same-corr"})

    from apps._shared.a2a.queue import worker_inbox_job_paths

    jobs = worker_inbox_job_paths(inbox)
    kept = dedupe_help_request_inbox(jobs, queue_dir=queue_dir, state_dir=state_dir)

    assert len(kept) == 1
    assert len(list(dirs["done"].glob("*.json"))) == 1
