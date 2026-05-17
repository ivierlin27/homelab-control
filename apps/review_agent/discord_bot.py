#!/usr/bin/env python3
"""Discord bridge for agent:review (Phase 0.7).

The review agent picks up PR/proposal artifacts and renders verdicts.
Bridge exposes ``help``/``status`` and acks free-form text; real review
intake is event-driven (PR webhooks, Planka card moves) and A2A in 0.9.
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

from apps._shared.discord_bridge import BridgeConfig, MessageContext, run_bridge  # noqa: E402


DEFAULT_STATE_DIR = Path.home() / ".local/state/homelab-control/agent-review"
DEFAULT_COMMAND_PREFIX = "!review"


def _queue_counts(state_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sub in ("inbox", "processing", "done", "failed"):
        d = state_dir / sub
        if d.is_dir():
            counts[sub] = sum(1 for _ in d.glob("*.json"))
    return counts


def _build_handler(*, state_dir: Path, command_prefix: str):
    async def handler(ctx: MessageContext) -> str | None:
        lowered = ctx.content.lower().strip()
        if lowered in {"", "help", "/help"}:
            return "\n".join(
                [
                    "review commands:",
                    f"- `{command_prefix} help`   — this message",
                    f"- `{command_prefix} status` — queue counts (inbox/processing/done/failed)",
                    "Free-form text is logged to the audit ledger;"
                    " real review intake is event-driven (PRs / Planka) + A2A in Phase 0.9.",
                ]
            )
        if lowered in {"status", "/status"}:
            counts = _queue_counts(state_dir)
            return f"review queue: {counts or '(empty queue dirs)'}"
        return (
            f"ack ({len(ctx.content)} chars). I'm a review worker — try"
            f" `{command_prefix} help` or wait for Phase 0.9 (A2A)."
        )

    return handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default=os.environ.get("REVIEW_STATE_DIR", str(DEFAULT_STATE_DIR)))
    args = parser.parse_args()

    state_dir = Path(args.state_dir).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True)
    command_prefix = os.environ.get("DISCORD_COMMAND_PREFIX", DEFAULT_COMMAND_PREFIX)

    config = BridgeConfig(
        principal="agent:review",
        bot_label="review",
        command_prefix=command_prefix,
        handler=_build_handler(state_dir=state_dir, command_prefix=command_prefix),
        audit_log_path=state_dir / "trust-ledger.jsonl",
    )
    return asyncio.run(run_bridge(config))


if __name__ == "__main__":
    raise SystemExit(main())
