#!/usr/bin/env python3
"""agent:finance CLI entrypoint.

Sprint F2 ships only the `status` subcommand — a smoke test that the
agent process boots, validates against the capability registry, and can
detect whether the ledger has been initialized.

  python -m apps.finance_agent status
  python -m apps.finance_agent status --json
  python -m apps.finance_agent status --ledger-dir /tmp/foo  # for tests

Acceptance (F2): with no ledger present, prints exactly
  agent:finance v0.1 — no ledger initialized
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps"))

from agentlib import boot_principal  # noqa: E402

from . import __version__

DEFAULT_PRINCIPAL = "agent:finance"
DEFAULT_LEDGER_DIR = Path.home() / "finance" / "ledger"
LEDGER_MAIN_FILE = "main.beancount"

# Public version label rendered in human output. Pinned at v0.1 for the
# whole MVP-B window; the package __version__ tracks finer changes.
VERSION_LABEL = "v0.1"


def _short_version() -> str:
    return f"{DEFAULT_PRINCIPAL} {VERSION_LABEL}"


def ledger_state(ledger_dir: Path) -> dict[str, object]:
    """Inspect the ledger directory without touching it.

    Returns a dict the CLI can render either as a human string or JSON.
    Kept pure (no prints, no side effects) so it's trivially testable.
    """
    main_file = ledger_dir / LEDGER_MAIN_FILE
    if not main_file.is_file():
        return {
            "initialized": False,
            "ledger_dir": str(ledger_dir),
            "main_file": str(main_file),
        }
    return {
        "initialized": True,
        "ledger_dir": str(ledger_dir),
        "main_file": str(main_file),
        "main_file_bytes": main_file.stat().st_size,
    }


def render_status(state: dict[str, object], *, as_json: bool) -> str:
    if as_json:
        payload = {
            "principal": DEFAULT_PRINCIPAL,
            "version": VERSION_LABEL,
            "package_version": __version__,
            **state,
        }
        return json.dumps(payload, indent=2, sort_keys=True)
    if not state["initialized"]:
        # F2 acceptance string. Em dash, not hyphen.
        return f"{_short_version()} — no ledger initialized"
    return f"{_short_version()} — ledger initialized at {state['ledger_dir']}"


def _cmd_status(args: argparse.Namespace) -> int:
    ledger_dir = Path(args.ledger_dir).expanduser()
    state = ledger_state(ledger_dir)
    print(render_status(state, as_json=args.json))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.finance_agent",
        description="agent:finance — advisory finance agent (MVP-B)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="show agent + ledger status")
    status.add_argument(
        "--ledger-dir",
        default=str(DEFAULT_LEDGER_DIR),
        help=f"ledger directory (default: {DEFAULT_LEDGER_DIR})",
    )
    status.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the human one-liner",
    )
    status.set_defaults(func=_cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Skip registry boot when --skip-boot is set OR when running under
    # pytest. Tests exercise the rendering / state functions directly;
    # full boot requires the agent-finance identity state file which
    # only exists on Alienware.
    parser = build_parser()
    parser.add_argument(
        "--skip-boot",
        action="store_true",
        help="skip boot_principal() (useful for local smoke + tests)",
    )
    args = parser.parse_args(argv)

    if not args.skip_boot:
        boot_principal(DEFAULT_PRINCIPAL)

    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
