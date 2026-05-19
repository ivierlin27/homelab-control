"""Unit tests for the ingest orchestration layer (F4a).

We don't have a real BMO PDF (F4b problem) so all tests inject a mock
PreParser via the ``get_importer_fn`` parameter. The mock returns a
deterministic list of ExtractedTransaction so we can assert the full
file-write + audit-write + bean-check-skip pipeline end-to-end.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Tuple

import pytest

from apps.finance_agent.importers.base import (
    ExtractedTransaction,
    Importer,
    PreParser,
)
from apps.finance_agent.importers.bmo_joint_chequing_pdf import (
    BmoJointChequingImporter,
)
from apps.finance_agent.ingest import (
    DEFAULT_AUDIT_PATH,
    IngestError,
    IngestResult,
    MAIN_FILENAME,
    TRANSACTIONS_FILENAME,
    TRANSACTIONS_INCLUDE_MARKER,
    ingest_file,
)


# --- mock pre-parser ------------------------------------------------------

@dataclass
class _MockPreParser:
    institution: str = "bmo-joint-chequing"
    txns: list = None  # type: ignore[assignment]

    def can_handle(self, _: str) -> bool:
        return True

    def extract(self, _: str):
        return list(self.txns or [])


def _make_mock_factory(txns):
    """Return a get_importer_fn that yields (_MockPreParser, real BMO importer)."""
    def _factory(slug: str) -> Tuple[PreParser, Importer]:
        assert slug == "bmo-joint-chequing"
        return _MockPreParser(txns=txns), BmoJointChequingImporter()
    return _factory


def _two_sample_txns():
    return [
        ExtractedTransaction(
            posting_date=date(2024, 1, 15),
            description="LOBLAWS #1234",
            amount=Decimal("-87.43"),
            currency="CAD",
        ),
        ExtractedTransaction(
            posting_date=date(2024, 1, 16),
            description="PAYROLL DEPOSIT",
            amount=Decimal("2500.00"),
            currency="CAD",
        ),
    ]


def _make_ledger(tmp_path: Path) -> Path:
    """Mirror the F3 ledger scaffold minimally enough for ingest to write to."""
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / MAIN_FILENAME).write_text(
        'option "title" "Test Ledger"\n'
        'option "operating_currency" "CAD"\n'
        '\n'
        'include "accounts.beancount"\n',
        encoding="utf-8",
    )
    # accounts.beancount doesn't matter for the ingest tests; we just need
    # main.beancount to exist for _ensure_main_include to update it.
    return ledger


# --- validation errors ----------------------------------------------------

def test_ingest_rejects_unknown_institution(tmp_path: Path) -> None:
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(IngestError, match="unknown institution"):
        ingest_file(
            institution="not-a-bank",
            file_path=f,
            ledger_dir=_make_ledger(tmp_path),
            audit_path=tmp_path / "audit.jsonl",
            run_bean_check=False,
        )


def test_ingest_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(IngestError, match="file not found"):
        ingest_file(
            institution="bmo-joint-chequing",
            file_path=tmp_path / "missing.pdf",
            ledger_dir=_make_ledger(tmp_path),
            audit_path=tmp_path / "audit.jsonl",
            run_bean_check=False,
        )


def test_ingest_rejects_missing_ledger_dir(tmp_path: Path) -> None:
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(IngestError, match="ledger directory"):
        ingest_file(
            institution="bmo-joint-chequing",
            file_path=f,
            ledger_dir=tmp_path / "nonexistent",
            audit_path=tmp_path / "audit.jsonl",
            run_bean_check=False,
        )


def test_ingest_surfaces_notimplemented_as_ingest_error(tmp_path: Path) -> None:
    """The default BMO pre-parser stub raises NotImplementedError (F4a)."""
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(IngestError, match="not yet implemented"):
        ingest_file(
            institution="bmo-joint-chequing",
            file_path=f,
            ledger_dir=_make_ledger(tmp_path),
            audit_path=tmp_path / "audit.jsonl",
            run_bean_check=False,
        )


# --- happy path with mock pre-parser --------------------------------------

def test_ingest_happy_path_writes_entries_and_audit(tmp_path: Path) -> None:
    ledger = _make_ledger(tmp_path)
    audit_path = tmp_path / "state" / "audit.jsonl"
    pdf = tmp_path / "bmo.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    result: IngestResult = ingest_file(
        institution="bmo-joint-chequing",
        file_path=pdf,
        ledger_dir=ledger,
        audit_path=audit_path,
        run_bean_check=False,
        get_importer_fn=_make_mock_factory(_two_sample_txns()),
        now=lambda: datetime(2024, 1, 17, 12, 0, tzinfo=timezone.utc),
    )

    assert result.institution == "bmo-joint-chequing"
    assert result.source_account == "Assets:CA:BMO:Chequing:Joint-4969"
    assert result.entries_written == 2
    assert result.bean_check_ran is False
    assert result.main_file_updated is True

    # transactions.beancount was created with both entries (chronological)
    tx_path = ledger / TRANSACTIONS_FILENAME
    assert tx_path.is_file()
    body = tx_path.read_text(encoding="utf-8")
    assert "LOBLAWS" in body
    assert "PAYROLL" in body
    assert body.index("LOBLAWS") < body.index("PAYROLL")  # date-sorted

    # main.beancount got the include line appended (idempotent on re-run)
    main_text = (ledger / MAIN_FILENAME).read_text(encoding="utf-8")
    assert TRANSACTIONS_INCLUDE_MARKER in main_text

    # audit row written
    assert audit_path.is_file()
    audit_lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 1
    row = json.loads(audit_lines[0])
    assert row["event"] == "finance_ingest"
    assert row["institution"] == "bmo-joint-chequing"
    assert row["source_account"] == "Assets:CA:BMO:Chequing:Joint-4969"
    assert row["entry_count"] == 2
    assert row["file_name"] == "bmo.pdf"
    assert row["bean_check_passed"] is None  # because run_bean_check=False
    assert "audit_hash" in row  # hash chain in effect


def test_ingest_main_include_is_idempotent(tmp_path: Path) -> None:
    ledger = _make_ledger(tmp_path)
    audit_path = tmp_path / "audit.jsonl"
    pdf = tmp_path / "bmo.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    factory = _make_mock_factory(_two_sample_txns())

    r1 = ingest_file(
        institution="bmo-joint-chequing",
        file_path=pdf,
        ledger_dir=ledger,
        audit_path=audit_path,
        run_bean_check=False,
        get_importer_fn=factory,
    )
    r2 = ingest_file(
        institution="bmo-joint-chequing",
        file_path=pdf,
        ledger_dir=ledger,
        audit_path=audit_path,
        run_bean_check=False,
        get_importer_fn=factory,
    )

    assert r1.main_file_updated is True
    assert r2.main_file_updated is False

    # Two ingests = two audit rows, both chained
    audit_lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 2
    rows = [json.loads(line) for line in audit_lines]
    assert rows[0]["audit_seq"] == 1
    assert rows[1]["audit_seq"] == 2
    assert rows[1]["audit_prev"] == rows[0]["audit_hash"]


def test_ingest_no_transactions_extracted_still_writes_audit(tmp_path: Path) -> None:
    """A statement with zero extracted txns is a valid (if rare) outcome.

    We still want an audit row so the operator knows the ingest happened.
    """
    ledger = _make_ledger(tmp_path)
    audit_path = tmp_path / "audit.jsonl"
    pdf = tmp_path / "bmo.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    result = ingest_file(
        institution="bmo-joint-chequing",
        file_path=pdf,
        ledger_dir=ledger,
        audit_path=audit_path,
        run_bean_check=False,
        get_importer_fn=_make_mock_factory([]),
    )

    assert result.entries_written == 0
    assert not (ledger / TRANSACTIONS_FILENAME).exists()  # no file = nothing written
    assert (ledger / MAIN_FILENAME).read_text(encoding="utf-8").count(
        TRANSACTIONS_INCLUDE_MARKER
    ) == 0  # include not added — nothing to include yet
    assert audit_path.is_file()  # still got an audit row
    row = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["entry_count"] == 0


# --- bean-check integration ------------------------------------------------

def test_ingest_skips_bean_check_when_command_missing(tmp_path: Path) -> None:
    ledger = _make_ledger(tmp_path)
    audit_path = tmp_path / "audit.jsonl"
    pdf = tmp_path / "bmo.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    result = ingest_file(
        institution="bmo-joint-chequing",
        file_path=pdf,
        ledger_dir=ledger,
        audit_path=audit_path,
        run_bean_check=True,
        bean_check_cmd="definitely-not-on-path-xyzzy",
        get_importer_fn=_make_mock_factory(_two_sample_txns()),
    )
    assert result.bean_check_ran is False
    assert "not on PATH" in result.bean_check_message


# --- default constants ----------------------------------------------------

def test_default_paths_under_user_home() -> None:
    # Cheap sanity: defaults should be under ~/finance and ~/.local/state
    assert str(DEFAULT_AUDIT_PATH).endswith(
        ".local/state/homelab-control/agent-finance/audit.jsonl"
    )
