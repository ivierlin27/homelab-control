"""Production-path E2E tests for A2A + escalation.

These exercise the same code paths as Alienware (``ask_agent`` → executive
``process_job`` → ``reply_to_caller`` → ``await_reply``) without mocking the
worker. Planka and Discord are mocked or degraded unless ``HOMELAB_E2E_LIVE=1``.

Run the optional live suite on a host with registry + credentials::

    HOMELAB_E2E_LIVE=1 pytest apps/_shared/a2a/test_e2e_production.py -m live -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from apps._shared.a2a import ask_agent, await_reply, make_tier2_executive_handler
from apps._shared.a2a.e2e_helpers import (
    ask_agent_with_executive_worker,
    build_maintainer_executive_registry,
    process_executive_inbox_job,
    production_registry_available,
)
from apps._shared.a2a.test_a2a import _FakeClock
from apps._shared.escalation import AttemptOutcome, Dispatcher
from apps._shared.escalation.policy import TierBudgets
from apps._shared.escalation.tier3_discord import make_tier3_discord_handler
from apps._shared.registry import load_registry
from apps.executive_agent import main as executive_main

pytestmark_e2e = pytest.mark.e2e
pytestmark_live = pytest.mark.live


def _help_payload(task_class: str = "e2e.help_request") -> dict:
    return {
        "task_class": task_class,
        "blocked_reason": "e2e production path",
        "urgent": False,
        "transitions": [{"from_tier": 1, "to_tier": 2, "reason": "e2e hard_fail"}],
    }


@pytest.mark.e2e
def test_e2e_help_request_worker_path(tmp_path: Path) -> None:
    """Maintainer ask → executive process_job → reply (no mocked worker)."""
    reg, maint_dir, exec_dir = build_maintainer_executive_registry(tmp_path)
    state_dir = exec_dir  # executive state alongside queue in tests

    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg), mock.patch.object(
        executive_main,
        "create_intake_card",
        side_effect=RuntimeError("HTTP Error 401: Unauthorized"),
    ):
        result = ask_agent_with_executive_worker(
            exec_dir,
            "agent:homelab-maintainer",
            "agent:executive",
            "help_request",
            _help_payload(),
            timeout_seconds=30,
        )
        reply = await_reply(
            result.correlation_id,
            maint_dir / "inbox",
            timeout_seconds=2.0,
            poll_interval=0.05,
        )

    assert reply is not None
    assert reply.success is True
    assert "acknowledged" in (reply.outcome or "")
    assert reply.reply_payload.get("card", {}).get("created") is False
    assert "401" in reply.reply_payload.get("card", {}).get("error", "")

    done_jobs = list((exec_dir / "done").glob("a2a-*.json"))
    assert done_jobs, "executive worker should move job to done/"
    assert not list((exec_dir / "inbox").glob("a2a-*.json"))

    ledger_lines = (state_dir / "trust-ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert ledger_lines
    event = json.loads(ledger_lines[-1])
    assert event["event"] == "a2a-help-request"
    assert event["correlation_id"] == result.correlation_id


@pytest.mark.e2e
def test_e2e_tier2_handler_with_real_executive_worker(tmp_path: Path) -> None:
    """Tier 2 handler + synchronous executive worker (no fake_ask mock)."""
    reg, maint_dir, exec_dir = build_maintainer_executive_registry(tmp_path)

    handler = make_tier2_executive_handler(
        caller="agent:homelab-maintainer",
        reply_queue_dir=maint_dir,
        ask_timeout_seconds=5.0,
        poll_interval=0.05,
    )

    def ask_and_process(*args, **kwargs):
        result = ask_agent_with_executive_worker(exec_dir, *args, **kwargs)
        return result

    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg), mock.patch(
        "apps._shared.a2a.ask_agent", side_effect=ask_and_process
    ), mock.patch.object(
        executive_main,
        "create_intake_card",
        return_value={"card": {"id": "e2e-card"}, "list_id": "list-1"},
    ):
        ok, payload, reason = handler(_help_payload("homelab.deploy"))

    assert ok is True
    assert payload.get("card", {}).get("card_id") == "e2e-card"
    assert "acknowledged" in reason or "card created" in reason


@pytest.mark.e2e
def test_e2e_dispatcher_tier2_worker_success_stops_at_tier2(tmp_path: Path) -> None:
    """Tier1 hard_fail → tier2 with real executive worker resolves at tier 2."""
    reg, maint_dir, exec_dir = build_maintainer_executive_registry(tmp_path)

    tier2 = make_tier2_executive_handler(
        caller="agent:homelab-maintainer",
        reply_queue_dir=maint_dir,
        ask_timeout_seconds=5.0,
        poll_interval=0.05,
    )

    def attempt(i, prev):
        return AttemptOutcome.HARD_FAIL, None, "e2e ledger inconsistent"

    def ask_and_process(*args, **kwargs):
        return ask_agent_with_executive_worker(exec_dir, *args, **kwargs)

    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg), mock.patch(
        "apps._shared.a2a.ask_agent", side_effect=ask_and_process
    ), mock.patch.object(
        executive_main,
        "create_intake_card",
        return_value={"card": {"id": "c-disp"}, "list_id": "l-1"},
    ):
        result = Dispatcher(
            budgets=TierBudgets(tier1_budget_seconds=100, tier1_max_attempts=1),
            attempt=attempt,
            tier2=tier2,
            clock=_FakeClock(),
        ).execute(task_class="homelab.deploy")

    assert result.final_tier == 2
    assert result.outcome == "rerouted"
    assert result.payload.get("card", {}).get("card_id") == "c-disp"


@pytest.mark.e2e
def test_e2e_dispatcher_tier2_decline_reaches_tier3_discord(tmp_path: Path) -> None:
    """Tier2 executive declines via real worker path → tier3 Discord post."""
    reg, maint_dir, exec_dir = build_maintainer_executive_registry(tmp_path)
    posts: list[str] = []

    def fake_post(_token: str, _channel: str, content: str) -> dict:
        posts.append(content)
        return {"id": "discord-e2e-1"}

    from apps._shared.a2a import reply_to_caller

    def declining_help(envelope, **kwargs):
        reply_to_caller(
            envelope,
            success=False,
            outcome="executive declined",
            payload={"code": "e2e_decline"},
        )
        return {
            "ok": False,
            "outcome": "executive declined",
            "reply_payload": {"code": "e2e_decline"},
        }

    tier2 = make_tier2_executive_handler(
        caller="agent:homelab-maintainer",
        reply_queue_dir=maint_dir,
        ask_timeout_seconds=5.0,
        poll_interval=0.05,
    )
    tier3 = make_tier3_discord_handler(
        channel_id="chan-e2e",
        token="token-e2e",
        post_channel=fake_post,
    )

    def attempt(i, prev):
        return AttemptOutcome.HARD_FAIL, None, "e2e ledger inconsistent"

    def ask_and_process(*args, **kwargs):
        result = ask_agent(*args, **kwargs)
        with mock.patch(
            "apps.executive_agent.help_request.handle_help_request",
            side_effect=declining_help,
        ):
            process_executive_inbox_job(exec_dir)
        return result

    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg), mock.patch(
        "apps._shared.a2a.ask_agent", side_effect=ask_and_process
    ):
        result = Dispatcher(
            budgets=TierBudgets(
                tier1_budget_seconds=100,
                tier1_max_attempts=1,
                tier2_budget_seconds=1000,
                tier3_dm_after_seconds=2000,
            ),
            attempt=attempt,
            tier2=tier2,
            tier3=tier3,
            clock=_FakeClock(),
        ).execute(task_class="homelab.deploy", urgent=True)

    assert result.final_tier == 3
    assert result.outcome == "human_intervention"
    assert posts
    assert "homelab.deploy" in posts[0]


@pytest.mark.e2e
def test_e2e_process_job_idempotent_on_done_queue(tmp_path: Path) -> None:
    """Second process_job call is not needed; job leaves inbox after first pass."""
    reg, maint_dir, exec_dir = build_maintainer_executive_registry(tmp_path)

    with mock.patch("apps._shared.a2a.routing.load_registry", return_value=reg), mock.patch.object(
        executive_main,
        "create_intake_card",
        return_value={"card": {"id": "once"}, "list_id": "l"},
    ):
        ask_agent_with_executive_worker(
            exec_dir,
            "agent:homelab-maintainer",
            "agent:executive",
            "help_request",
            _help_payload(),
        )

    assert not list((exec_dir / "inbox").glob("a2a-*.json"))
    with pytest.raises(FileNotFoundError):
        process_executive_inbox_job(exec_dir)


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("HOMELAB_E2E_LIVE") != "1",
    reason="set HOMELAB_E2E_LIVE=1 to run against the repo registry on this host",
)
@pytest.mark.skipif(
    not production_registry_available(),
    reason="homelab-control registry not loadable from cwd",
)
def test_live_registry_allows_maintainer_executive_a2a() -> None:
    """Smoke contract: production registry permits maintainer → executive."""
    from apps._shared.a2a.routing import assert_callee_allowed

    reg = load_registry()
    assert_callee_allowed("agent:homelab-maintainer", "agent:executive", registry=reg)
    exec_manifest = reg.get("agent:executive")
    maint_manifest = reg.get("agent:homelab-maintainer")
    assert exec_manifest.get("queue_dir")
    assert "agent:executive" in (maint_manifest.get("a2a", "allowed_callees", default=[]) or [])


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("HOMELAB_E2E_LIVE") != "1",
    reason="set HOMELAB_E2E_LIVE=1 for live queue smoke on this host",
)
@pytest.mark.skipif(
    os.environ.get("HOMELAB_E2E_LIVE_QUEUES") != "1",
    reason="set HOMELAB_E2E_LIVE_QUEUES=1 to hit real agent queue dirs (side effects)",
)
def test_live_help_request_on_host_queues() -> None:
    """Optional Alienware smoke: uses real queue dirs; safe correlation_id prefix."""
    from apps._shared.a2a import ask_agent, await_reply
    from apps._shared.a2a.routing import resolve_queue_dir

    reg = load_registry()
    exec_dir = resolve_queue_dir("agent:executive", registry=reg)
    maint_inbox = resolve_queue_dir("agent:homelab-maintainer", registry=reg) / "inbox"
    maint_inbox.mkdir(parents=True, exist_ok=True)

    payload = _help_payload("live.smoke.help_request")
    result = ask_agent(
        "agent:homelab-maintainer",
        "agent:executive",
        "help_request",
        payload,
        timeout_seconds=120,
    )
    assert "live.smoke" in payload["task_class"]

    reply = await_reply(
        result.correlation_id,
        maint_inbox,
        timeout_seconds=90,
        poll_interval=1.0,
    )
    assert reply is not None, "executive worker must be running (alienware-executive-agent.service)"
    assert reply.success is True
