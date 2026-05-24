#!/usr/bin/env python3
"""Poll Tier-3 #approvals posts and DM when unacknowledged past policy budget."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps._shared.escalation.tier3_discord import (  # noqa: E402
    resolve_approvals_channel_id,
    resolve_dm_user_ids,
)
from apps._shared.escalation.tier3_pending import process_pending_followups  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--principal",
        default="agent:executive",
        help="manifest principal for #approvals resolution",
    )
    parser.add_argument(
        "--state-dir",
        default=str(Path.home() / ".local/state/homelab-control/escalation"),
    )
    parser.add_argument("--json", action="store_true", help="print action summary as JSON")
    args = parser.parse_args(argv)

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token or token == "replace-me":
        print("DISCORD_BOT_TOKEN not configured", file=sys.stderr)
        return 2

    channel_id = os.environ.get("ESCALATION_APPROVALS_CHANNEL_ID", "").strip()
    if not channel_id:
        channel_id = resolve_approvals_channel_id(args.principal)

    dm_users = resolve_dm_user_ids()
    if not dm_users:
        print("no ESCALATION_DM_USER_IDS or DISCORD_ALLOWED_USER_IDS configured", file=sys.stderr)
        return 2

    results = process_pending_followups(
        token=token,
        dm_user_ids=dm_users,
        state_dir=Path(args.state_dir),
    )
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for item in results:
            print(json.dumps(item, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
