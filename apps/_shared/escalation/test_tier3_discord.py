"""Tests for Tier 3 Discord escalation handler."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from apps._shared.escalation import AttemptOutcome, Dispatcher
from apps._shared.escalation.test_dispatcher import FakeClock, _budgets
from apps._shared.escalation.tier3_discord import (
    Tier3DiscordError,
    format_tier3_message,
    make_tier3_discord_handler,
    resolve_approvals_channel_id,
)
from apps._shared.registry.loader import AgentManifest, Registry


def _manifest_with_approvals(channel_id: str, tmp_path) -> AgentManifest:
    return AgentManifest(
        "agent:executive",
        path=tmp_path / "agent-executive.yaml",
        data={
            "principal": "agent:executive",
            "display_name": "Executive",
            "domain": "x",
            "discord": {
                "channels": [
                    {"name": "#approvals", "id": channel_id, "mode": "write"},
                ]
            },
        },
    )


def test_format_tier3_message_includes_blocked_reason() -> None:
    text = format_tier3_message(
        {
            "task_class": "homelab.deploy",
            "urgent": True,
            "blocked_reason": "out of retries",
            "transitions": [{"from_tier": 1, "to_tier": 2, "reason": "hard_fail"}],
        }
    )
    assert "homelab.deploy" in text
    assert "out of retries" in text
    assert "1→2" in text


def test_resolve_approvals_channel_id_from_manifest(tmp_path) -> None:
    reg = Registry(
        schema_version=1,
        agents={"agent:executive": _manifest_with_approvals("999888777", tmp_path)},
    )
    assert resolve_approvals_channel_id("agent:executive", registry=reg) == "999888777"


def test_resolve_approvals_channel_id_env_override(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ESCALATION_APPROVALS_CHANNEL_ID", "111")
    reg = Registry(
        schema_version=1,
        agents={"agent:executive": _manifest_with_approvals("999", tmp_path)},
    )
    assert resolve_approvals_channel_id("agent:executive", registry=reg) == "111"


def test_tier3_handler_posts_to_channel() -> None:
    posts: list[tuple[str, str, str]] = []

    def fake_post(token: str, channel_id: str, content: str) -> dict:
        posts.append((token, channel_id, content))
        return {"id": "msg-42"}

    handler = make_tier3_discord_handler(
        channel_id="chan-1",
        token="bot-token",
        post_channel=fake_post,
    )
    outcome, payload = handler(
        {
            "task_class": "homelab.deploy",
            "urgent": False,
            "blocked_reason": "stuck",
            "transitions": [],
        }
    )
    assert outcome == "human_intervention"
    assert payload["message_id"] == "msg-42"
    assert len(posts) == 1
    assert posts[0][0] == "bot-token"
    assert posts[0][1] == "chan-1"
    assert "homelab.deploy" in posts[0][2]
def test_tier3_handler_urgent_sends_dm() -> None:
    dms: list[tuple[str, str, str]] = []

    def fake_post(token: str, channel_id: str, content: str) -> dict:
        return {"id": "msg-1"}

    def fake_dm(token: str, user_id: str, content: str) -> dict:
        dms.append((token, user_id, content))
        return {"channel_id": "dm-1", "message": {"id": "dm-msg-1"}}

    handler = make_tier3_discord_handler(
        channel_id="chan-1",
        token="bot-token",
        dm_user_ids=["user-kevin"],
        post_channel=fake_post,
        send_dm=fake_dm,
    )
    handler({"task_class": "x", "urgent": True, "blocked_reason": "y", "transitions": []})
    assert len(dms) == 1
    assert dms[0][1] == "user-kevin"


def test_dispatcher_with_tier3_discord_handler() -> None:
    posts: list[str] = []

    def fake_post(_token: str, _channel: str, content: str) -> dict:
        posts.append(content)
        return {"id": "m-1"}

    tier3 = make_tier3_discord_handler(
        channel_id="c",
        token="t",
        post_channel=fake_post,
    )

    def attempt(i, prev):
        return AttemptOutcome.HARD_FAIL, None, "broken"

    d = Dispatcher(
        budgets=_budgets(skip_tier2=True),
        attempt=attempt,
        tier3=tier3,
        clock=FakeClock(),
    )
    result = d.execute(task_class="homelab.deploy", urgent=True)
    assert result.final_tier == 3
    assert result.outcome == "human_intervention"
    assert posts


def test_tier3_handler_missing_token_raises() -> None:
    handler = make_tier3_discord_handler(channel_id="c", token="")
    with pytest.raises(Tier3DiscordError, match="DISCORD_BOT_TOKEN"):
        handler({"task_class": "x", "urgent": False, "blocked_reason": "y", "transitions": []})
