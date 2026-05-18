#!/usr/bin/env python3
"""Print a YAML-ready ``id: <snowflake>`` map for every channel a bot can see.

Used to backfill ``config/agents/agent-<principal>.yaml :: discord.channels[].id``
so the bridge's manifest-driven channel allowlist becomes enforceable. The
operator runs this once per bot identity (each bot only sees the channels it
has been invited to), greps for the channels the agent should care about, and
pastes the ``id:`` values into the manifest next to the existing ``name:``
entries.

Usage:
    DISCORD_BOT_TOKEN=<token-for-the-bot> \
        python3 scripts/discord/dump-channel-ids.py [--guild-id <id>]

The token MUST be the bot's own token; a user token will not work and would
be rejected by Discord anyway.

This script does NOT modify any manifests on its own — that's an operator
decision (and we want git diffs to show the change). It only prints; pipe to
a file if you want a record:

    python3 scripts/discord/dump-channel-ids.py > /tmp/discord-channels.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--guild-id",
        default=os.environ.get("DISCORD_GUILD_ID", ""),
        help="restrict output to one guild (optional; default: all the bot is in)",
    )
    args = parser.parse_args()

    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token or token == "replace-me":
        print("error: DISCORD_BOT_TOKEN must be set in the environment", file=sys.stderr)
        return 2

    try:
        import discord
    except ModuleNotFoundError:
        print("error: discord.py not installed in this venv", file=sys.stderr)
        return 2

    target_guild_id = int(args.guild_id) if args.guild_id else None
    intents = discord.Intents.default()
    intents.members = True
    client = discord.Client(intents=intents)
    captured: dict = {"guilds": []}

    @client.event
    async def on_ready() -> None:  # type: ignore[no-redef]
        for guild in client.guilds:
            if target_guild_id is not None and guild.id != target_guild_id:
                continue
            captured["guilds"].append({
                "guild_id": guild.id,
                "guild_name": guild.name,
                "channels": [
                    {
                        "id": ch.id,
                        "name": f"#{ch.name}",
                        "type": str(getattr(ch, "type", "")).rsplit(".", 1)[-1],
                    }
                    for ch in guild.channels
                ],
            })
        await client.close()

    asyncio.run(client.start(token))

    if not captured["guilds"]:
        print("# (bot is in no guilds; or guild-id filter excluded all)")
        return 1

    print("# Channel inventory — paste relevant `id:` values into")
    print("# config/agents/agent-<principal>.yaml under discord.channels[].")
    print("# (only `text` channels are usable by the bridge; voice/forum are listed for ref.)")
    for g in captured["guilds"]:
        print(f"")
        print(f"# guild: {g['guild_name']} ({g['guild_id']})")
        for ch in sorted(g["channels"], key=lambda x: x["name"]):
            print(f'  - {{ id: {ch["id"]}, name: "{ch["name"]}", mode: write }}  # {ch["type"]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
