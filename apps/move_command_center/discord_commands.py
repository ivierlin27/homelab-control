"""Discord slash commands, forum hooks, and text formatters."""

from __future__ import annotations

import os
import asyncio
from typing import TYPE_CHECKING, Any

from . import api_client

try:
    from . import discord_forum
except ImportError:
    discord_forum = None  # type: ignore[assignment]

if TYPE_CHECKING:
    import discord
    from discord import app_commands

DASHBOARD_URL = os.environ.get("MOVE_DASHBOARD_URL", "https://move.dev-path.org")
FORUM_CHANNEL_IDS = {
    x.strip()
    for x in os.environ.get("MOVE_FORUM_CHANNEL_IDS", "1516955162974097430").split(",")
    if x.strip()
}


def forum_thread_url(thread: Any) -> str:
    guild_id = thread.guild.id if thread.guild else 0
    return f"https://discord.com/channels/{guild_id}/{thread.id}"


async def fetch_thread_text(channel: Any, limit: int = 100) -> str:
    lines: list[str] = []
    async for msg in channel.history(limit=limit, oldest_first=True):
        if msg.author.bot:
            continue
        author = getattr(msg.author, "display_name", str(msg.author))
        content = (msg.content or "").strip()
        if content:
            lines.append(f"{author}: {content}")
    return "\n".join(lines)


def format_status(data: dict) -> str:
    lines = ["**Move command center**", ""]
    lines.append("**Critical dates**")
    for m in data.get("milestones", []):
        days = m.get("days_until", "?")
        lines.append(f"- {m['label']}: {m['date']} ({days}d)")
    lines.append("")
    lines.append("**Next actions**")
    tasks = data.get("next_tasks") or []
    if tasks:
        for t in tasks[:3]:
            due = f", due {t['due_date']}" if t.get("due_date") else ""
            lines.append(f"- {t['title']} ({t['workstream']}{due})")
    else:
        lines.append("- None")
    waiting = data.get("waiting") or []
    lines.append("")
    lines.append(f"**Waiting:** {len(waiting)}")
    risks = data.get("risks") or []
    if risks:
        lines.append("")
        lines.append("**High risks**")
        for r in risks[:4]:
            lines.append(f"- [{r['severity']}] {r['title']}")
    lines.append("")
    lines.append(f"Dashboard: {DASHBOARD_URL}")
    return "\n".join(lines)


def format_today(data: dict) -> str:
    lines = [f"**Today ({data.get('date', '')})**", ""]
    overdue = data.get("overdue_count", 0)
    if overdue:
        lines.append(f"Overdue: {overdue}")
        lines.append("")
    tasks = data.get("tasks") or []
    if not tasks:
        lines.append("No tasks due today or overdue.")
    else:
        for t in tasks:
            due = f" (due {t['due_date']})" if t.get("due_date") else ""
            lines.append(f"- [{t['priority']}] {t['title']}{due}")
    lines.append("")
    lines.append(f"Dashboard: {DASHBOARD_URL}")
    return "\n".join(lines)


def format_house_status(data: dict) -> str:
    pipe = data.get("pipeline", {})
    by_status = pipe.get("by_status") or {}
    lines = ["**House search pipeline**", ""]
    if not by_status:
        lines.append("No properties indexed yet. Use `/house add` in a forum post.")
    else:
        for status, count in sorted(by_status.items()):
            lines.append(f"- {status}: {count}")
        lines.append("")
        lines.append(f"Total: {pipe.get('total', 0)}")
    recent = data.get("recent") or []
    if recent:
        lines.append("")
        lines.append("**Recent**")
        for p in recent[:5]:
            lines.append(f"- {p['address_normalized']} ({p['status']})")
    links = data.get("external_links") or {}
    if links.get("google_sheet"):
        lines.append("")
        lines.append(f"Sheet: {links['google_sheet']}")
    lines.append(f"Dashboard: {DASHBOARD_URL}")
    return "\n".join(lines)


def format_digest(data: dict) -> str:
    text = data.get("text")
    if text:
        return text
    return format_status(data)


def _interaction_forum_url(interaction: "discord.Interaction") -> str | None:
    ch = interaction.channel
    if ch is None:
        return None
    import discord

    if isinstance(ch, discord.Thread):
        return forum_thread_url(ch)
    return None


