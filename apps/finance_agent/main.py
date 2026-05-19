#!/usr/bin/env python3
"""agent:finance CLI entrypoint.

Subcommands:

  status   — show agent + ledger health (F2)
  ingest   — parse a bank statement into Beancount entries (F4)

Examples:

  python -m apps.finance_agent status
  python -m apps.finance_agent status --json
  python -m apps.finance_agent ingest \\
      --institution bmo-joint-chequing \\
      --file ~/finance/fixtures/bmo-joint-chequing-2024-01.pdf

Acceptance strings (F2):
  with no ledger: "agent:finance v0.1 — no ledger initialized"
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


def _cmd_ingest(args: argparse.Namespace) -> int:
    from .ingest import DEFAULT_AUDIT_PATH, IngestError, ingest_file

    if args.list_institutions:
        from .importers import list_institutions
        for slug in list_institutions():
            print(slug)
        return 0

    if not args.institution or not args.file:
        print("error: --institution and --file are required (or use --list-institutions)", file=sys.stderr)
        return 2

    try:
        result = ingest_file(
            institution=args.institution,
            file_path=Path(args.file),
            ledger_dir=Path(args.ledger_dir).expanduser(),
            audit_path=Path(args.audit_path).expanduser() if args.audit_path else DEFAULT_AUDIT_PATH,
            run_bean_check=not args.skip_bean_check,
            statement_year=args.statement_year,
        )
    except IngestError as exc:
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True, default=str))
    else:
        print(f"✓ ingested {result.entries_written} entries from {Path(result.file).name}")
        print(f"  institution    : {result.institution}")
        print(f"  source account : {result.source_account}")
        print(f"  ledger file    : {result.ledger_path}")
        print(f"  audit row      : {result.audit_path}")
        if result.main_file_updated:
            print(f"  main.beancount : updated to include transactions.beancount")
        if result.bean_check_ran:
            status_str = "passed" if result.bean_check_passed else "FAILED"
            print(f"  bean-check     : {status_str} — {result.bean_check_message}")
        else:
            print(f"  bean-check     : skipped ({result.bean_check_message})")
    return 0 if (not result.bean_check_ran or result.bean_check_passed) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.finance_agent",
        description="agent:finance — advisory finance agent (MVP-B)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status -----------------------------------------------------------------
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

    # ingest -----------------------------------------------------------------
    ingest = subparsers.add_parser(
        "ingest",
        help="parse a bank statement into Beancount entries (F4)",
    )
    ingest.add_argument(
        "--institution",
        help="institution slug (use --list-institutions to enumerate)",
    )
    ingest.add_argument(
        "--file",
        help="path to the statement file (PDF/OFX/CSV per institution)",
    )
    ingest.add_argument(
        "--ledger-dir",
        default=str(DEFAULT_LEDGER_DIR),
        help=f"ledger directory (default: {DEFAULT_LEDGER_DIR})",
    )
    ingest.add_argument(
        "--audit-path",
        default=None,
        help="audit log path (default: ~/.local/state/homelab-control/agent-finance/audit.jsonl)",
    )
    ingest.add_argument(
        "--skip-bean-check",
        action="store_true",
        help="skip post-ingest bean-check (default: run if available)",
    )
    ingest.add_argument(
        "--statement-year",
        type=int,
        default=None,
        help=(
            "year to anchor undated transactions (e.g. BMO PDFs don't put "
            "year on each line). Default: inferred from filename if it "
            "contains exactly one 20xx token."
        ),
    )
    ingest.add_argument(
        "--list-institutions",
        action="store_true",
        help="print known institution slugs and exit",
    )
    ingest.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON",
    )
    ingest.set_defaults(func=_cmd_ingest)

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
