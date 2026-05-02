#!/usr/bin/env python3
"""Shared chat orchestration for local web and Discord executive assistant surfaces."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import main as executive
from conversation_store import ConversationStore


def csv_env(name: str) -> set[str]:
    raw = os.environ.get(name, "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def source_allowed(policy: dict[str, Any], *, source: str, metadata: dict[str, Any]) -> tuple[bool, str]:
    sources = policy.get("interaction_sources", {})
    if not sources:
        return True, "no source policy configured"
    cfg = sources.get(source)
    if not cfg or not cfg.get("enabled", False):
        return False, f"source is not enabled: {source}"

    if source in {"discord-dm", "discord-channel"}:
        user_id = str(metadata.get("user_id", ""))
        guild_id = str(metadata.get("guild_id", ""))
        channel_id = str(metadata.get("channel_id", ""))
        allowed_users = set(str(item) for item in cfg.get("allowed_user_ids", [])) | csv_env("DISCORD_ALLOWED_USER_IDS")
        allowed_guilds = set(str(item) for item in cfg.get("allowed_guild_ids", [])) | csv_env("DISCORD_ALLOWED_GUILD_IDS")
        allowed_channels = set(str(item) for item in cfg.get("allowed_channel_ids", [])) | csv_env("DISCORD_ALLOWED_CHANNEL_IDS")
        if allowed_users and user_id not in allowed_users:
            return False, f"discord user is not allowed: {user_id}"
        if source == "discord-channel":
            if allowed_guilds and guild_id not in allowed_guilds:
                return False, f"discord guild is not allowed: {guild_id}"
            if allowed_channels and channel_id not in allowed_channels:
                return False, f"discord channel is not allowed: {channel_id}"

    return True, "source allowed"


def render_assistant_reply(result: dict[str, Any]) -> str:
    decision = result.get("decision", {})
    task_class = result.get("task_class", {})
    routing = result.get("routing", {})
    lines = [
        f"Decision: {decision.get('decision', 'unknown')}",
        f"Reason: {decision.get('reason', 'unknown')}",
    ]
    if task_class:
        lines.append(f"Task class: {task_class.get('task_class', 'unknown')}")
    if routing:
        lines.append(f"Route: {routing.get('route', 'unknown')} ({routing.get('model_tier', 'unknown')})")
    card = result.get("card", {})
    if card.get("created"):
        lines.append(f"Planka card: {card.get('url') or card.get('card_id')}")
    elif decision.get("can_create_card"):
        lines.append("Planka card: not created in this mode.")
    memory = result.get("memory", {})
    if memory.get("posted"):
        lines.append("Memory: written.")
    elif memory.get("reason"):
        lines.append(f"Memory: {memory['reason']}")
    return "\n".join(lines)


def handle_chat_turn(
    *,
    store: ConversationStore,
    conversation: dict[str, Any],
    message: str,
    source: str,
    source_ref: str,
    source_user: str,
    metadata: dict[str, Any],
    state_dir: Path,
    policy_path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    policy = executive.load_yaml(policy_path)
    allowed, reason = source_allowed(policy, source=source, metadata=metadata)
    store.add_turn(conversation["id"], role="user", content=message, result={"source": source, "source_ref": source_ref})
    if not allowed:
        result = {
            "ok": False,
            "title": message[:80],
            "decision": {
                "decision": "blocked",
                "reason": reason,
                "labels": ["shield-blocked"],
                "shield": {"ok": False, "reason": reason, "category": "source_policy"},
            },
            "card": {"created": False},
            "memory": {"posted": False, "reason": "source policy blocked request"},
        }
        executive.append_jsonl(
            state_dir / "trust-ledger.jsonl",
            {
                "event": "request-evaluated",
                "occurred_at": executive.utc_now(),
                "principal": os.environ.get("AGENT_PRINCIPAL", executive.DEFAULT_PRINCIPAL),
                "title": message[:80],
                "domain": conversation["domain"],
                "task_type": conversation["task_type"],
                "decision": "blocked",
                "reason": reason,
                "labels": ["shield-blocked"],
                "dry_run": dry_run,
                "source": source,
                "source_ref": source_ref,
                "source_user": source_user,
                "conversation_id": conversation["id"],
                "card": {"created": False},
            },
        )
    else:
        args = argparse.Namespace(
            request=message,
            title=message.strip().splitlines()[0][:80] if message.strip() else conversation["title"],
            domain=conversation["domain"],
            task_type=conversation["task_type"],
            label=[],
            plan_ready=conversation["plan_ready"],
            dry_run=dry_run,
            search_memory=conversation["search_memory"],
            write_memory=conversation["write_memory"],
            policy=str(policy_path),
            state_dir=str(state_dir),
            source=source,
            source_ref=source_ref,
            source_user=source_user,
            conversation_id=conversation["id"],
        )
        result = executive.handle_request(args)

    reply = render_assistant_reply(result)
    store.add_turn(conversation["id"], role="assistant", content=reply, result=result)
    return {"reply": reply, "result": result, "conversation": conversation}