def _interaction_default_address(interaction: "discord.Interaction") -> str | None:
    ch = interaction.channel
    if ch is None:
        return None
    import discord

    if isinstance(ch, discord.Thread) and ch.name:
        return ch.name
    return None


def _address_match_key(address: str) -> str:
    """Mirror app/properties.address_match_key for forum hook skip logic."""
    suffixes = {
        "st", "street", "rd", "road", "dr", "drive", "pl", "place",
        "ave", "avenue", "blvd", "crt", "court", "terr", "terrace", "way", "ln", "lane",
    }
    parts = " ".join(address.strip().split()).lower().split()
    if not parts:
        return ""
    if not parts[0].isdigit():
        return " ".join(parts[:2]) if len(parts) >= 2 else parts[0]
    end = len(parts)
    if end > 2 and parts[-1] in suffixes:
        end -= 1
    return " ".join(parts[: min(end, 4)])


async def on_forum_thread_create(thread: Any) -> None:
    """Auto-create property stub when a new forum post appears in #coquitlam-houses."""
    if str(thread.parent_id) not in FORUM_CHANNEL_IDS:
        return
    if not os.environ.get("MOVE_COMMAND_CENTER_URL"):
        return
    forum_url = forum_thread_url(thread)
    title = (thread.name or "").strip()
    title_key = _address_match_key(title) if title else ""
    try:
        for prop in api_client.list_properties():
            if prop.get("discord_forum_url") == forum_url:
                break
            prop_addr = (prop.get("address_normalized") or "").strip()
            if title and prop_addr.lower() == title.lower():
                break
            if title_key and _address_match_key(prop_addr) == title_key:
                break
        else:
            await asyncio.sleep(1.5)
            for prop in api_client.list_properties():
                if prop.get("discord_forum_url") == forum_url:
                    break
                prop_addr = (prop.get("address_normalized") or "").strip()
                if title and prop_addr.lower() == title.lower():
                    break
                if title_key and _address_match_key(prop_addr) == title_key:
                    break
            else:
                api_client.upsert_property(
                    address=thread.name or "Untitled property",
                    forum_url=forum_url,
                    status="discovered",
                )
    except Exception as exc:  # noqa: BLE001
        print(f"move_command_center: forum auto-add failed: {exc}")
    try:
        from . import discord_forum as move_forum

        await move_forum.sync_forum_tags_to_board(thread)
    except Exception as exc:  # noqa: BLE001
        print(f"move_command_center: forum tag sync on create failed: {exc}")


