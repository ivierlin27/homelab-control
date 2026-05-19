"""Importer interfaces — shared across all institutions.

Design constraints driving these types:

1. PreParser output is fully testable WITHOUT pdfplumber. Tests construct
   ExtractedTransaction objects directly and feed them to the Importer.
2. Importer output is fully testable WITHOUT beancount. We emit Beancount
   syntax as text (BeancountEntry); validation via `bean-check` is a
   downstream verifier step, not part of the importer contract.
3. Both layers are pure: no file I/O, no globals, no logging. The Ingest
   layer (apps/finance_agent/ingest.py) owns all side effects.
4. ExtractedTransaction is currency-aware (CAD vs USD savings account 6863).
   Amounts use Decimal — float arithmetic on money is unacceptable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol


class PreParserError(Exception):
    """Raised when the institution-specific pre-parser cannot read the file."""


class ImporterError(Exception):
    """Raised when an importer rejects its input (bad extracted txns, etc.)."""


@dataclass(frozen=True)
class ExtractedTransaction:
    """One transaction as extracted from a statement, pre-categorization.

    The Importer receives a list of these and turns them into Beancount
    entries. F4 keeps the schema minimal; richer fields (memo, check_no,
    fitid, running_balance) get added when a real importer needs them.
    """

    posting_date: date
    description: str
    amount: Decimal
    currency: str  # "CAD" or "USD" — must match the source account's currency
    raw_line: str = ""  # original line from PDF/CSV, for audit and debugging
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise PreParserError("ExtractedTransaction.description cannot be blank")
        if self.currency not in {"CAD", "USD"}:
            raise PreParserError(
                f"ExtractedTransaction.currency must be CAD or USD, got {self.currency!r}"
            )
        if not isinstance(self.amount, Decimal):
            raise PreParserError(
                f"ExtractedTransaction.amount must be Decimal, got {type(self.amount).__name__}"
            )


@dataclass(frozen=True)
class BeancountEntry:
    """One Beancount entry rendered as text (newline-terminated)."""

    text: str
    posting_date: date  # for sorting / file routing
    # Optional structured echo for tests and the audit row. Not parsed back.
    source_account: str = ""
    counter_account: str = ""
    amount: Decimal | None = None
    currency: str = ""

    def __post_init__(self) -> None:
        if not self.text.endswith("\n"):
            raise ImporterError("BeancountEntry.text must be newline-terminated")


class PreParser(Protocol):
    """Per-institution PDF/CSV → ExtractedTransaction[] adapter."""

    institution: str  # institution slug, must match registry key

    def can_handle(self, path_or_bytes: bytes | str) -> bool:
        """Cheap sniff: filename heuristic or first-page magic bytes."""

    def extract(self, path_or_bytes: bytes | str) -> list[ExtractedTransaction]:
        """Parse the input and return all transactions in posting-date order."""


class Importer(Protocol):
    """Per-account ExtractedTransaction[] → BeancountEntry[] adapter."""

    institution: str
    source_account: str  # e.g. "Assets:CA:BMO:Chequing:Joint-4969"
    currency: str  # CAD or USD — must match the source account's declared currency
    counter_account: str = "Expenses:Uncategorized"

    def render(self, txns: list[ExtractedTransaction]) -> list[BeancountEntry]:
        """Render the extracted txns as Beancount entries (no I/O)."""


# ---------------------------------------------------------------------------
# Default rendering helper — used by all importers unless they need something
# institution-specific. Pulled out so it's testable independently.
# ---------------------------------------------------------------------------


def render_simple_entry(
    txn: ExtractedTransaction,
    *,
    source_account: str,
    counter_account: str,
    importer_slug: str,
) -> BeancountEntry:
    """Render one transaction as a two-leg Beancount entry.

    Sign convention: ``txn.amount`` is the signed delta to the source account
    (positive = credit/deposit, negative = debit/withdrawal). The counter
    account gets the opposite sign so the entry balances.

    The ``! `` flag marks the entry as pending operator review. F6's
    categorize loop will rewrite the counter leg with a real category and
    promote the flag to ``*``.
    """
    if txn.currency != "CAD" and txn.currency != "USD":
        raise ImporterError(f"render_simple_entry: unsupported currency {txn.currency!r}")

    desc = txn.description.replace('"', "'").strip()
    amt = txn.amount
    inverse = -amt
    # Beancount: dates are ISO, amounts are explicit-precision Decimals.
    text = (
        f"{txn.posting_date.isoformat()} ! \"{desc}\"\n"
        f"  source_importer: \"{importer_slug}\"\n"
        f"  raw_line: \"{_escape_metadata(txn.raw_line)}\"\n"
        f"  {source_account:<55} {amt:>14.2f} {txn.currency}\n"
        f"  {counter_account:<55} {inverse:>14.2f} {txn.currency}\n"
    )
    return BeancountEntry(
        text=text,
        posting_date=txn.posting_date,
        source_account=source_account,
        counter_account=counter_account,
        amount=amt,
        currency=txn.currency,
    )


def _escape_metadata(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', "'").replace("\n", " ")[:200]
