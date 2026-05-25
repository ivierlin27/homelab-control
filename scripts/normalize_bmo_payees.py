#!/usr/bin/env python3
"""Truncate BMO statement footer text from payee fields in transactions.beancount.

BMO PDF import used to glue multi-page legalese after ``Pleasereportanyerrors``.
This script rewrites only transaction header descriptions (the quoted payee on
``YYYY-MM-DD ! "..."`` / ``* "..."`` lines). Postings, metadata, and raw_line
audit fields are left unchanged.

Usage:
  python3 scripts/normalize_bmo_payees.py --ledger-dir ~/finance/ledger
  python3 scripts/normalize_bmo_payees.py --ledger-dir ~/finance/ledger --dry-run
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow ``python3 scripts/normalize_bmo_payees.py`` from repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from apps.finance_agent.importers.base import normalize_bmo_description

TRANSACTIONS_FILE = "transactions.beancount"
MAIN_FILE = "main.beancount"
_FOOTER_MARKER = re.compile(r"(?i)pleasereportanyerrors")
_TXN_HEADER = re.compile(
    r'^(\d{4}-\d{2}-\d{2}) ([*!]) "((?:[^"\\]|\\.)*)"(.*)$'
)


def _escape_payee(description: str) -> str:
    """Match render_simple_entry quoting conventions."""
    return description.replace("\\", "").replace('"', "'").strip()


def normalize_transactions_text(text: str) -> tuple[str, int, int]:
    """Return (new_text, headers_scanned, headers_changed)."""
    changed = 0
    scanned = 0
    out_lines: list[str] = []

    for line in text.splitlines(keepends=True):
        body = line.rstrip("\n")
        newline = line[len(body) :]
        m = _TXN_HEADER.match(body)
        if not m or not _FOOTER_MARKER.search(m.group(3)):
            out_lines.append(line)
            continue
        scanned += 1
        date, flag, desc, tail = m.group(1), m.group(2), m.group(3), m.group(4)
        new_desc = _escape_payee(normalize_bmo_description(desc))
        if new_desc == desc:
            out_lines.append(line)
            continue
        changed += 1
        out_lines.append(f'{date} {flag} "{new_desc}"{tail}{newline}')

    new_text = "".join(out_lines)
    if text.endswith("\n") and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, scanned, changed


def run_bean_check(ledger_dir: Path, cmd: str = "bean-check") -> tuple[bool, str]:
    main_path = ledger_dir / MAIN_FILE
    if not main_path.is_file():
        return False, f"{main_path} not found"
    if not shutil.which(cmd):
        return False, f"{cmd} not on PATH"
    proc = subprocess.run(
        [cmd, str(main_path)],
        capture_output=True,
        text=True,
    )
    msg = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, msg.strip() or f"exit {proc.returncode}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ledger-dir",
        type=Path,
        default=Path.home() / "finance" / "ledger",
        help="ledger directory containing transactions.beancount",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report changes without writing",
    )
    parser.add_argument(
        "--skip-bean-check",
        action="store_true",
        help="skip post-write bean-check",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="do not create a .bak copy before writing",
    )
    args = parser.parse_args(argv)

    ledger_dir = args.ledger_dir.expanduser().resolve()
    txn_path = ledger_dir / TRANSACTIONS_FILE
    if not txn_path.is_file():
        print(f"error: {txn_path} not found", file=sys.stderr)
        return 1

    original = txn_path.read_text(encoding="utf-8")
    new_text, scanned, changed = normalize_transactions_text(original)

    print(f"ledger: {txn_path}")
    print(f"headers with BMO footer marker: {scanned}")
    print(f"headers truncated: {changed}")

    if changed == 0:
        print("nothing to do")
        return 0

    if args.dry_run:
        print("dry-run: no files modified")
        return 0

    if not args.no_backup:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = txn_path.with_suffix(f".beancount.bak-{ts}")
        shutil.copy2(txn_path, backup)
        print(f"backup: {backup}")

    txn_path.write_text(new_text, encoding="utf-8")

    if args.skip_bean_check:
        print("bean-check: skipped")
        return 0

    ok, msg = run_bean_check(ledger_dir)
    if ok:
        print("bean-check: passed")
        return 0
    print(f"bean-check: FAILED\n{msg}", file=sys.stderr)
    if not args.no_backup:
        print("restore from backup if needed", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
