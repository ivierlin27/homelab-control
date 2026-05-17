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

from apps._shared.discord_bridge.bridge import _chunk, _parse_message, _split_ids


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
