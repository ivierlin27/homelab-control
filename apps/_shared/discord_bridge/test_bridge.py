"""Unit tests for the shared Discord bridge (Phase 0.7)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps._shared.discord_bridge.bridge import (
    _chunk,
    _parse_message,
    _split_ids,
    manifest_channel_index,
)


BOT_ID = 999


@dataclass
class FakeAuthor:
    id: int
    bot: bool = False


@dataclass
class FakeGuild:
    id: int


@dataclass
class FakeChannel:
    id: int


def _msg(content: str, *, guild_id: int | None = 1, author_bot: bool = False, mentions_ids: list[int] | None = None):
    mentions = [SimpleNamespace(id=mid) for mid in (mentions_ids or [])]
    return SimpleNamespace(
        content=content,
        author=FakeAuthor(id=42, bot=author_bot),
        guild=FakeGuild(id=guild_id) if guild_id is not None else None,
        channel=FakeChannel(id=7),
        mentions=mentions,
    )


def test_parse_ignores_other_bots():
    m = _msg("hello", author_bot=True)
    assert _parse_message(m, BOT_ID, "!agent") is None


def test_parse_ignores_unaddressed_guild_messages():
    m = _msg("just chatting in the channel")
    assert _parse_message(m, BOT_ID, "!agent") is None


def test_parse_strips_prefix_in_guild():
    m = _msg("!agent status")
    ctx = _parse_message(m, BOT_ID, "!agent")
    assert ctx is not None
    assert ctx.content == "status"
    assert ctx.is_dm is False
    assert ctx.source == "discord-channel"
    assert ctx.source_ref == "guild:1:channel:7"


def test_parse_handles_dm():
    m = _msg("hi", guild_id=None)
    ctx = _parse_message(m, BOT_ID, "!agent")
    assert ctx is not None
    assert ctx.is_dm is True
    assert ctx.source == "discord-dm"
    assert ctx.source_ref == "dm:42"


def test_parse_strips_bot_mention():
    m = _msg(f"<@{BOT_ID}> please status", mentions_ids=[BOT_ID])
    ctx = _parse_message(m, BOT_ID, "!agent")
    assert ctx is not None
    assert ctx.content == "please status"


def test_parse_strips_bang_mention_variant():
    m = _msg(f"<@!{BOT_ID}> status", mentions_ids=[BOT_ID])
    ctx = _parse_message(m, BOT_ID, "!agent")
    assert ctx is not None
    assert ctx.content == "status"


def test_split_ids_handles_csv_and_whitespace():
    assert _split_ids(" 1, 2 ,3, ") == {"1", "2", "3"}
    assert _split_ids("") == set()


def test_chunk_passes_short_through():
    assert _chunk("short") == ["short"]


def test_chunk_splits_at_limit():
    text = "x" * 4000
    chunks = _chunk(text, limit=1000)
    assert chunks == ["x" * 1000] * 4


def test_chunk_preserves_total_bytes():
    text = "abcdefghij" * 250
    chunks = _chunk(text, limit=137)
    assert "".join(chunks) == text


def test_chunk_handles_exact_limit_boundary():
    text = "x" * 1800
    chunks = _chunk(text, limit=1800)
    assert chunks == [text]


# ---------------------------------------------------------------------------
# manifest_channel_index
# ---------------------------------------------------------------------------


class _FakeManifest:
    """Duck-typed manifest matching AgentManifest.get() shape."""

    def __init__(self, channels):
        self._channels = channels

    def get(self, *keys, default=None):
        # Only handles ("discord", "channels", default=[]) — that's all the
        # bridge calls in practice.
        if keys == ("discord", "channels"):
            return self._channels
        return default


def test_manifest_channel_index_skips_name_only_entries():
    m = _FakeManifest([
        {"name": "#ops", "mode": "write"},  # no id; cannot enforce
        {"id": 123, "name": "#homelab", "mode": "write"},
        {"id": "456", "name": "#approvals", "mode": "read"},
    ])
    idx = manifest_channel_index(m)
    assert idx == {
        "123": {"name": "#homelab", "mode": "write"},
        "456": {"name": "#approvals", "mode": "read"},
    }


def test_manifest_channel_index_accepts_list_directly():
    idx = manifest_channel_index([{"id": 7, "name": "#x", "mode": "write"}])
    assert idx == {"7": {"name": "#x", "mode": "write"}}


def test_manifest_channel_index_handles_empty_and_garbage():
    assert manifest_channel_index(_FakeManifest([])) == {}
    assert manifest_channel_index(_FakeManifest([{"mode": "write"}])) == {}
    assert manifest_channel_index(None) == {}
    assert manifest_channel_index(_FakeManifest([{"id": None}])) == {}


def test_manifest_channel_index_defaults_mode_to_write():
    m = _FakeManifest([{"id": 9, "name": "#x"}])  # mode omitted
    assert manifest_channel_index(m)["9"]["mode"] == "write"


# ---------------------------------------------------------------------------
# run_bridge integration: manifest is authoritative for the allowlist + read mode
# ---------------------------------------------------------------------------


def _install_fake_discord(monkeypatch, captured):
    """Replace bridge.discord with a fake module that gives us hooks into
    Client.event registration and Client.start. ``captured`` is a dict the
    test fills out (on_ready / on_message handlers, started_with_token)."""

    class _FakeIntents:
        message_content = False
        dm_messages = False
        members = False

        @classmethod
        def default(cls):
            return cls()

    class _FakeUser:
        id = 999

    class _FakeClient:
        def __init__(self, intents=None):
            self.user = _FakeUser()
            self.intents = intents

        def event(self, fn):
            captured[fn.__name__] = fn
            return fn

        async def start(self, token):
            captured["started_with_token"] = token

    fake = SimpleNamespace(
        Client=_FakeClient,
        Intents=_FakeIntents,
        Message=object,
    )
    import apps._shared.discord_bridge.bridge as bridge_mod
    monkeypatch.setattr(bridge_mod, "discord", fake)
    return bridge_mod


@pytest.mark.asyncio
async def test_run_bridge_uses_manifest_channel_allowlist(monkeypatch, tmp_path, caplog):
    """When manifest has IDs and env var is set with a disagreeing set, the
    manifest wins (with a warning logged)."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("DISCORD_ALLOWED_CHANNEL_IDS", "9999")  # disagrees
    captured: dict = {}
    bridge_mod = _install_fake_discord(monkeypatch, captured)
    monkeypatch.setattr(
        bridge_mod,
        "_try_load_manifest_channels",
        lambda principal: {
            "100": {"name": "#ops", "mode": "write"},
            "200": {"name": "#approvals", "mode": "read"},
        },
    )

    from apps._shared.discord_bridge.bridge import BridgeConfig, run_bridge

    async def handler(_ctx):
        return "ok"

    config = BridgeConfig(
        principal="agent:test",
        bot_label="test",
        command_prefix="!t",
        handler=handler,
        audit_log_path=tmp_path / "trust-ledger.jsonl",
    )
    with caplog.at_level("WARNING"):
        await run_bridge(config)
    assert captured["started_with_token"] == "fake-token"
    # Warning surfaced about env-var disagreement.
    assert any("disagrees with manifest" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_run_bridge_blocks_outbound_on_read_mode_channel(monkeypatch, tmp_path):
    """A handler that returns a reply must NOT be posted to a channel the
    manifest marked as read-only; a write-denied audit row is emitted."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake-token")
    monkeypatch.delenv("DISCORD_ALLOWED_USER_IDS", raising=False)
    monkeypatch.delenv("DISCORD_ALLOWED_CHANNEL_IDS", raising=False)
    captured: dict = {}
    bridge_mod = _install_fake_discord(monkeypatch, captured)
    monkeypatch.setattr(
        bridge_mod,
        "_try_load_manifest_channels",
        lambda principal: {
            "200": {"name": "#approvals", "mode": "read"},
        },
    )

    from apps._shared.discord_bridge.bridge import BridgeConfig, run_bridge

    sent: list[str] = []

    async def handler(_ctx):
        return "should-not-post"

    config = BridgeConfig(
        principal="agent:test",
        bot_label="test",
        command_prefix="!t",
        handler=handler,
        audit_log_path=tmp_path / "trust-ledger.jsonl",
    )
    await run_bridge(config)
    on_message = captured["on_message"]

    class _FakeChan:
        id = 200

        async def send(self, text):
            sent.append(text)

    class _FakeAuth:
        id = 42
        bot = False

    class _FakeGuild:
        id = 1

    fake_msg = SimpleNamespace(
        content="!t hello",
        author=_FakeAuth(),
        guild=_FakeGuild(),
        channel=_FakeChan(),
        mentions=[],
    )
    await on_message(fake_msg)
    assert sent == []  # outbound blocked
    audit_lines = (tmp_path / "trust-ledger.jsonl").read_text().splitlines()
    # Two rows: inbound + write-denied (no outbound row).
    directions = []
    import json as _json
    for line in audit_lines:
        directions.append(_json.loads(line).get("direction"))
    assert directions == ["inbound", "write-denied"]


@pytest.mark.asyncio
async def test_run_bridge_posts_on_write_mode_channel(monkeypatch, tmp_path):
    """Sanity check: same setup but channel mode=write — reply is posted and
    audit logs inbound + outbound with channel metadata enriched."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake-token")
    monkeypatch.delenv("DISCORD_ALLOWED_USER_IDS", raising=False)
    monkeypatch.delenv("DISCORD_ALLOWED_CHANNEL_IDS", raising=False)
    captured: dict = {}
    bridge_mod = _install_fake_discord(monkeypatch, captured)
    monkeypatch.setattr(
        bridge_mod,
        "_try_load_manifest_channels",
        lambda principal: {
            "100": {"name": "#ops", "mode": "write"},
        },
    )

    from apps._shared.discord_bridge.bridge import BridgeConfig, run_bridge

    sent: list[str] = []

    async def handler(_ctx):
        return "reply-body"

    config = BridgeConfig(
        principal="agent:test",
        bot_label="test",
        command_prefix="!t",
        handler=handler,
        audit_log_path=tmp_path / "trust-ledger.jsonl",
    )
    await run_bridge(config)
    on_message = captured["on_message"]

    class _FakeChan:
        id = 100

        async def send(self, text):
            sent.append(text)

    class _FakeAuth:
        id = 42
        bot = False

    fake_msg = SimpleNamespace(
        content="!t hello",
        author=_FakeAuth(),
        guild=SimpleNamespace(id=1),
        channel=_FakeChan(),
        mentions=[],
    )
    await on_message(fake_msg)
    assert sent == ["reply-body"]
    import json as _json
    rows = [_json.loads(line) for line in (tmp_path / "trust-ledger.jsonl").read_text().splitlines()]
    assert [r["direction"] for r in rows] == ["inbound", "outbound"]
    for r in rows:
        assert r["channel_name"] == "#ops"
        assert r["channel_mode"] == "write"


@pytest.mark.asyncio
async def test_run_bridge_requires_token(monkeypatch, tmp_path):
    pytest.importorskip("discord")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    from apps._shared.discord_bridge.bridge import BridgeConfig, run_bridge

    async def handler(_ctx):
        return None

    config = BridgeConfig(
        principal="agent:test",
        bot_label="test",
        command_prefix="!t",
        handler=handler,
        audit_log_path=tmp_path / "trust-ledger.jsonl",
    )
    with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
        await run_bridge(config)