def register_slash_commands(tree: "app_commands.CommandTree") -> None:
    import discord
    from discord import app_commands

    move = app_commands.Group(name="move", description="Move command center")
    house = app_commands.Group(name="house", description="House buying")

    @move.command(name="status", description="Critical dates, next actions, risks")
    async def move_status(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            await interaction.followup.send(format_status(api_client.get_status()), ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(f"Move API error: {exc}", ephemeral=True)

    @move.command(name="today", description="Tasks due today or overdue")
    async def move_today(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            await interaction.followup.send(format_today(api_client.get_today()), ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(f"Move API error: {exc}", ephemeral=True)

    @move.command(name="digest", description="Full daily digest (same as scheduled morning post)")
    async def move_digest(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            await interaction.followup.send(format_digest(api_client.get_digest()), ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(f"Move API error: {exc}", ephemeral=True)

    @move.command(name="add-task", description="Add a move/sell/buy task")
    @app_commands.describe(
        title="Task description",
        workstream="sell, move, temp_housing, or buy",
        due="Due date YYYY-MM-DD",
        priority="critical, high, normal, or low",
    )
    @app_commands.choices(
        workstream=[
            app_commands.Choice(name="sell", value="sell"),
            app_commands.Choice(name="move", value="move"),
            app_commands.Choice(name="temp_housing", value="temp_housing"),
            app_commands.Choice(name="buy", value="buy"),
        ],
        priority=[
            app_commands.Choice(name="critical", value="critical"),
            app_commands.Choice(name="high", value="high"),
            app_commands.Choice(name="normal", value="normal"),
            app_commands.Choice(name="low", value="low"),
        ],
    )
    async def move_add_task(
        interaction: discord.Interaction,
        title: str,
        workstream: str = "move",
        due: str | None = None,
        priority: str = "normal",
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            task = api_client.create_task(
                title=title,
                workstream=workstream,
                due_date=due,
                priority=priority,
            )
            due_note = f" due {task['due_date']}" if task.get("due_date") else ""
            await interaction.followup.send(
                f"Added: **{task['title']}** ({task['workstream']}{due_note})",
                ephemeral=True,
            )
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(f"Move API error: {exc}", ephemeral=True)

    @house.command(name="intake", description="Listing URL → sheet row + forum post + dashboard")
    @app_commands.describe(
        listing_url="Redfin (or other) listing URL",
        mls="MLS number if known (optional; add later via sheet if missing)",
        notes="Optional notes for forum post / sheet",
    )
    async def house_intake(
        interaction: discord.Interaction,
        listing_url: str,
        mls: str | None = None,
        notes: str | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            if discord_forum is None:
                await interaction.followup.send("Forum module not available.", ephemeral=True)
                return
            data = await asyncio.to_thread(
                api_client.intake_from_listing,
                listing_url=listing_url.strip(),
                mls=mls,
                notes=notes,
            )
            prop = data["property"]
            forum_url = prop.get("discord_forum_url") or data.get("forum_url")
            if not forum_url and discord_forum is not None:
                thread = await discord_forum.create_forum_post(
                    interaction.client,
                    title=data["forum_title"],
                    body=data["forum_post_body"],
                    image_url=(data.get("parsed") or {}).get("og_image"),
                )
                forum_url = discord_forum.forum_url_from_thread(thread)
                prop = await asyncio.to_thread(
                    api_client.patch_property,
                    prop["id"],
                    forum_url=forum_url,
                )
            lines = [
                f"**Intake complete:** {prop['address_normalized']}",
            ]
            if forum_url:
                lines.append(f"Forum: {forum_url}")
            elif data.get("forum_error"):
                lines.append(f"Forum: _{data['forum_error']}_")
            if prop.get("sheet_row_url"):
                lines.append(f"Sheet: {prop['sheet_row_url']}")
            elif data.get("sheet", {}).get("error"):
                lines.append(f"Sheet: _{data['sheet']['error']}_")
            if data.get("parsed", {}).get("parse_method") == "redfin_url":
                lines.append("_Parsed address from URL — verify price/beds in sheet._")
            lines.append(f"Dashboard: {DASHBOARD_URL}")
            await interaction.followup.send("\n".join(lines), ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(f"Intake error: {exc}", ephemeral=True)

    @house.command(name="add", description="Link a property to forum post, sheet, listing")
    @app_commands.describe(
        address="Address or label (defaults to forum post title if in thread)",
        listing_url="Realtor/MLS link",
        sheet_url="Google Sheet row link",
        forum_url="Discord forum thread URL (auto if run in thread)",
    )
    async def house_add(
        interaction: discord.Interaction,
        address: str | None = None,
        listing_url: str | None = None,
        sheet_url: str | None = None,
        forum_url: str | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            resolved_address = address or _interaction_default_address(interaction)
            if not resolved_address:
                await interaction.followup.send(
                    "Provide an `address` or run this inside a forum thread.",
                    ephemeral=True,
                )
                return
            resolved_forum = forum_url or _interaction_forum_url(interaction)
            prop = api_client.upsert_property(
                address=resolved_address,
                listing_url=listing_url,
                sheet_url=sheet_url,
                forum_url=resolved_forum,
            )
            lines = [
                f"Indexed **{prop['address_normalized']}** ({prop['status']})",
                f"ID: `{prop['id'][:8]}…`",
            ]
            if prop.get("discord_forum_url"):
                lines.append(f"Forum: {prop['discord_forum_url']}")
            if prop.get("sheet_row_url"):
                lines.append(f"Sheet: {prop['sheet_row_url']}")
            await interaction.followup.send("\n".join(lines), ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(f"House API error: {exc}", ephemeral=True)

    @house.command(name="status", description="Property pipeline counts")
    async def house_status_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            await interaction.followup.send(format_house_status(api_client.get_house_status()), ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(f"House API error: {exc}", ephemeral=True)

    @house.command(name="summarize", description="Summarize forum thread vs buy criteria")
    @app_commands.describe(
        address="Property address/label (defaults to thread title if in forum post)",
    )
    async def house_summarize(
        interaction: discord.Interaction,
        address: str | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            ch = interaction.channel
            if ch is None:
                await interaction.followup.send("No channel context.", ephemeral=True)
                return
            import discord

            if not isinstance(ch, discord.Thread):
                await interaction.followup.send(
                    "Run `/house summarize` inside a forum thread, or use `/house add` first.",
                    ephemeral=True,
                )
                return
            resolved_address = address or ch.name
            forum_url = forum_thread_url(ch)
            prop = api_client.upsert_property(address=resolved_address, forum_url=forum_url)
            thread_text = await fetch_thread_text(ch)
            if not thread_text.strip():
                await interaction.followup.send("No messages found in this thread yet.", ephemeral=True)
                return
            updated = api_client.summarize_property(prop["id"], thread_text=thread_text)
            summary = updated.get("ai_summary") or "No summary generated."
            if len(summary) > 1900:
                summary = summary[:1900] + "…"
            await interaction.followup.send(summary, ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(f"Summarize error: {exc}", ephemeral=True)

    @house.command(name="compare", description="Compare 2–4 properties vs buy criteria (LLM)")
    @app_commands.describe(
        addresses="Comma-separated address fragments (2–4), e.g. Scott Creek, Lincoln",
    )
    async def house_compare(
        interaction: discord.Interaction,
        addresses: str,
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            parts = [p.strip() for p in addresses.split(",") if p.strip()]
            if len(parts) < 2:
                await interaction.followup.send(
                    "Provide at least 2 comma-separated address fragments.",
                    ephemeral=True,
                )
                return
            data = await asyncio.to_thread(api_client.compare_properties, addresses=parts)
            comparison = data.get("comparison") or "No comparison returned."
            if len(comparison) > 1900:
                comparison = comparison[:1900] + "…"
            props = ", ".join(p["address_normalized"] for p in data.get("properties", []))
            await interaction.followup.send(
                f"**Compare:** {props}\n\n{comparison}",
                ephemeral=True,
            )
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(f"Compare error: {exc}", ephemeral=True)

    guild_ids = [
        int(x.strip())
        for x in os.environ.get("DISCORD_ALLOWED_GUILD_IDS", "539859908691099672").split(",")
        if x.strip()
    ]
    if guild_ids:
        guild = discord.Object(id=guild_ids[0])
        tree.add_command(move, guild=guild)
        tree.add_command(house, guild=guild)
    else:
        tree.add_command(move)
        tree.add_command(house)
    names = [c.qualified_name for g in (move, house) for c in g.walk_commands()]
    print(f"move_command_center: registered slash commands: {names}")


def handle_text_command(content: str) -> str | None:
    stripped = content.strip()
    if stripped.startswith("/"):
        stripped = stripped[1:].strip()
    lowered = stripped.lower()
    content = stripped
    if lowered in {"move status", "move"}:
        return format_status(api_client.get_status())
    if lowered == "move today":
        return format_today(api_client.get_today())
    if lowered in {"move digest", "move daily"}:
        return format_digest(api_client.get_digest())
    if lowered.startswith("move add-task "):
        title = content.strip()[len("move add-task ") :].strip()
        if not title:
            return "Usage: `move add-task <title>`"
        task = api_client.create_task(title=title)
        return f"Added: **{task['title']}** ({task['workstream']})"
    if lowered in {"house status", "move house status"}:
        return format_house_status(api_client.get_house_status())
    if lowered.startswith("house intake "):
        url = content.strip()[len("house intake ") :].strip()
        if not url:
            return "Usage: `house intake <listing_url>`"
        return "Use `/house intake` for full automation (sheet + forum)."
    if lowered.startswith("house add "):
        address = content.strip()[len("house add ") :].strip()
        if not address:
            return "Usage: `house add <address>`"
        prop = api_client.upsert_property(address=address)
        return f"Indexed **{prop['address_normalized']}** ({prop['status']})"
    return None
