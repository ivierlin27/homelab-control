#!/usr/bin/env python3
"""Discord bridge for agent:homelab (Phase 0.7).

The author/homelab agent picks up tasks from its queue and drafts changes.
Like the maintainer bridge, this exposes ``help``/``status`` and acks free-
form text into the audit ledger; real intake will land via A2A in 0.9.
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


DEFAULT_STATE_DIR = Path.home() / ".local/state/homelab-control/agent-homelab"
DEFAULT_COMMAND_PREFIX = "!homelab"


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
                    "homelab (author) commands:",
                    f"- `{command_prefix} help`   — this message",
                    f"- `{command_prefix} status` — queue counts (inbox/processing/done/failed)",
                    "Free-form text is logged to the audit ledger; A2A intake lands in Phase 0.9.",
                ]
            )
        if lowered in {"status", "/status"}:
            counts = _queue_counts(state_dir)
            return f"homelab queue: {counts or '(empty queue dirs)'}"
        return (
            f"ack ({len(ctx.content)} chars). I'm a project agent — try"
            f" `{command_prefix} help` or wait for Phase 0.9 (A2A)."
        )

    return handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default=os.environ.get("HOMELAB_STATE_DIR", str(DEFAULT_STATE_DIR)))
    args = parser.parse_args()

    state_dir = Path(args.state_dir).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True)
    command_prefix = os.environ.get("DISCORD_COMMAND_PREFIX", DEFAULT_COMMAND_PREFIX)

    config = BridgeConfig(
        principal="agent:homelab",
        bot_label="homelab",
        command_prefix=command_prefix,
        handler=_build_handler(state_dir=state_dir, command_prefix=command_prefix),
        audit_log_path=state_dir / "trust-ledger.jsonl",
    )
    return asyncio.run(run_bridge(config))


if __name__ == "__main__":
    raise SystemExit(main())
