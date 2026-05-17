"""Discord bridge runtime shared by every per-agent bot."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

try:
    import discord
except ModuleNotFoundError as exc:  # pragma: no cover
    discord = None  # type: ignore[assignment]
    _DISCORD_IMPORT_ERROR = exc
else:
    _DISCORD_IMPORT_ERROR = None


REPLY_CHUNK_LIMIT = 1800


def _split_ids(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _chunk(text: str, limit: int = REPLY_CHUNK_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


@dataclass
class MessageContext:
    """Parsed message handed to user-provided handlers.

    ``content`` is the message body with the bot's prefix and any direct
    mentions of the bot already stripped; ``raw_content`` is the original.
    """

    content: str
    raw_content: str
    is_dm: bool
    source: str
    source_ref: str
    user_id: str
    guild_id: str | None
    channel_id: str
    message: Any  # discord.Message; typed Any so importers don't need discord


Handler = Callable[[MessageContext], Awaitable[Optional[str]]]


@dataclass
class BridgeConfig:
    """Per-agent configuration for :func:`run_bridge`."""

    principal: str
    """Agent principal id, e.g. ``agent:homelab-maintainer``."""

    bot_label: str
    """Short label used in log lines, e.g. ``homelab-maintainer``."""

    command_prefix: str
    """Prefix that triggers the bot in non-DM contexts, e.g. ``!maintainer``."""

    handler: Handler
    """Async callable: ``async (ctx: MessageContext) -> str | None``."""

    audit_log_path: Path
    """Path to the agent's hash-chained trust ledger (per-agent file)."""

    audit_event: str = "discord-message"
    """Event name written to the audit log."""

    extra_env: dict[str, str] = field(default_factory=dict)
    """Optional extra metadata recorded in audit entries."""


def _parse_message(
    message: "discord.Message",
    bot_user_id: int,
    command_prefix: str,
) -> MessageContext | None:
    if message.author.bot:
        return None

    is_dm = message.guild is None
    bot_mentioned = bot_user_id in (u.id for u in message.mentions)
    starts_with_prefix = message.content.startswith(command_prefix)
    if not (is_dm or bot_mentioned or starts_with_prefix):
        return None

    content = message.content
    if starts_with_prefix:
        content = content.removeprefix(command_prefix).strip()
    content = (
        content.replace(f"<@{bot_user_id}>", "")
        .replace(f"<@!{bot_user_id}>", "")
        .strip()
    )

    if is_dm:
        source = "discord-dm"
        source_ref = f"dm:{message.author.id}"
        guild_id: str | None = None
    else:
        source = "discord-channel"
        source_ref = f"guild:{message.guild.id}:channel:{message.channel.id}"
        guild_id = str(message.guild.id)

    return MessageContext(
        content=content,
        raw_content=message.content,
        is_dm=is_dm,
        source=source,
        source_ref=source_ref,
        user_id=str(message.author.id),
        guild_id=guild_id,
        channel_id=str(message.channel.id),
        message=message,
    )


def _audit_log(config: BridgeConfig):
    _root = Path(__file__).resolve().parents[2]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from apps._shared.audit import AuditLog

    config.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    return AuditLog(config.audit_log_path)


async def run_bridge(config: BridgeConfig) -> int:
    """Run a Discord bridge for one agent until cancelled."""

    if discord is None:
        raise RuntimeError(
            "discord.py is required for the Discord bridge."
        ) from _DISCORD_IMPORT_ERROR

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token or token == "replace-me":
        raise ValueError(f"DISCORD_BOT_TOKEN must be configured for {config.principal}")

    allowed_users = _split_ids(os.environ.get("DISCORD_ALLOWED_USER_IDS", ""))
    allowed_channels = _split_ids(os.environ.get("DISCORD_ALLOWED_CHANNEL_IDS", ""))

    audit = _audit_log(config)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.dm_messages = True
    intents.members = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        print(f"{config.bot_label} discord bridge connected as {client.user}")

    @client.event
    async def on_message(message: "discord.Message") -> None:
        ctx = _parse_message(message, client.user.id, config.command_prefix)
        if ctx is None:
            return
        if allowed_users and ctx.user_id not in allowed_users:
            return
        if not ctx.is_dm and allowed_channels and ctx.channel_id not in allowed_channels:
            return

        audit.append(
            {
                "event": config.audit_event,
                "principal": config.principal,
                "direction": "inbound",
                "source": ctx.source,
                "source_ref": ctx.source_ref,
                "source_user": ctx.user_id,
                "content_len": len(ctx.content),
                **config.extra_env,
            }
        )

        try:
            reply = await config.handler(ctx)
        except Exception as exc:  # noqa: BLE001
            logging.exception("%s handler error", config.principal)
            audit.append(
                {
                    "event": config.audit_event,
                    "principal": config.principal,
                    "direction": "error",
                    "source": ctx.source,
                    "source_ref": ctx.source_ref,
                    "error": repr(exc),
                    **config.extra_env,
                }
            )
            try:
                await message.channel.send(
                    f"{config.bot_label}: handler error ({type(exc).__name__}); see logs."
                )
            except Exception:  # noqa: BLE001
                pass
            return

        if reply is None:
            return

        for chunk in _chunk(reply):
            await message.channel.send(chunk)

        audit.append(
            {
                "event": config.audit_event,
                "principal": config.principal,
                "direction": "outbound",
                "source": ctx.source,
                "source_ref": ctx.source_ref,
                "reply_len": len(reply),
                **config.extra_env,
            }
        )

    await client.start(token)
    return 0
