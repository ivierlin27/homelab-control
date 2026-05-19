"""Importer interfaces — shared across all institutions.

Design constraints driving these types:

1. PreParser output is fully testable WITHOUT pdfplumber. Tests construct
   StatementExtract / ExtractedTransaction objects directly and feed
   them to the Importer.
2. Importer output is fully testable WITHOUT beancount. We emit Beancount
   syntax as text (BeancountEntry); validation via `bean-check` is a
   downstream verifier step, not part of the importer contract.
3. Both layers are pure: no file I/O, no globals, no logging. The Ingest
   layer (apps/finance_agent/ingest.py) owns all side effects.
4. ExtractedTransaction is currency-aware (CAD vs USD savings account 6863).
   Amounts use Decimal — float arithmetic on money is unacceptable.
5. StatementExtract carries the (optional) opening and closing balances
   so the Importer can emit Beancount `pad` + `balance` assertions that
   self-validate the period via bean-check. If a transaction was missed
   in parsing, the closing-balance assertion will fail loudly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional, Protocol


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
class StatementExtract:
    """Everything one statement file yields, pre-import.

    PreParsers return this; Importers consume it. The opening/closing
    balance fields are optional (some sources — e.g. a hand-edited CSV —
    won't supply them) but PDF importers should produce them whenever
    the statement carries them, because they unlock self-validation via
    Beancount `pad`/`balance` directives.
    """

    transactions: list[ExtractedTransaction] = field(default_factory=list)
    opening_date: Optional[date] = None
    opening_balance: Optional[Decimal] = None
    closing_date: Optional[date] = None
    closing_balance: Optional[Decimal] = None
    statement_id: str = ""  # arbitrary identifier (e.g. period label) for audit


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
    """Per-institution PDF/CSV → StatementExtract adapter."""

    institution: str  # institution slug, must match registry key

    def can_handle(self, path_or_bytes: bytes | str) -> bool:
        """Cheap sniff: filename heuristic or first-page magic bytes."""

    def extract(self, path_or_bytes: bytes | str) -> StatementExtract:
        """Parse the input and return all transactions in posting-date order."""


class Importer(Protocol):
    """Per-account StatementExtract → BeancountEntry[] adapter."""

    institution: str
    source_account: str  # e.g. "Assets:CA:BMO:Chequing:Joint-4969"
    currency: str  # CAD or USD — must match the source account's declared currency
    counter_account: str = "Expenses:Uncategorized"

    def render(self, extract: StatementExtract) -> list[BeancountEntry]:
        """Render the statement as Beancount entries (no I/O).

        Entries include any pad+balance assertions for opening balance
        (if present) and a balance assertion for closing balance (if
        present), interleaved chronologically with the transaction
        entries. bean-check then validates the period end-to-end.
        """


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


def render_pad_balance(
    *,
    opening_date: date,
    source_account: str,
    opening_balance: Decimal,
    currency: str,
    pad_from_account: str = "Equity:Opening-Balances",
) -> list[BeancountEntry]:
    """Render the two directives that anchor an account's opening balance.

    Beancount semantics: a ``pad`` directive at date D inserts a balancing
    transaction *immediately before* the following ``balance`` assertion
    on the same account. So we emit:

        D-1 pad     <account>  <pad_from_account>
        D   balance <account>  <opening_balance> <currency>

    The pad is dated one day before the balance to ensure Beancount inserts
    the synthetic transaction before any other postings on date D. Both
    directives are returned as separate BeancountEntry objects so the
    ingest layer can sort them with everything else by posting_date.
    """
    pad_date = date.fromordinal(opening_date.toordinal() - 1)
    pad_text = (
        f"{pad_date.isoformat()} pad {source_account} {pad_from_account}\n"
    )
    balance_text = (
        f"{opening_date.isoformat()} balance {source_account:<55} "
        f"{opening_balance:>14.2f} {currency}\n"
    )
    return [
        BeancountEntry(
            text=pad_text,
            posting_date=pad_date,
            source_account=source_account,
            counter_account=pad_from_account,
            amount=None,
            currency=currency,
        ),
        BeancountEntry(
            text=balance_text,
            posting_date=opening_date,
            source_account=source_account,
            counter_account="",
            amount=opening_balance,
            currency=currency,
        ),
    ]


def render_closing_balance_assertion(
    *,
    closing_date: date,
    source_account: str,
    closing_balance: Decimal,
    currency: str,
) -> BeancountEntry:
    """Render a `balance` directive that asserts the period-end balance.

    Dated the day AFTER the closing date, because Beancount's `balance`
    assertion checks the balance AT THE START OF THE GIVEN DAY, which
    means we want it to fire after all of the closing-day transactions
    have settled.
    """
    assert_date = date.fromordinal(closing_date.toordinal() + 1)
    text = (
        f"{assert_date.isoformat()} balance {source_account:<55} "
        f"{closing_balance:>14.2f} {currency}\n"
    )
    return BeancountEntry(
        text=text,
        posting_date=assert_date,
        source_account=source_account,
        counter_account="",
        amount=closing_balance,
        currency=currency,
    )
