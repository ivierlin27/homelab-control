"""Tier 3 human escalation via Discord (#approvals + optional urgent DM)."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Mapping, Sequence
from urllib import error, request

from apps._shared.audit import AuditLog

from .dispatcher import Tier3Fn

PostFn = Callable[[str, str], dict[str, Any]]
DmFn = Callable[[str, str, str], dict[str, Any]]


class Tier3DiscordError(RuntimeError):
    """Discord Tier 3 delivery failed."""


def _discord_request(
    token: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        f"https://discord.com/api/v10{path}",
        data=body,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "homelab-control-escalation (https://github.com/sailpoint/homelab-control)",
        },
        method=method,
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise Tier3DiscordError(f"Discord API {method} {path} failed: {exc.code} {detail}") from exc
    return json.loads(raw) if raw else {}


def post_channel_message(token: str, channel_id: str, content: str) -> dict[str, Any]:
    """Post *content* to a channel; return the Discord message object."""
    return _discord_request(
        token,
        "POST",
        f"/channels/{channel_id}/messages",
        payload={"content": content[:2000]},
    )


def post_webhook_message(webhook_url: str, content: str) -> dict[str, Any]:
    """Post via incoming webhook (no bot token required)."""
    body = json.dumps({"content": content[:2000]}).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise Tier3DiscordError(f"Discord webhook failed: {exc.code} {detail}") from exc
    return json.loads(raw) if raw else {"posted": True}


def send_user_dm(token: str, user_id: str, content: str) -> dict[str, Any]:
    """Open (or reuse) a DM channel and post *content*."""
    channel = _discord_request(
        token,
        "POST",
        "/users/@me/channels",
        payload={"recipient_id": str(user_id)},
    )
    channel_id = str(channel.get("id", ""))
    if not channel_id:
        raise Tier3DiscordError("Discord did not return a DM channel id")
    message = post_channel_message(token, channel_id, content)
    return {"channel_id": channel_id, "message": message}


def resolve_approvals_channel_id(
    principal: str,
    *,
    channel_name: str = "#approvals",
    registry: Any | None = None,
) -> str:
    """Resolve the snowflake channel id for *channel_name* from the agent manifest."""
    explicit = os.environ.get("ESCALATION_APPROVALS_CHANNEL_ID", "").strip()
    if explicit:
        return explicit

    if registry is None:
        from apps._shared.registry import load_registry

        registry = load_registry()

    manifest = registry.get(principal)
    for channel in manifest.get("discord", "channels", default=[]) or []:
        if not isinstance(channel, dict):
            continue
        if str(channel.get("name", "")) != channel_name:
            continue
        channel_id = channel.get("id")
        if channel_id:
            return str(channel_id)

    raise Tier3DiscordError(
        f"No Discord channel id for {channel_name!r} on {principal}; "
        "set discord.channels[].id in the manifest or ESCALATION_APPROVALS_CHANNEL_ID"
    )


def resolve_dm_user_ids(
    *,
    explicit: Sequence[str] | None = None,
) -> list[str]:
    """User ids to DM when ``urgent`` is set on the escalation envelope."""
    if explicit:
        return [str(item) for item in explicit if str(item).strip()]
    env = os.environ.get("ESCALATION_DM_USER_IDS", "").strip()
    if env:
        return [item.strip() for item in env.split(",") if item.strip()]
    fallback = os.environ.get("DISCORD_ALLOWED_USER_IDS", "").strip()
    if fallback:
        return [item.strip() for item in fallback.split(",") if item.strip()]
    return []


def format_tier3_message(envelope: Mapping[str, Any]) -> str:
    """Render the #approvals post body from a Tier 3 escalation envelope."""
    task_class = envelope.get("task_class", "unknown")
    urgent = bool(envelope.get("urgent", False))
    blocked = str(envelope.get("blocked_reason") or "(none)")
    lines = [
        f"**Tier 3 escalation** — `{task_class}`",
        f"**Urgent:** {urgent}",
        "",
        "**Blocked reason**",
        blocked,
        "",
        "**Tier transitions**",
    ]
    transitions = envelope.get("transitions") or []
    if not transitions:
        lines.append("(none)")
    else:
        for index, transition in enumerate(transitions, start=1):
            if not isinstance(transition, Mapping):
                lines.append(f"{index}. {transition!r}")
                continue
            lines.append(
                f"{index}. {transition.get('from_tier')}→{transition.get('to_tier')}: "
                f"{transition.get('reason', '')}"
            )
    card_url = envelope.get("card_url") or envelope.get("planka_card_url")
    if card_url:
        lines.extend(["", f"**Card:** {card_url}"])
    lines.append("")
    lines.append("Reply in-thread when acknowledged.")
    return "\n".join(lines)


