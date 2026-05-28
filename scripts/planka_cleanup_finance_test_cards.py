#!/usr/bin/env python3
"""Delete finance-agent smoke / test cards from the finance Planka board.

Separate from ``planka_cleanup_test_cards.py`` (homelab board). Matches cards
created by finance categorize defer smoke tests and future finance-only flows.

Defaults to **dry-run** (list only). Pass ``--execute`` to delete.

Examples::

  set -a && source ~/.config/homelab-control/agent-finance.env && set +a
  export PYTHONPATH=~/git/homelab-control

  # Preview (needs PLANKA_FINANCE_BOARD_ID in env or --board-id)
  python3 scripts/planka_cleanup_finance_test_cards.py

  # Delete matched cards
  python3 scripts/planka_cleanup_finance_test_cards.py --execute
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps._shared.planka_client import planka_auth_configured, planka_request  # noqa: E402

# Finance-board smoke / test cards (extend as new finance flows add Planka cards).
DEFAULT_FINANCE_TEST_PATTERNS: tuple[str, ...] = (
    r"smoke-finance-defer",
    r"live\.smoke\.finance-planka",
    r"pytest\.live\.finance-planka",
)


def load_env_file(path: Path) -> None:
    """Load KEY=value lines into os.environ (does not unset existing vars)."""
    if not path.is_file():
        raise FileNotFoundError(path)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def compile_patterns(*groups: tuple[str, ...]) -> list[re.Pattern[str]]:
    combined: list[str] = []
    for group in groups:
        combined.extend(group)
    return [re.compile(pattern, re.IGNORECASE) for pattern in combined]


def card_search_text(card: dict[str, Any]) -> str:
    """Match against title and description (correlation ids often live in the body)."""
    parts = [str(card.get("name") or ""), str(card.get("description") or "")]
    return "\n".join(parts)


def card_matches(card: dict[str, Any], patterns: list[re.Pattern[str]]) -> bool:
    text = card_search_text(card)
    return any(pattern.search(text) for pattern in patterns)


def finance_board_id(explicit: str) -> str:
    return (
        explicit.strip()
        or os.environ.get("PLANKA_FINANCE_BOARD_ID", "").strip()
        or os.environ.get("PLANKA_BOARD_ID", "").strip()
    )


def fetch_board_cards(board_id: str) -> list[dict[str, Any]]:
    payload = planka_request(f"boards/{board_id}")
    cards = payload.get("included", {}).get("cards", [])
    if not isinstance(cards, list):
        return []
    return [card for card in cards if isinstance(card, dict)]


def delete_card(card_id: str) -> None:
    planka_request(f"cards/{card_id}", method="DELETE")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path.home() / ".config/homelab-control/agent-finance.env",
        help="Env file with PLANKA_BASE_URL, PLANKA_API_KEY, PLANKA_FINANCE_BOARD_ID",
    )
    parser.add_argument(
        "--board-id",
        default="",
        help="Finance board id (default: PLANKA_FINANCE_BOARD_ID)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Delete matching cards (default: dry-run only)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable summary on stdout",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max deletions per run (0 = no limit)",
    )
    args = parser.parse_args(argv)

    if args.env_file:
        load_env_file(args.env_file.expanduser())

    board_id = finance_board_id(args.board_id)
    if not board_id:
        print(
            "PLANKA_FINANCE_BOARD_ID is required (env or --board-id)",
            file=sys.stderr,
        )
        return 2
    if not planka_auth_configured():
        print(
            "Planka auth not configured: set PLANKA_API_KEY (or token/user/pass) "
            "and PLANKA_BASE_URL",
            file=sys.stderr,
        )
        return 2

    patterns = compile_patterns(DEFAULT_FINANCE_TEST_PATTERNS)
    all_cards = fetch_board_cards(board_id)
    targets = [card for card in all_cards if card_matches(card, patterns)]

    if args.limit > 0:
        targets = targets[: args.limit]

    summary: dict[str, Any] = {
        "board_id": board_id,
        "dry_run": not args.execute,
        "total_cards": len(all_cards),
        "matched": len(targets),
        "deleted": 0,
        "errors": [],
        "cards": [
            {"id": str(card.get("id", "")), "name": str(card.get("name", ""))}
            for card in targets
        ],
    }

    if not args.execute:
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"DRY-RUN: {len(targets)} finance test card(s) on board {board_id}")
            for card in targets:
                print(f"  {card.get('id')}  {card.get('name')}")
            if targets:
                print("\nRe-run with --execute to delete.")
        return 0

    deleted = 0
    for index, card in enumerate(targets, start=1):
        card_id = str(card.get("id", ""))
        name = str(card.get("name", ""))
        if not card_id:
            continue
        try:
            delete_card(card_id)
            deleted += 1
            if not args.json and (index <= 20 or index % 50 == 0):
                print(f"deleted {card_id}  {name[:70]}")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            summary["errors"].append(
                {"id": card_id, "name": name, "code": exc.code, "detail": detail}
            )
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append({"id": card_id, "name": name, "error": str(exc)})
        if index % 10 == 0:
            time.sleep(0.05)

    summary["deleted"] = deleted
    remaining = fetch_board_cards(board_id)
    summary["remaining_total"] = len(remaining)
    summary["remaining_matched"] = sum(1 for card in remaining if card_matches(card, patterns))

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Deleted {deleted}/{len(targets)} matched finance test cards")
        if summary["errors"]:
            print(f"Errors: {len(summary['errors'])} (see --json for details)")
        print(
            f"Board now has {summary['remaining_total']} cards "
            f"({summary['remaining_matched']} still match patterns)"
        )
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
