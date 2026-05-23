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
from datetime import date
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


def _cmd_ingest_ofx(args: argparse.Namespace) -> int:
    """Ingest a multi-account BMO OFX file."""
    from .importers.bmo_ofx import FitIdStore, parse_ofx_file
    from .importers.base import render_closing_balance_assertion, render_simple_entry
    from .ingest import (
        TRANSACTIONS_FILENAME,
        _append_entries,
        _ensure_main_include,
        _maybe_run_bean_check,
    )
    from .ledger_inspector import find_last_balance_date

    ledger_dir = Path(args.ledger_dir).expanduser()
    transactions_path = ledger_dir / TRANSACTIONS_FILENAME
    main_path = ledger_dir / "main.beancount"
    state_dir = Path(args.state_dir).expanduser()
    fitid_store = FitIdStore(state_dir / "fitids")

    # Build per-account date cutoffs from the ledger (latest balance assertion)
    from .importers.bmo_ofx import _slug_meta_map
    cutoff_dates: dict[str, date] = {}
    for slug, (src_acct, _cur) in _slug_meta_map().items():
        d = find_last_balance_date(transactions_path, src_acct)
        if d:
            cutoff_dates[slug] = d

    # Parse the OFX
    ofx_path = Path(args.file).expanduser()
    try:
        extracts = parse_ofx_file(
            ofx_path,
            fitid_store=fitid_store,
            cutoff_dates=cutoff_dates,
        )
    except Exception as exc:
        print(f"ingest-ofx failed: {exc}", file=sys.stderr)
        return 2

    if not extracts:
        print("No new transactions found in OFX (all accounts filtered by date-cutoff or FITID dedup).")
        return 0

    # Ingest each account's transactions
    total_txns = 0
    total_entries = 0

    for extract in extracts:
        from .importers.base import BeancountEntry
        entries: list[BeancountEntry] = []
        for txn in extract.transactions:
            entries.append(render_simple_entry(
                txn,
                source_account=extract.source_account,
                counter_account="Expenses:Uncategorized",
                importer_slug=f"bmo-ofx:{extract.slug}",
            ))

        if extract.closing_balance is not None and extract.closing_date is not None:
            entries.append(render_closing_balance_assertion(
                closing_date=extract.closing_date,
                source_account=extract.source_account,
                closing_balance=extract.closing_balance,
                currency=extract.currency,
            ))

        if entries:
            _append_entries(transactions_path, entries)
            _ensure_main_include(main_path)
            total_entries += len(entries)
            total_txns += len(extract.transactions)

            # Commit FITIDs
            for fitid in extract.ingested_fitids:
                fitid_store.add(extract.slug, fitid)
            fitid_store.commit(extract.slug)

    # Run bean-check once at the end (covers all accounts)
    bean_ran, bean_ok, bean_msg = _maybe_run_bean_check(
        main_path, not args.skip_bean_check, "bean-check"
    )

    # Print summary
    print(f"✓ ingest-ofx complete: {total_txns} transactions, {total_entries} entries across {len(extracts)} accounts")
    for extract in extracts:
        print(f"  {extract.slug:<35} +{len(extract.transactions):>3} txns  (skipped {len(extract.skipped_fitids)} dedup/cutoff)")
    if bean_ran:
        print(f"  bean-check     : {'passed' if bean_ok else 'FAILED'} — {bean_msg}")
    else:
        print(f"  bean-check     : {bean_msg}")
    print(f"  ledger file    : {transactions_path}")
    print(f"  fitid store    : {state_dir / 'fitids'}/")

    return 0 if (not bean_ran or bean_ok) else 1