def make_tier3_discord_handler(
    *,
    principal: str = "agent:executive",
    channel_name: str = "#approvals",
    token: str | None = None,
    channel_id: str | None = None,
    webhook_url: str | None = None,
    dm_user_ids: Sequence[str] | None = None,
    post_channel: PostFn | None = None,
    send_dm: DmFn | None = None,
    audit: AuditLog | None = None,
    registry: Any | None = None,
) -> Tier3Fn:
    """Factory for a :class:`Dispatcher` Tier-3 handler that posts to Discord.

    Uses ``ESCALATION_TIER3_DISCORD_WEBHOOK`` when set; otherwise posts with the
    executive bot token to the manifest ``#approvals`` channel. When
    ``envelope['urgent']`` is true, also DMs users from ``ESCALATION_DM_USER_IDS``
    (or ``DISCORD_ALLOWED_USER_IDS``).
    """

    def tier3_discord(envelope: Mapping[str, Any]) -> tuple[str, Any]:
        content = format_tier3_message(envelope)
        webhook = webhook_url or os.environ.get("ESCALATION_TIER3_DISCORD_WEBHOOK", "").strip()
        bot_token = token or os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        resolved_channel = channel_id
        if not resolved_channel and not webhook:
            resolved_channel = resolve_approvals_channel_id(
                principal,
                channel_name=channel_name,
                registry=registry,
            )

        message: dict[str, Any]
        delivery = "webhook" if webhook else "bot"
        try:
            if webhook:
                message = post_webhook_message(webhook, content)
            else:
                if not bot_token or bot_token == "replace-me":
                    raise Tier3DiscordError("DISCORD_BOT_TOKEN is not configured for Tier 3")
                assert resolved_channel is not None
                poster = post_channel or post_channel_message
                message = poster(bot_token, resolved_channel, content)
        except Exception as exc:
            if audit is not None:
                audit.append(
                    {
                        "event": "tier3_discord_failed",
                        "principal": principal,
                        "task_class": envelope.get("task_class"),
                        "error": str(exc),
                    }
                )
            raise

        dm_results: list[dict[str, Any]] = []
        if bool(envelope.get("urgent")):
            if not bot_token or bot_token == "replace-me":
                dm_results.append({"error": "DISCORD_BOT_TOKEN required for urgent DM"})
            else:
                dm_fn = send_dm or send_user_dm
                for user_id in resolve_dm_user_ids(explicit=dm_user_ids):
                    try:
                        dm_results.append(dm_fn(bot_token, user_id, content))
                    except Exception as exc:  # noqa: BLE001
                        dm_results.append({"user_id": user_id, "error": str(exc)})

        payload = {
            "delivery": delivery,
            "channel_id": resolved_channel,
            "message_id": str(message.get("id", "")),
            "dm_results": dm_results,
        }
        if audit is not None:
            audit.append(
                {
                    "event": "tier3_discord",
                    "principal": principal,
                    "task_class": envelope.get("task_class"),
                    "urgent": bool(envelope.get("urgent")),
                    **payload,
                }
            )
        return ("human_intervention", payload)

    return tier3_discord
