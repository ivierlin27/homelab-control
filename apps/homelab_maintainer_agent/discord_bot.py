#!/usr/bin/env python3
"""Discord bridge for agent:homelab-maintainer (Phase 0.7).

The maintainer is a queue worker, not an interactive chat agent. The bridge
exposes ``help``/``status`` shortcuts and acks free-form messages with a
note that A2A (Phase 0.9) is the route for proposing work; arbitrary text
is logged to the agent's audit ledger but not actioned.
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
sys.path.insert(0, str(ROOT / "apps" / "homelab_maintainer_agent"))

import main as maintainer  # noqa: E402

from apps._shared.discord_bridge import BridgeConfig, MessageContext, run_bridge  # noqa: E402


DEFAULT_STATE_DIR = Path.home() / ".local/state/homelab-control/agent-homelab-maintainer"
DEFAULT_COMMAND_PREFIX = "!maintainer"


def _build_handler(*, state_dir: Path, command_prefix: str):
    async def handler(ctx: MessageContext) -> str | None:
        lowered = ctx.content.lower().strip()
        if lowered in {"", "help", "/help"}:
            return "\n".join(
                [
                    "homelab-maintainer commands:",
                    f"- `{command_prefix} help`   — this message",
                    f"- `{command_prefix} status` — queue counts + failed-job count",
                    "Free-form text is logged to the audit ledger but not yet actioned;"
                    " A2A wiring lands in Phase 0.9.",
                ]
            )
        if lowered in {"status", "/status"}:
            status = maintainer.queue_status(state_dir)
            counts = status.get("counts", {})
            failed = status.get("failed_jobs", []) or status.get("failed", [])
            return f"homelab-maintainer queue: {counts}; failed jobs: {len(failed)}"
        return (
            f"ack ({len(ctx.content)} chars). I'm a queue worker — try"
            f" `{command_prefix} help` or wait for Phase 0.9 (A2A)."
        )

    return handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default=os.environ.get("MAINTAINER_STATE_DIR", str(DEFAULT_STATE_DIR)))
    args = parser.parse_args()

    state_dir = Path(args.state_dir).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True)
    command_prefix = os.environ.get("DISCORD_COMMAND_PREFIX", DEFAULT_COMMAND_PREFIX)

    config = BridgeConfig(
        principal="agent:homelab-maintainer",
        bot_label="homelab-maintainer",
        command_prefix=command_prefix,
        handler=_build_handler(state_dir=state_dir, command_prefix=command_prefix),
        audit_log_path=state_dir / "trust-ledger.jsonl",
    )
    return asyncio.run(run_bridge(config))


if __name__ == "__main__":
    raise SystemExit(main())