def _cmd_ingest_csv(args: argparse.Namespace) -> int:
    """Ingest a Bank of America CSV file."""
    from .importers.bofa_csv import BOFA_PROFILES, parse_bofa_csv
    from .importers.bmo_ofx import FitIdStore
    from .importers.base import render_closing_balance_assertion, render_simple_entry
    from .ingest import (
        TRANSACTIONS_FILENAME,
        _append_entries,
        _ensure_main_include,
        _maybe_run_bean_check,
    )
    from .ledger_inspector import find_last_balance_date

    profile = BOFA_PROFILES[args.account]
    ledger_dir = Path(args.ledger_dir).expanduser()
    transactions_path = ledger_dir / TRANSACTIONS_FILENAME
    main_path = ledger_dir / "main.beancount"
    state_dir = Path(args.state_dir).expanduser()
    fitid_store = FitIdStore(state_dir / "fitids")

    # Date cutoff from existing ledger
    cutoff = find_last_balance_date(transactions_path, profile.source_account)

    csv_path = Path(args.file).expanduser()
    try:
        extract = parse_bofa_csv(
            csv_path,
            profile,
            fitid_store=fitid_store,
            cutoff_date=cutoff,
        )
    except Exception as exc:
        print(f"ingest-csv failed: {exc}", file=sys.stderr)
        return 2

    if not extract.transactions:
        print(f"No new transactions for {profile.slug} (all filtered by cutoff/hash dedup).")
        return 0

    # Build entries
    from .importers.base import BeancountEntry
    entries: list[BeancountEntry] = []

    # Opening balance assertion (only if no prior history)
    if cutoff is None:
        from datetime import timedelta
        entries.append(render_closing_balance_assertion(
            closing_date=extract.opening_date,
            source_account=extract.source_account,
            closing_balance=extract.opening_balance,
            currency=extract.currency,
        ))

    for txn in extract.transactions:
        entries.append(render_simple_entry(
            txn,
            source_account=extract.source_account,
            counter_account="Expenses:Uncategorized",
            importer_slug=f"bofa-csv:{profile.slug}",
        ))

    # Closing balance assertion
    entries.append(render_closing_balance_assertion(
        closing_date=extract.closing_date,
        source_account=extract.source_account,
        closing_balance=extract.closing_balance,
        currency=extract.currency,
    ))

    _append_entries(transactions_path, entries)
    _ensure_main_include(main_path)

    # Commit content-hashes
    for h in extract.ingested_hashes:
        fitid_store.add(profile.slug, h)
    fitid_store.commit(profile.slug)

    # bean-check
    bean_ran, bean_ok, bean_msg = _maybe_run_bean_check(
        main_path, not args.skip_bean_check, "bean-check"
    )

    print(f"✓ ingest-csv complete: {len(extract.transactions)} transactions, {len(entries)} entries")
    print(f"  account        : {profile.slug} ({profile.source_account})")
    print(f"  period         : {extract.opening_date} → {extract.closing_date}")
    print(f"  skipped        : {len(extract.skipped_hashes)} (cutoff/hash dedup)")
    if bean_ran:
        print(f"  bean-check     : {'passed' if bean_ok else 'FAILED'} — {bean_msg}")
    else:
        print(f"  bean-check     : {bean_msg}")
    print(f"  ledger file    : {transactions_path}")

    return 0 if (not bean_ran or bean_ok) else 1


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

    # ingest-ofx ---------------------------------------------------------------
    ingest_ofx = subparsers.add_parser(
        "ingest-ofx",
        help="ingest a multi-account BMO OFX file with FITID dedup + date-cutoff (F7)",
    )
    ingest_ofx.add_argument(
        "--file",
        required=True,
        help="path to the .ofx file",
    )
    ingest_ofx.add_argument(
        "--ledger-dir",
        default=str(DEFAULT_LEDGER_DIR),
        help=f"ledger directory (default: {DEFAULT_LEDGER_DIR})",
    )
    ingest_ofx.add_argument(
        "--state-dir",
        default="~/.local/state/homelab-control/agent-finance",
        help="state directory for FITID store (default: ~/.local/state/homelab-control/agent-finance)",
    )
    ingest_ofx.add_argument(
        "--skip-bean-check",
        action="store_true",
        help="skip post-ingest bean-check",
    )
    ingest_ofx.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON",
    )
    ingest_ofx.set_defaults(func=_cmd_ingest_ofx)

    # ingest-csv ---------------------------------------------------------------
    ingest_csv = subparsers.add_parser(
        "ingest-csv",
        help="ingest a Bank of America CSV with content-hash dedup (F7)",
    )
    ingest_csv.add_argument(
        "--file",
        required=True,
        help="path to the .csv file",
    )
    ingest_csv.add_argument(
        "--account",
        required=True,
        choices=["bofa-checking-5396", "bofa-savings-8762"],
        help="BofA account slug",
    )
    ingest_csv.add_argument(
        "--ledger-dir",
        default=str(DEFAULT_LEDGER_DIR),
        help=f"ledger directory (default: {DEFAULT_LEDGER_DIR})",
    )
    ingest_csv.add_argument(
        "--state-dir",
        default="~/.local/state/homelab-control/agent-finance",
        help="state directory for hash store",
    )
    ingest_csv.add_argument(
        "--skip-bean-check",
        action="store_true",
        help="skip post-ingest bean-check",
    )
    ingest_csv.set_defaults(func=_cmd_ingest_csv)

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
