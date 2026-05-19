"""BMO joint chequing (account 4969) — PDF importer.

PDF layout observed in real BMO statements (sanitized sample 2024-03):

  PrimaryChequingAccount#X-YYY
  ...owner block...
  Mar19 Openingbalance 20,347.81
  Mar24 ScheduledTransfer,TF0764#3953-616 20.00 20,327.81
  Mar28 IncomingWirePayment,INCOMINGWIRE 66,331.86 87,531.04
  PAYMENT,GB,CROWEU.K.LLP                                  <-- continuation
  ...
  Apr17 Closingtotals 65,754.17 68,294.70                  <-- skip

Key facts:
  - pdfplumber concatenates words within a description into one space-free
    token. Dates are also glued ("Mar19" not "Mar 19").
  - Transaction lines have NO debit/credit sign on the amount column.
    We infer sign from `new_balance - prior_balance`. This makes parsing
    self-validating: if a transaction's amount doesn't reconcile, the
    parser raises rather than guess.
  - The opening balance line has a different shape (no amount, just balance).
  - The closing totals line has the same shape as a transaction but the
    description is the literal "Closingtotals" and is a sum, not a txn.
  - Continuation lines (description wrap) carry no date and need to be
    glued back onto the prior transaction. Page-break boilerplate must
    be filtered first or it'll look like continuations.
  - No year on transaction lines — caller must supply (CLI flag or filename
    inference).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .base import (
    BeancountEntry,
    ExtractedTransaction,
    Importer,
    PreParser,
    PreParserError,
    StatementExtract,
    render_closing_balance_assertion,
    render_pad_balance,
    render_simple_entry,
)

# Hard-coded constants — F4 targets this one account first.
INSTITUTION = "bmo-joint-chequing"
SOURCE_ACCOUNT = "Assets:CA:BMO:Chequing:Joint-4969"
CURRENCY = "CAD"
COUNTER_ACCOUNT = "Expenses:Uncategorized"
ACCOUNT_NUMBER_SUFFIX = "4-969"  # appears in PrimaryChequingAccount#...-4-969 marker

# ---------------------------------------------------------------------------
# Regex / constants for parsing BMO PDF text dumps
# ---------------------------------------------------------------------------

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_MONTH_PATTERN = "|".join(_MONTHS.keys())

# A transaction line: <MonDD> <single-token desc> <amount> <balance>
_TXN_LINE_RE = re.compile(
    rf"^(?P<date>(?:{_MONTH_PATTERN}))(?P<day>\d{{1,2}})\s+"
    r"(?P<desc>\S+)\s+"
    r"(?P<amount>[\d,]+\.\d{2})\s+"
    r"(?P<balance>[\d,]+\.\d{2})\s*$"
)

# Opening balance line: <MonDD> Openingbalance <balance>
_OPENING_LINE_RE = re.compile(
    rf"^(?P<date>(?:{_MONTH_PATTERN}))(?P<day>\d{{1,2}})\s+"
    r"Openingbalance\s+(?P<balance>[\d,]+\.\d{2})\s*$"
)

# Closing totals line: <MonDD> Closingtotals <deducted> <added>
_CLOSING_LINE_RE = re.compile(
    rf"^(?P<date>(?:{_MONTH_PATTERN}))(?P<day>\d{{1,2}})\s+"
    r"Closingtotals\s+[\d,]+\.\d{2}\s+[\d,]+\.\d{2}\s*$"
)

# Statement period footer/header: "For the period ending April 18, 2022"
# pdfplumber may or may not preserve inter-word whitespace; \s* between
# every word accommodates both layouts. Months are spelled out in full
# (April, not Apr) per real BMO output. Search (not match) because the
# line may have leading boilerplate.
_FULL_MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5,
    "June": 6, "July": 7, "August": 8, "September": 9, "October": 10,
    "November": 11, "December": 12,
}
_PERIOD_LINE_RE = re.compile(
    r"For\s*the\s*period\s*ending\s+"
    r"(?P<month>" + "|".join(_FULL_MONTHS.keys()) + r")\s*"
    r"(?P<day>\d{1,2}),?\s*"
    r"(?P<year>\d{4})",
    re.IGNORECASE,
)

# Lines that should be silently dropped (statement boilerplate / page chrome).
# Each entry is a regex that matches the WHOLE line (after .strip()).
_NOISE_PATTERNS = [
    re.compile(r"^continued$"),
    re.compile(r"^Page\d+of\d+$"),
    # pdfplumber outputs plain ASCII apostrophes for typographic curly ones
    re.compile(r"^Here'swhathappenedinyouraccount.*$"),
    re.compile(r"^Amounts.*$"),                                   # "Amountsdeducted Amountsadded"
    re.compile(r"^Date\s+Description.*$"),                        # column header line
    re.compile(r"^PrimaryChequingAccount#.*$"),                   # account header (also block start)
    re.compile(r"^Owners:$"),
    re.compile(r"^(MR|MRS|MISS|MS|DR)[A-Z'. ,]+$"),               # owner name lines (with optional trailing comma)
]

# Numeric tolerance for balance reconciliation. Beancount default precision
# is 2 dp for currency; we keep this tight to catch parse drift early.
_RECONCILE_TOLERANCE = Decimal("0.005")


# ---------------------------------------------------------------------------
# Year inference
# ---------------------------------------------------------------------------


def infer_statement_year_from_filename(filename: str) -> Optional[int]:
    """Look for a 4-digit year (20xx) in the filename. Return None if ambiguous."""
    matches = sorted({int(m.group(0)) for m in re.finditer(r"20\d{2}", filename)})
    if len(matches) == 1:
        return matches[0]
    # Multiple year tokens or none — operator must supply --statement-year.
    return None


# ---------------------------------------------------------------------------
# Pure parsing — easy to unit test (no pdfplumber required)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RawTxn:
    posting_date: date
    description: str
    amount: Decimal  # unsigned magnitude from the PDF column
    balance: Decimal  # running balance reported on the line
    raw_line: str


@dataclass(frozen=True)
class _ParseResult:
    opening_date: Optional[date]
    opening_balance: Optional[Decimal]
    raw_txns: list[_RawTxn] = field(default_factory=list)
    period_end_date: Optional[date] = None  # from "For the period ending ..."


def find_period_end_date(text: str) -> Optional[date]:
    """Return the period-ending date from a 'For the period ending ...' line.

    Searches the whole text (case-insensitive) for the first match.
    Returns None if not found — caller then falls back to filename year
    inference or the --statement-year CLI override.
    """
    m = _PERIOD_LINE_RE.search(text)
    if not m:
        return None
    month_word = m.group("month").lower().capitalize()
    return date(
        int(m.group("year")),
        _FULL_MONTHS[month_word],
        int(m.group("day")),
    )


def _is_noise(line: str) -> bool:
    return any(p.match(line) for p in _NOISE_PATTERNS)


def _to_decimal(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _derive_year(prior_month: Optional[int], txn_month: int, anchor_year: int) -> int:
    """Handle year-rollover within a single statement period.

    Statement periods can cross Dec→Jan. We walk transactions in observed
    order and start at ``anchor_year``. Whenever month decreases sharply
    (e.g. Dec→Jan), we bump the year forward.
    """
    if prior_month is None:
        return anchor_year
    if txn_month < prior_month - 6:  # heuristic for Dec → Jan wrap
        return anchor_year + 1
    return anchor_year


def parse_statement_text(text: str, *, anchor_year: int) -> _ParseResult:
    """Parse the pdfplumber text dump of one BMO chequing statement.

    Returns the opening balance + raw (unsigned) transactions + (optionally)
    the period-end date. Sign resolution and conversion to
    ExtractedTransaction happens in :func:`resolve_signs`.

    If the text contains a "For the period ending <date>" line, the year
    from that line overrides ``anchor_year`` (the period line is the
    authoritative source — filename inference is the fallback).

    Raises ``PreParserError`` if no opening balance line is found.
    """
    period_end_date = find_period_end_date(text)
    if period_end_date is not None:
        anchor_year = period_end_date.year

    opening_date: Optional[date] = None
    opening_balance: Optional[Decimal] = None
    raw_txns: list[_RawTxn] = []
    current_year = anchor_year
    prior_month: Optional[int] = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _is_noise(line):
            continue

        m = _OPENING_LINE_RE.match(line)
        if m:
            month = _MONTHS[m.group("date")]
            current_year = _derive_year(prior_month, month, anchor_year)
            opening_date = date(current_year, month, int(m.group("day")))
            opening_balance = _to_decimal(m.group("balance"))
            prior_month = month
            continue

        if _CLOSING_LINE_RE.match(line):
            # Closing TOTALS line doesn't carry the closing balance; we
            # derive that from the last transaction's running balance.
            continue

        m = _TXN_LINE_RE.match(line)
        if m:
            month = _MONTHS[m.group("date")]
            current_year = _derive_year(prior_month, month, anchor_year)
            posting_date = date(current_year, month, int(m.group("day")))
            raw_txns.append(
                _RawTxn(
                    posting_date=posting_date,
                    description=m.group("desc"),
                    amount=_to_decimal(m.group("amount")),
                    balance=_to_decimal(m.group("balance")),
                    raw_line=line,
                )
            )
            prior_month = month
            continue

        # Unmatched non-noise line → continuation of the previous transaction's
        # description. If there's no previous transaction, skip silently
        # (could be pre-opening-balance metadata we didn't blacklist).
        if raw_txns:
            last = raw_txns[-1]
            raw_txns[-1] = _RawTxn(
                posting_date=last.posting_date,
                description=f"{last.description} {line}",
                amount=last.amount,
                balance=last.balance,
                raw_line=f"{last.raw_line} {line}",
            )

    if opening_balance is None:
        raise PreParserError(
            "BMO statement parse: no 'Openingbalance' line found. "
            "PDF may use a layout this parser doesn't recognize, or "
            "pdfplumber text extraction failed silently."
        )

    return _ParseResult(
        opening_date=opening_date,
        opening_balance=opening_balance,
        raw_txns=raw_txns,
        period_end_date=period_end_date,
    )


def resolve_signs(
    parse_result: _ParseResult, *, currency: str
) -> tuple[list[ExtractedTransaction], Optional[Decimal], Optional[date]]:
    """Walk transactions in order, infer +/- from running balance.

    Returns (signed_transactions, closing_balance, closing_date).
    Raises ``PreParserError`` on any line whose amount doesn't reconcile —
    that's the self-validation guarantee.
    """
    if parse_result.opening_balance is None:
        raise PreParserError("resolve_signs: parse_result has no opening_balance")

    running = parse_result.opening_balance
    signed: list[ExtractedTransaction] = []

    for raw in parse_result.raw_txns:
        delta = raw.balance - running
        if abs(delta - raw.amount) <= _RECONCILE_TOLERANCE:
            signed_amount = raw.amount       # credit
        elif abs(delta + raw.amount) <= _RECONCILE_TOLERANCE:
            signed_amount = -raw.amount      # debit
        else:
            raise PreParserError(
                f"BMO line failed reconciliation at {raw.posting_date}: "
                f"running={running} amount={raw.amount} "
                f"new_balance={raw.balance} delta={delta} "
                f"(line: {raw.raw_line!r})"
            )
        signed.append(
            ExtractedTransaction(
                posting_date=raw.posting_date,
                description=raw.description,
                amount=signed_amount,
                currency=currency,
                raw_line=raw.raw_line,
            )
        )
        running = raw.balance

    closing_balance = parse_result.raw_txns[-1].balance if parse_result.raw_txns else None
    # Prefer the authoritative period-end date if present; otherwise fall
    # back to the last transaction's date. The closing balance assertion
    # in the importer is dated D+1 either way (so bean-check fires after
    # all postings on the closing day have settled).
    closing_date = parse_result.period_end_date or (
        parse_result.raw_txns[-1].posting_date if parse_result.raw_txns else None
    )
    return signed, closing_balance, closing_date


# ---------------------------------------------------------------------------
# PreParser — opens the PDF, dispatches to the pure parser
# ---------------------------------------------------------------------------


@dataclass
class BmoJointChequingPdfPreParser:
    """BMO joint chequing 4969 PDF importer.

    Uses pdfplumber to extract text from each page, concatenates, and
    feeds the result through ``parse_statement_text`` + ``resolve_signs``.

    The pdfplumber import is deferred so tests can patch it (and so the
    module is importable on a Mac dev box without the lib installed).
    """

    institution: str = INSTITUTION
    expected_account_suffix: str = ACCOUNT_NUMBER_SUFFIX
    statement_year: Optional[int] = None  # set by CLI; else inferred from filename

    def can_handle(self, path_or_bytes: bytes | str) -> bool:
        if not isinstance(path_or_bytes, str):
            return False
        p = Path(path_or_bytes)
        if p.suffix.lower() != ".pdf":
            return False
        # Cheaper to just trust the suffix here — full account-number
        # verification happens during extract() after we read the PDF.
        return True

    def extract(self, path_or_bytes: bytes | str) -> StatementExtract:
        if not isinstance(path_or_bytes, str):
            raise PreParserError("BMO PDF pre-parser requires a path, not bytes")

        path = Path(path_or_bytes)
        if not path.is_file():
            raise PreParserError(f"PDF not found: {path}")

        text = self._extract_pdf_text(path)

        # Year resolution priority (highest wins):
        #   1. --statement-year CLI override
        #   2. "For the period ending <date>" line in the PDF (authoritative)
        #   3. 4-digit year token in filename (best-effort)
        year = self.statement_year
        if year is None:
            period_end = find_period_end_date(text)
            if period_end is not None:
                year = period_end.year
        if year is None:
            year = infer_statement_year_from_filename(path.name)
        if year is None:
            raise PreParserError(
                f"could not infer statement year from {path.name!r} (no period "
                "line found in PDF, no 20xx in filename); pass --statement-year YYYY"
            )

        # Account-number verification: refuse to import if the PDF doesn't
        # contain our expected account suffix. Defense against operator
        # accidentally pointing this importer at the wrong account's PDF.
        if self.expected_account_suffix and self.expected_account_suffix not in text:
            raise PreParserError(
                f"PDF does not contain expected account suffix "
                f"{self.expected_account_suffix!r}; refusing to import. "
                "Are you sure this is the BMO joint chequing 4969 statement?"
            )

        parse_result = parse_statement_text(text, anchor_year=year)
        signed_txns, closing_balance, closing_date = resolve_signs(
            parse_result, currency=CURRENCY
        )

        return StatementExtract(
            transactions=signed_txns,
            opening_date=parse_result.opening_date,
            opening_balance=parse_result.opening_balance,
            closing_date=closing_date,
            closing_balance=closing_balance,
            statement_id=path.stem,
        )

    @staticmethod
    def _extract_pdf_text(path: Path) -> str:
        """Open the PDF via pdfplumber and concatenate all page text."""
        try:
            import pdfplumber  # type: ignore[import-not-found]
        except ImportError as exc:
            raise PreParserError(
                "pdfplumber not installed. In the sandbox it's baked into "
                "agent-finance:latest; on a dev box run "
                "`pip3 install --user pdfplumber`."
            ) from exc

        chunks: list[str] = []
        try:
            with pdfplumber.open(str(path)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    chunks.append(page_text)
        except Exception as exc:
            # pdfplumber raises pdfminer-specific exception types; we
            # wrap them so callers only need to catch PreParserError.
            raise PreParserError(f"pdfplumber failed to read {path}: {exc}") from exc
        return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Importer — pure rendering from StatementExtract
# ---------------------------------------------------------------------------


@dataclass
class BmoJointChequingImporter:
    """Render a BMO joint chequing StatementExtract into Beancount entries.

    Output order (sorted by posting_date in the ingest layer):
      D₀-1   pad      <source>  Equity:Opening-Balances        (if opening balance present)
      D₀     balance  <source>  <opening_balance> CAD          (asserts opening)
      D₁     transaction entry 1
      D₂     transaction entry 2
      ...
      Dₙ+1   balance  <source>  <closing_balance> CAD          (asserts closing; +1 day)
    """

    institution: str = INSTITUTION
    source_account: str = SOURCE_ACCOUNT
    currency: str = CURRENCY
    counter_account: str = COUNTER_ACCOUNT

    def render(self, extract: StatementExtract) -> list[BeancountEntry]:
        entries: list[BeancountEntry] = []

        if extract.opening_date is not None and extract.opening_balance is not None:
            entries.extend(
                render_pad_balance(
                    opening_date=extract.opening_date,
                    source_account=self.source_account,
                    opening_balance=extract.opening_balance,
                    currency=self.currency,
                )
            )

        for txn in extract.transactions:
            if txn.currency != self.currency:
                raise PreParserError(
                    f"txn currency {txn.currency} != source account currency {self.currency} "
                    f"for {self.institution}"
                )
            entries.append(
                render_simple_entry(
                    txn,
                    source_account=self.source_account,
                    counter_account=self.counter_account,
                    importer_slug=self.institution,
                )
            )

        if extract.closing_date is not None and extract.closing_balance is not None:
            entries.append(
                render_closing_balance_assertion(
                    closing_date=extract.closing_date,
                    source_account=self.source_account,
                    closing_balance=extract.closing_balance,
                    currency=self.currency,
                )
            )

        return entries


# Registry factories.

def build_bmo_joint_chequing_pre_parser() -> PreParser:
    return BmoJointChequingPdfPreParser()


def build_bmo_joint_chequing_importer() -> Importer:
    return BmoJointChequingImporter()
