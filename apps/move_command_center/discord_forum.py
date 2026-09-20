"""Discord forum helpers and emoji → pipeline status sync."""

from __future__ import annotations

import os
from typing import Any

# Kevin's forum emoji vocabulary (override via MOVE_STATUS_EMOJI_JSON env)
DEFAULT_EMOJI_STATUS: dict[str, str] = {
    "\U0001f6ab": "rejected",      # 🚫 no / reject before visit
    "\u274c": "rejected",            # ❌
    "\u2b55": "rejected",            # ⭕ don't circle / pass
    "\U0001f44c": "reviewing",       # 👌 OK / maybe
    "\U0001f197": "reviewing",       # 🆗
    "\U0001f914": "reviewing",       # 🤔
    "\u2705": "shortlisted",         # ✅ checkbox / actively considering
    "\u2611\ufe0f": "shortlisted",   # ☑️
    "\U0001f4cb": "reviewing",       # 📋 considering
}

FORUM_CHANNEL_IDS = {
    x.strip()
    for x in os.environ.get("MOVE_FORUM_CHANNEL_IDS", "1516955162974097430").split(",")
    if x.strip()
}
DEFAULT_FORUM_TAG_IDS = [
    x.strip()
    for x in os.environ.get(
        "MOVE_FORUM_DEFAULT_TAG_IDS",
        "1518737036863995944",
    ).split(",")
    if x.strip()
]

# Forum tag snowflake → board pipeline status (override via MOVE_FORUM_TAG_STATUS_JSON)
DEFAULT_TAG_STATUS: dict[str, str | None] = {
    "1518736720701554760": "shortlisted",   # Yes/Pursue ✅
    "1518736986414776476": "rejected",      # No/Discard ❌
    "1518737036863995944": None,            # Pending information ❔ — default, no status change
    "1518737088709791924": "backup",        # OK/Keep as backup 🆗
    "1518737119814619257": "reviewing",     # Want to see 👀
    "1518741543115034734": None,            # Over-Budget 💰 — informational only
}

STATUS_RANK: dict[str, int] = {
    "discovered": 1,
    "backup": 2,
    "reviewing": 3,
    "visit_scheduled": 4,
    "visited": 5,
    "shortlisted": 6,
    "offer": 7,
    "rejected": 0,
}

# When multiple decision tags are present, first match wins (reject beats pursue).
TAG_STATUS_PRIORITY: tuple[str, ...] = (
    "rejected",
    "shortlisted",
    "offer",
    "visited",
    "visit_scheduled",
    "reviewing",
    "backup",
    "discovered",
)


def tag_status_map() -> dict[str, str | None]:
    raw = os.environ.get("MOVE_FORUM_TAG_STATUS_JSON", "").strip()
    if not raw:
        return DEFAULT_TAG_STATUS
    import json

    return {str(k): (v if v else None) for k, v in json.loads(raw).items()}


def _tag_ids_from_thread(thread: Any) -> list[str]:
    tags = getattr(thread, "applied_tags", None) or []
    out: list[str] = []
    for tag in tags:
        if isinstance(tag, str):
            out.append(tag)
        else:
            out.append(str(getattr(tag, "id", tag)))
    return out


def status_for_forum_tags(tag_ids: list[str]) -> str | None:
    """Pick board status from applied forum tags (ignores neutral/pending tags)."""
    mapping = tag_status_map()
    candidates: list[str] = []
    for tag_id in tag_ids:
        status = mapping.get(str(tag_id))
        if status:
            candidates.append(status)
    if not candidates:
        return None
    candidate_set = set(candidates)
    for status in TAG_STATUS_PRIORITY:
        if status in candidate_set:
            return status
    return candidates[0]


def emoji_status_map() -> dict[str, str]:
    raw = os.environ.get("MOVE_STATUS_EMOJI_JSON", "").strip()
    if not raw:
        return DEFAULT_EMOJI_STATUS
    import json

    return {str(k): str(v) for k, v in json.loads(raw).items()}


def status_for_emoji(emoji: str) -> str | None:
    if not emoji:
        return None
    return emoji_status_map().get(emoji)


async def create_forum_post(
    client: Any,
    *,
    title: str,
    body: str,
    image_url: str | None = None,
    forum_channel_id: str | None = None,
) -> Any:
    import discord

    channel_id = int(forum_channel_id or next(iter(FORUM_CHANNEL_IDS)))
    channel = client.get_channel(channel_id)
    if channel is None:
        channel = await client.fetch_channel(channel_id)
    if not isinstance(channel, discord.ForumChannel):
        raise RuntimeError(f"channel {channel_id} is not a forum")
    embeds = []
    if image_url and str(image_url).startswith("http"):
        embeds.append(discord.Embed().set_image(url=str(image_url)[:2048]))
    thread_with_msg = await channel.create_thread(
        name=title[:100],
        content=body[:2000],
        embeds=embeds or None,
        auto_archive_duration=10080,
        applied_tags=DEFAULT_FORUM_TAG_IDS[:5] or None,
    )
    return thread_with_msg.thread


def forum_url_from_thread(thread: Any) -> str:
    guild_id = thread.guild.id if thread.guild else 0
    return f"https://discord.com/channels/{guild_id}/{thread.id}"


async def sync_forum_tags_to_board(thread: Any) -> None:
    """Apply forum tag → board status if a decision tag is present."""
    from . import api_client

    import discord

    if not isinstance(thread, discord.Thread):
        return
    if str(thread.parent_id) not in FORUM_CHANNEL_IDS:
        return

    status = status_for_forum_tags(_tag_ids_from_thread(thread))
    if not status:
        return

    forum_url = forum_url_from_thread(thread)
    try:
        result = api_client.set_status_by_forum(forum_url=forum_url, status=status)
    except Exception as exc:  # noqa: BLE001
        print(f"move_command_center: tag sync failed: {exc}")
        return
    print(
        f"move_command_center: forum tags → {status} "
        f"for {result.get('address_normalized', forum_url)}"
    )


async def on_forum_thread_update(before: Any, after: Any) -> None:
    """Sync board + sheet when forum tags change on #coquitlam-houses."""
    import discord

    if not isinstance(after, discord.Thread):
        return
    if str(after.parent_id) not in FORUM_CHANNEL_IDS:
        return

    before_ids = set(_tag_ids_from_thread(before))
    after_ids = set(_tag_ids_from_thread(after))
    if before_ids == after_ids:
        return

    await sync_forum_tags_to_board(after)


async def on_forum_reaction(reaction: Any, user: Any, *, added: bool) -> None:
    """Sync forum starter-post emoji reactions to homelab + sheet status."""
    if not added or user.bot:
        return
    from . import api_client

    channel = reaction.message.channel
    import discord

    if not isinstance(channel, discord.Thread):
        return
    if str(channel.parent_id) not in FORUM_CHANNEL_IDS:
        return

    emoji = reaction.emoji
    if isinstance(emoji, discord.PartialEmoji):
        key = str(emoji)
    else:
        key = str(emoji)

    status = status_for_emoji(key)
    if not status:
        return

    forum_url = forum_url_from_thread(channel)
    try:
        result = api_client.set_status_by_forum(forum_url=forum_url, status=status)
    except Exception as exc:  # noqa: BLE001
        print(f"move_command_center: reaction sync failed: {exc}")
        return
    print(
        f"move_command_center: {user.display_name} {key} → {status} "
        f"for {result.get('address_normalized', forum_url)}"
    )
