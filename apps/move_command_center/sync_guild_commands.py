#!/usr/bin/env python3
"""Push move/house slash commands to Discord via REST (guild-scoped).

discord.py tree.sync() was returning 0 without registering commands; this script
uses the bulk guild command PUT endpoint directly.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

GUILD_ID = os.environ.get("DISCORD_ALLOWED_GUILD_IDS", "539859908691099672").split(",")[0].strip()

COMMANDS = [
    {
        "name": "move",
        "description": "Move command center",
        "type": 1,
        "options": [
            {
                "type": 1,
                "name": "status",
                "description": "Critical dates, next actions, risks",
            },
            {
                "type": 1,
                "name": "today",
                "description": "Tasks due today or overdue",
            },
            {
                "type": 1,
                "name": "digest",
                "description": "Full daily digest (milestones, overdue, risks, pipeline)",
            },
            {
                "type": 1,
                "name": "add-task",
                "description": "Add a move/sell/buy task",
                "options": [
                    {
                        "type": 3,
                        "name": "title",
                        "description": "Task description",
                        "required": True,
                    },
                    {
                        "type": 3,
                        "name": "workstream",
                        "description": "sell, move, temp_housing, or buy",
                        "required": False,
                        "choices": [
                            {"name": "sell", "value": "sell"},
                            {"name": "move", "value": "move"},
                            {"name": "temp_housing", "value": "temp_housing"},
                            {"name": "buy", "value": "buy"},
                        ],
                    },
                    {
                        "type": 3,
                        "name": "due",
                        "description": "Due date YYYY-MM-DD",
                        "required": False,
                    },
                    {
                        "type": 3,
                        "name": "priority",
                        "description": "critical, high, normal, or low",
                        "required": False,
                        "choices": [
                            {"name": "critical", "value": "critical"},
                            {"name": "high", "value": "high"},
                            {"name": "normal", "value": "normal"},
                            {"name": "low", "value": "low"},
                        ],
                    },
                ],
            },
        ],
    },
    {
        "name": "house",
        "description": "House buying",
        "type": 1,
        "options": [
            {
                "type": 1,
                "name": "intake",
                "description": "Listing URL → sheet row + forum post + dashboard",
                "options": [
                    {
                        "type": 3,
                        "name": "listing_url",
                        "description": "Redfin listing URL",
                        "required": True,
                    },
                    {
                        "type": 3,
                        "name": "mls",
                        "description": "MLS number if known",
                        "required": False,
                    },
                    {
                        "type": 3,
                        "name": "notes",
                        "description": "Notes for forum post",
                        "required": False,
                    },
                ],
            },
            {
                "type": 1,
                "name": "add",
                "description": "Link a property to forum post, sheet, listing",
                "options": [
                    {
                        "type": 3,
                        "name": "address",
                        "description": "Address or label (defaults to forum post title in thread)",
                        "required": False,
                    },
                    {
                        "type": 3,
                        "name": "listing_url",
                        "description": "Realtor/MLS link",
                        "required": False,
                    },
                    {
                        "type": 3,
                        "name": "sheet_url",
                        "description": "Google Sheet row link",
                        "required": False,
                    },
                    {
                        "type": 3,
                        "name": "forum_url",
                        "description": "Discord forum thread URL (auto if run in thread)",
                        "required": False,
                    },
                ],
            },
            {
                "type": 1,
                "name": "status",
                "description": "Property pipeline counts",
            },
            {
                "type": 1,
                "name": "summarize",
                "description": "Summarize forum thread vs buy criteria",
                "options": [
                    {
                        "type": 3,
                        "name": "address",
                        "description": "Property address/label (defaults to thread title)",
                        "required": False,
                    },
                ],
            },
            {
                "type": 1,
                "name": "compare",
                "description": "Compare 2–4 properties vs buy criteria (LLM)",
                "options": [
                    {
                        "type": 3,
                        "name": "addresses",
                        "description": "Comma-separated address fragments, e.g. Scott Creek, Lincoln",
                        "required": True,
                    },
                ],
            },
        ],
    },
]


def main() -> int:
    token = os.environ.get("DISCORD_BOT_TOKEN") or os.environ.get("DISCORD_TOKEN")
    if not token:
        print("DISCORD_BOT_TOKEN required", file=sys.stderr)
        return 1

    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "move-command-center-sync (homelab)",
    }
    req = urllib.request.Request(
        "https://discord.com/api/v10/users/@me",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        app_id = json.load(resp)["id"]

    url = f"https://discord.com/api/v10/applications/{app_id}/guilds/{GUILD_ID}/commands"
    body = json.dumps(COMMANDS).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            **headers,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            registered = json.load(resp)
    except urllib.error.HTTPError as exc:
        print(exc.read().decode(), file=sys.stderr)
        return 1

    names = [c.get("name") for c in registered]
    print(f"Registered {len(registered)} guild command group(s): {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
