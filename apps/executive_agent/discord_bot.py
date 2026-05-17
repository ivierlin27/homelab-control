#!/usr/bin/env python3
"""Discord bridge for the executive assistant."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "executive_agent"))

import main as executive  # noqa: E402
from chat_core import handle_chat_turn  # noqa: E402
from conversation_store import ConversationStore  # noqa: E402


try:
    import discord
except ModuleNotFoundError as exc:  # pragma: no cover - depends on runtime environment
    discord = None
    DISCORD_IMPORT_ERROR = exc
else:
    DISCORD_IMPORT_ERROR = None


DEFAULT_STATE_DIR = Path.home() / ".local/state/homelab-control/agent-executive"


def split_ids(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def discord_source(message: "discord.Message") -> tuple[str, str, dict[str, str]]:
    if message.guild is None:
        return (
            "discord-dm",
            f"dm:{message.author.id}",
            {
                "user_id": str(message.author.id),
                "channel_id": str(message.channel.id),
            },
        )
    return (
        "discord-channel",
        f"guild:{message.guild.id}:channel:{message.channel.id}",
        {
            "user_id": str(message.author.id),
            "guild_id": str(message.guild.id),
            "channel_id": str(message.channel.id),
        },
    )


def chunk_message(text: str, limit: int = 1800) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    current = text
    while current:
        chunks.append(current[:limit])
        current = current[limit:]
    return chunks


async def run_bot(args: argparse.Namespace) -> int:
    if discord is None:
        raise RuntimeError(
            "discord.py is required for the Discord bridge. Install apps/executive_agent/requirements.txt."
        ) from DISCORD_IMPORT_ERROR

    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token or token == "replace-me":
        raise ValueError("DISCORD_BOT_TOKEN must be configured")

    state_dir = Path(args.state_dir).expanduser()
    db_path = Path(args.db).expanduser() if args.db else state_dir / "conversations.sqlite3"
    store = ConversationStore(db_path)
    policy_path = Path(args.policy).expanduser()
    allowed_users = split_ids(os.environ.get("DISCORD_ALLOWED_USER_IDS", ""))
    command_prefix = os.environ.get("DISCORD_COMMAND_PREFIX", "!assistant")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.dm_messages = True
    intents.members = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        print(f"executive discord bot connected as {client.user}")

    @client.event
    async def on_message(message: "discord.Message") -> None:
        if message.author.bot:
            return
        if allowed_users and str(message.author.id) not in allowed_users:
            return

        is_dm = message.guild is None
        bot_mentioned = client.user is not None and client.user in message.mentions
        starts_with_prefix = message.content.startswith(command_prefix)
        if not (is_dm or bot_mentioned or starts_with_prefix):
            return

        content = message.content
        if starts_with_prefix:
            content = content.removeprefix(command_prefix).strip()
        if client.user is not None:
            content = content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
        if not content:
            content = "status"

        source, source_ref, metadata = discord_source(message)
        conversation = store.conversation_for_source(
            source=source,
            source_ref=source_ref,
            default_title=f"Discord {source_ref}",
            owner=str(message.author.id),
            domain=os.environ.get("DISCORD_DEFAULT_DOMAIN", "homelab"),
            task_type=os.environ.get("DISCORD_DEFAULT_TASK_TYPE", "research"),
            plan_ready=os.environ.get("DISCORD_PLAN_READY", "").lower() in {"1", "true", "yes"},
            write_memory=os.environ.get("DISCORD_WRITE_MEMORY", "").lower() in {"1", "true", "yes"},
            search_memory=os.environ.get("DISCORD_SEARCH_MEMORY", "true").lower() in {"1", "true", "yes"},
            metadata=metadata,
        )

        lowered = content.lower().strip()
        if lowered in {"help", "/help"}:
            reply = "\n".join(
                [
                    "Executive assistant commands:",
                    f"- `{command_prefix} help`",
                    f"- `{command_prefix} status`",
                    f"- `{command_prefix} weekly-review`",
                    f"- Mention me or DM me with a normal request to create/delegate work.",
                ]
            )
        elif lowered in {"status", "/status"}:
            status = executive.queue_status(state_dir)
            reply = f"Executive queue: {status['counts']}; failed jobs: {len(status['failed_jobs'])}"
        elif lowered in {"weekly-review", "/weekly-review"}:
            review = executive.weekly_review(state_dir)
            reply = "\n".join(review.get("summary", []))
        else:
            result = await asyncio.to_thread(
                handle_chat_turn,
                store=store,
                conversation=conversation,
                message=content,
                source=source,
                source_ref=source_ref,
                source_user=str(message.author.id),
                metadata=metadata,
                state_dir=state_dir,
                policy_path=policy_path,
                dry_run=os.environ.get("DISCORD_DRY_RUN", "true").lower() in {"1", "true", "yes"},
            )
            reply = result["reply"]

        for chunk in chunk_message(reply):
            await message.channel.send(chunk)

    await client.start(token)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default=os.environ.get("EXECUTIVE_STATE_DIR", str(DEFAULT_STATE_DIR)))
    parser.add_argument("--db", default=os.environ.get("EXECUTIVE_CHAT_DB", ""))
    parser.add_argument("--policy", default=os.environ.get("EXECUTIVE_POLICY", str(executive.DEFAULT_POLICY)))
    args = parser.parse_args()
    return asyncio.run(run_bot(args))


if __name__ == "__main__":
    raise SystemExit(main())
