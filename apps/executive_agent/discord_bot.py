#!/usr/bin/env python3
"""Discord bridge for the executive assistant.

Thin per-agent wrapper around :mod:`apps._shared.discord_bridge` (Phase 0.7).
The executive's handler keeps the rich behavior the assistant has had since
its first cut: ``help``/``status``/``weekly-review`` shortcuts plus a fall-
through into :func:`chat_core.handle_chat_turn` for free-form turns. The
shared bridge handles intents, allowlists, audit logging, and reply chunking.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "executive_agent"))

import main as executive  # noqa: E402
from chat_core import handle_chat_turn  # noqa: E402
from conversation_store import ConversationStore  # noqa: E402

from apps._shared.discord_bridge import BridgeConfig, MessageContext, run_bridge  # noqa: E402


DEFAULT_STATE_DIR = Path.home() / ".local/state/homelab-control/agent-executive"
DEFAULT_COMMAND_PREFIX = "!assistant"


def _build_handler(
    *,
    store: ConversationStore,
    state_dir: Path,
    policy_path: Path,
    command_prefix: str,
):
    async def handler(ctx: MessageContext) -> str | None:
        message = ctx.message
        metadata = {
            "user_id": ctx.user_id,
            "channel_id": ctx.channel_id,
        }
        if ctx.guild_id is not None:
            metadata["guild_id"] = ctx.guild_id

        content = ctx.content if ctx.content else "status"

        conversation = store.conversation_for_source(
            source=ctx.source,
            source_ref=ctx.source_ref,
            default_title=f"Discord {ctx.source_ref}",
            owner=ctx.user_id,
            domain=os.environ.get("DISCORD_DEFAULT_DOMAIN", "homelab"),
            task_type=os.environ.get("DISCORD_DEFAULT_TASK_TYPE", "research"),
            plan_ready=os.environ.get("DISCORD_PLAN_READY", "").lower() in {"1", "true", "yes"},
            write_memory=os.environ.get("DISCORD_WRITE_MEMORY", "").lower() in {"1", "true", "yes"},
            search_memory=os.environ.get("DISCORD_SEARCH_MEMORY", "true").lower() in {"1", "true", "yes"},
            metadata=metadata,
        )

        lowered = content.lower().strip()
        if lowered in {"help", "/help"}:
            return "\n".join(
                [
                    "Executive assistant commands:",
                    f"- `{command_prefix} help`",
                    f"- `{command_prefix} status`",
                    f"- `{command_prefix} weekly-review`",
                    "- Mention me or DM me with a normal request to create/delegate work.",
                ]
            )
        if lowered in {"status", "/status"}:
            status = executive.queue_status(state_dir)
            return f"Executive queue: {status['counts']}; failed jobs: {len(status['failed_jobs'])}"
        if lowered in {"weekly-review", "/weekly-review"}:
            review = executive.weekly_review(state_dir)
            return "\n".join(review.get("summary", []))

        result = await asyncio.to_thread(
            handle_chat_turn,
            store=store,
            conversation=conversation,
            message=content,
            source=ctx.source,
            source_ref=ctx.source_ref,
            source_user=ctx.user_id,
            metadata=metadata,
            state_dir=state_dir,
            policy_path=policy_path,
            dry_run=os.environ.get("DISCORD_DRY_RUN", "true").lower() in {"1", "true", "yes"},
        )
        return result["reply"]

    return handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default=os.environ.get("EXECUTIVE_STATE_DIR", str(DEFAULT_STATE_DIR)))
    parser.add_argument("--db", default=os.environ.get("EXECUTIVE_CHAT_DB", ""))
    parser.add_argument("--policy", default=os.environ.get("EXECUTIVE_POLICY", str(executive.DEFAULT_POLICY)))
    args = parser.parse_args()

    state_dir = Path(args.state_dir).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db).expanduser() if args.db else state_dir / "conversations.sqlite3"
    store = ConversationStore(db_path)
    policy_path = Path(args.policy).expanduser()
    command_prefix = os.environ.get("DISCORD_COMMAND_PREFIX", DEFAULT_COMMAND_PREFIX)

    handler = _build_handler(
        store=store,
        state_dir=state_dir,
        policy_path=policy_path,
        command_prefix=command_prefix,
    )

    config = BridgeConfig(
        principal="agent:executive",
        bot_label="executive",
        command_prefix=command_prefix,
        handler=handler,
        audit_log_path=state_dir / "trust-ledger.jsonl",
    )
    return asyncio.run(run_bridge(config))


if __name__ == "__main__":
    raise SystemExit(main())
