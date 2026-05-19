"""Ingest orchestration: file → entries → ledger append + audit row.

All side effects live here. The importer layer is pure.

Pipeline:

    1. Validate inputs (institution slug known, file exists)
    2. PreParser.extract(file) → list[ExtractedTransaction]
    3. Importer.render(txns) → list[BeancountEntry]
    4. Append entries to <ledger_dir>/transactions.beancount (creating
       both the file and the include directive on first use)
    5. Optionally run `bean-check <ledger_dir>/main.beancount` to confirm
       the ledger still validates (best-effort; skipped if bean-check is
       not on PATH, which is the case on Mac dev boxes)
    6. Append one audit row to the agent-finance audit log

Each ingest produces an ``IngestResult`` summarizing what happened. The
CLI prints it (as text or JSON). The audit row is the durable record.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from .importers import (
    BeancountEntry,
    Importer,
    PreParser,
    PreParserError,
    get_importer,
    list_institutions,
)

DEFAULT_LEDGER_DIR = Path.home() / "finance" / "ledger"
DEFAULT_AUDIT_PATH = (
    Path.home() / ".local" / "state" / "homelab-control" / "agent-finance" / "audit.jsonl"
)
TRANSACTIONS_FILENAME = "transactions.beancount"
MAIN_FILENAME = "main.beancount"

# Marker the orchestrator writes into main.beancount the first time it adds
# the transactions include. Idempotent: subsequent ingests notice it and skip.
TRANSACTIONS_INCLUDE_LINE = 'include "transactions.beancount"\n'
TRANSACTIONS_INCLUDE_MARKER = "include \"transactions.beancount\""


class IngestError(Exception):
    """Raised on validation / IO errors during ingest."""


@dataclass(frozen=True)
class IngestResult:
    institution: str
    source_account: str
    file: str
    entries_written: int
    ledger_path: str
    audit_path: str
    bean_check_ran: bool
    bean_check_passed: bool | None
    bean_check_message: str = ""
    main_file_updated: bool = False

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ingest_file(
    *,
    institution: str,
    file_path: Path,
    ledger_dir: Path = DEFAULT_LEDGER_DIR,
    audit_path: Path = DEFAULT_AUDIT_PATH,
    run_bean_check: bool = True,
    # Injection points for tests:
    get_importer_fn: Callable[[str], tuple[PreParser, Importer]] = get_importer,
    bean_check_cmd: str = "bean-check",
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> IngestResult:
    """Top-level ingest. Raises ``IngestError`` on bad inputs."""

    if institution not in list_institutions():
        raise IngestError(
            f"unknown institution {institution!r}; known: {', '.join(list_institutions())}"
        )

    file_path = Path(file_path).expanduser().resolve()
    if not file_path.is_file():
        raise IngestError(f"file not found: {file_path}")

    ledger_dir = Path(ledger_dir).expanduser()
    if not ledger_dir.is_dir():
        raise IngestError(f"ledger directory not found: {ledger_dir}")

    pre_parser, importer = get_importer_fn(institution)

    try:
        txns = pre_parser.extract(str(file_path))
    except NotImplementedError as exc:
        # F4a stub path. Re-raise as IngestError so the CLI surfaces it
        # cleanly with exit code 2 (operator should know this is expected
        # for institutions whose PDF extractor isn't written yet).
        raise IngestError(f"{institution}: pre-parser not yet implemented — {exc}") from exc

    entries = importer.render(txns)

    transactions_path = ledger_dir / TRANSACTIONS_FILENAME
    _append_entries(transactions_path, entries)
    # Only touch main.beancount if we actually wrote entries. A zero-entry
    # ingest is rare-but-valid (operator imported an already-imported file,
    # or a statement with no activity) — no reason to dirty main for it.
    main_updated = (
        _ensure_main_include(ledger_dir / MAIN_FILENAME) if entries else False
    )

    bean_ran, bean_ok, bean_msg = _maybe_run_bean_check(
        ledger_dir / MAIN_FILENAME, run_bean_check, bean_check_cmd
    )

    _write_audit_row(
        audit_path,
        institution=institution,
        source_account=importer.source_account,
        file_path=file_path,
        entry_count=len(entries),
        bean_check_passed=bean_ok if bean_ran else None,
        ts=now(),
    )

    return IngestResult(
        institution=institution,
        source_account=importer.source_account,
        file=str(file_path),
        entries_written=len(entries),
        ledger_path=str(transactions_path),
        audit_path=str(audit_path),
        bean_check_ran=bean_ran,
        bean_check_passed=bean_ok if bean_ran else None,
        bean_check_message=bean_msg,
        main_file_updated=main_updated,
    )


# ---------------------------------------------------------------------------
# Side-effect helpers (kept small + testable)
# ---------------------------------------------------------------------------


def _append_entries(path: Path, entries: list[BeancountEntry]) -> None:
    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_entries = sorted(entries, key=lambda e: e.posting_date)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8") as fh:
        if needs_header:
            fh.write(
                ";; ─── transactions.beancount ──────────────────────────────────\n"
                ";; Auto-appended by agent:finance ingest. DO NOT hand-edit unless\n"
                ";; you also delete the corresponding audit row in:\n"
                ";;   ~/.local/state/homelab-control/agent-finance/audit.jsonl\n"
                ";; (Hand-edits drift the ledger from the audit chain.)\n"
                ";; ────────────────────────────────────────────────────────────\n"
                "\n"
            )
        for entry in sorted_entries:
            fh.write(entry.text)
            fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())


def _ensure_main_include(main_path: Path) -> bool:
    """Add `include "transactions.beancount"` to main.beancount if missing.

    Returns True if the file was modified. Idempotent.
    """
    if not main_path.is_file():
        return False
    text = main_path.read_text(encoding="utf-8")
    if TRANSACTIONS_INCLUDE_MARKER in text:
        return False
    # Append a blank line + the include directive. We avoid trying to be
    # clever about placement — operator can rearrange if they care.
    appended = text
    if not appended.endswith("\n"):
        appended += "\n"
    appended += "\n" + TRANSACTIONS_INCLUDE_LINE
    main_path.write_text(appended, encoding="utf-8")
    return True


def _maybe_run_bean_check(
    main_path: Path, requested: bool, cmd: str
) -> tuple[bool, bool, str]:
    """Run bean-check if requested AND available. Returns (ran, ok, message)."""
    if not requested:
        return False, False, "bean-check skipped (run_bean_check=False)"
    if shutil.which(cmd) is None:
        return False, False, f"bean-check skipped ({cmd!r} not on PATH)"
    if not main_path.is_file():
        return False, False, f"bean-check skipped ({main_path} not found)"
    try:
        result = subprocess.run(
            [cmd, str(main_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return True, False, "bean-check timed out after 60s"
    if result.returncode == 0:
        return True, True, "bean-check passed"
    msg = (result.stdout + result.stderr).strip()
    return True, False, f"bean-check failed (exit {result.returncode}): {msg[:500]}"


def _write_audit_row(
    audit_path: Path,
    *,
    institution: str,
    source_account: str,
    file_path: Path,
    entry_count: int,
    bean_check_passed: bool | None,
    ts: datetime,
) -> None:
    """Append a hash-chained audit row for this ingest.

    Delayed import of apps._shared.audit so the importer/ingest tests can
    run without dragging the audit module into their dependency set.
    """
    audit_path = Path(audit_path).expanduser()
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    # Lazy import — avoid coupling test runs of pure ingest helpers to the
    # full _shared.audit module.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from _shared.audit import AuditLog  # noqa: E402

    AuditLog(audit_path).append(
        {
            "event": "finance_ingest",
            "institution": institution,
            "source_account": source_account,
            "file": str(file_path),
            "file_name": file_path.name,
            "entry_count": entry_count,
            "bean_check_passed": bean_check_passed,
            "ingested_at": ts.isoformat(),
        }
    )
