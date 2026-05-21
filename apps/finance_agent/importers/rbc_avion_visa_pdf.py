"""RBC Avion Visa Platinum — PDF importer.

Layout differs substantially from BMO Cash Back MC despite both being CC
liability accounts:

  * Two-column page layout. The left column has the txn list; the right
    column has customer service info, points balance, payment details,
    interest rates. pdfplumber's text extraction concatenates left + right
    on the same line ("APR10 APR11 KI-TEAHOUSECAFE... $69.56 Minimumpayment
    $10.00"). We anchor on the LEFTMOST $X.XX amount via non-greedy desc;
    everything after is ignored.

  * Two-line txn structure: the txn proper sits on one line; the 17-digit
    reference number is on the next line by itself. We capture only the
    txn line — refs are nice-to-have, not needed for the ledger.

  * Sign convention is explicit (no "CR" suffix like BMO MC). Payments
    show as "-$2,650.09" with a literal minus in front of the dollar sign.
    Charges are bare "$28.21". We negate the parsed amount to get the
    Beancount liability delta:
      * charge $28.21    → -28.21 (we owe more — balance more negative)
      * payment -$2650.09 → +2650.09 (we owe less)

  * Date format "APR07" — 3 letters + digits, no separator. Different
    from BMO's "Apr.07" / "Apr. 7" forms.

  * Period line: "STATEMENTFROMAPR09TOMAY08,2024". Year is on the END
    only — start year may differ on Dec→Jan rollover statements.

  * Balance line variants:
      "PREVIOUSACCOUNTBALANCE $0.00"
      "NEW BALANCE $1,089.09"       (with space)
      "TOTALACCOUNTBALANCE $1,089.09" (also appears — same value)

Self-validation (same posture as MC): sum of signed deltas must equal
(-NEW BALANCE) - (-PREVIOUS BALANCE). Mismatch raises PreParserError.
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

CURRENCY = "CAD"
COUNTER_ACCOUNT = "Expenses:Uncategorized"
_RECONCILE_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class RbcAvionProfile:
    """Per-card configuration for RBC Avion Visa PDF imports."""

    slug: str
    source_account: str
    account_last4: str
    currency: str = CURRENCY


PROFILES: dict[str, RbcAvionProfile] = {
    "rbc-avion-1847": RbcAvionProfile(
        slug="rbc-avion-1847",
        source_account="Liabilities:CA:RBC:CreditCard:AvionVisaPlatinum-Joint-1847",
        account_last4="1847",
    ),
}


# ---------------------------------------------------------------------------
# Regex / constants
# ---------------------------------------------------------------------------

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_MONTH_PATTERN = "|".join(_MONTHS.keys())

# Header balance lines.
# Old RBC layout has labels concatenated (no spaces); newer too. The
# `\s*` between label words tolerates both forms defensively.
_PREV_BAL_RE = re.compile(
    r"PREVIOUS\s*ACCOUNT\s*BALANCE\s+\$?(?P<amount>-?[\d,]+\.\d{2})",
    re.IGNORECASE,
)
# "NEW BALANCE" (with space) AND "NEWBALANCE" (no space) appear on the same
# statement — the spaced form is on page 1 in the summary box, the joined
# form sometimes appears on the payment slip.
_NEW_BAL_RE = re.compile(
    r"\bNEW\s*BALANCE\s+\$?(?P<amount>-?[\d,]+\.\d{2})",
    re.IGNORECASE,
)
# Fallback: "TOTALACCOUNTBALANCE $X" should equal NEW BALANCE — use only
# if NEW BALANCE is missing.
_TOTAL_BAL_RE = re.compile(
    r"TOTAL\s*ACCOUNT\s*BALANCE\s+\$?(?P<amount>-?[\d,]+\.\d{2})",
    re.IGNORECASE,
)

# Period line — two formats seen in real RBC statements:
#   Old (Oct 2022-Dec 2022): "STATEMENTFROMSEP21TOOCT11,2022"
#     — year only on the END date; start year inferred via Dec→Jan rollover
#   New (Jan 2023+):         "STATEMENTFROMDEC09,2022TOJAN09,2023"
#     — year on BOTH dates, so no inference needed
# Regex captures optional start-year via `(?:,?\s*(?P<y1>\d{4}))?`.
_PERIOD_RE = re.compile(
    rf"STATEMENT\s*FROM\s*(?P<m1>{_MONTH_PATTERN})\s*(?P<d1>\d{{1,2}})"
    rf"(?:,?\s*(?P<y1>\d{{4}}))?"
    rf"\s*TO\s*(?P<m2>{_MONTH_PATTERN})\s*(?P<d2>\d{{1,2}}),?\s*(?P<year>\d{{4}})",
    re.IGNORECASE,
)

# Transaction line:
#   <tm><td> <pm><pd> <desc-concat> [-]$<amount> [trailing right-column noise]
# Date format is "APR07" (no separator). The amount is anchored to the
# LEFTMOST $X.XX via non-greedy desc; trailing content (right-column page
# chrome like "Minimumpayment $10.00") is captured by the optional tail
# group and discarded.
_TXN_LINE_RE = re.compile(
    rf"^(?P<tm>{_MONTH_PATTERN})\s*(?P<td>\d{{1,2}})\s+"
    rf"(?P<pm>{_MONTH_PATTERN})\s*(?P<pd>\d{{1,2}})\s+"
    r"(?P<desc>.+?)\s+(?P<neg>-)?\$(?P<amount>[\d,]+\.\d{2})"
    r"(?:\s.*)?$",
    re.IGNORECASE,
)


def _to_decimal(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


# ---------------------------------------------------------------------------
# Header parsers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Summary:
    previous_balance: Optional[Decimal]
    new_balance: Optional[Decimal]
    period_start_month: Optional[int]
    period_start_day: Optional[int]
    period_start_year: Optional[int]   # only set on Jan-2023+ format
    period_end_month: Optional[int]
    period_end_day: Optional[int]
    period_end_year: Optional[int]

    @property
    def period_end_date(self) -> Optional[date]:
        if self.period_end_year and self.period_end_month and self.period_end_day:
            return date(self.period_end_year, self.period_end_month, self.period_end_day)
        return None

    @property
    def period_start_date(self) -> Optional[date]:
        """Start date — uses explicit start_year if present (Jan 2023+
        format), otherwise infers from end year + Dec→Jan rollover."""
        if not (self.period_start_month and self.period_start_day):
            return None
        if self.period_start_year is not None:
            return date(self.period_start_year, self.period_start_month, self.period_start_day)
        if not (self.period_end_month and self.period_end_year):
            return None
        year = self.period_end_year
        if self.period_start_month > self.period_end_month:
            year -= 1
        return date(year, self.period_start_month, self.period_start_day)


def parse_summary(text: str) -> _Summary:
    """Extract balance + period info from the statement header."""
    previous_balance = new_balance = None
    period_start_m = period_start_d = period_start_y = None
    period_end_m = period_end_d = period_end_y = None

    m = _PREV_BAL_RE.search(text)
    if m:
        previous_balance = _to_decimal(m.group("amount"))

    m = _NEW_BAL_RE.search(text)
    if m:
        new_balance = _to_decimal(m.group("amount"))
    else:
        m = _TOTAL_BAL_RE.search(text)
        if m:
            new_balance = _to_decimal(m.group("amount"))

    m = _PERIOD_RE.search(text)
    if m:
        period_start_m = _MONTHS[m.group("m1").upper()]
        period_start_d = int(m.group("d1"))
        if m.group("y1"):
            period_start_y = int(m.group("y1"))
        period_end_m = _MONTHS[m.group("m2").upper()]
        period_end_d = int(m.group("d2"))
        period_end_y = int(m.group("year"))

    return _Summary(
        previous_balance=previous_balance,
        new_balance=new_balance,
        period_start_month=period_start_m,
        period_start_day=period_start_d,
        period_start_year=period_start_y,
        period_end_month=period_end_m,
        period_end_day=period_end_d,
        period_end_year=period_end_y,
    )


# ---------------------------------------------------------------------------
# Transaction parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RawTxn:
    posting_date: date
    description: str
    pdf_amount: Decimal       # signed as it appears in PDF (negative = payment)
    raw_line: str


@dataclass
class _ParseResult:
    summary: _Summary
    raw_txns: list[_RawTxn] = field(default_factory=list)


def _year_for_txn(
    txn_month: int,
    *,
    period_end_month: int,
    period_end_year: int,
    period_start_month: Optional[int] = None,
    period_start_year: Optional[int] = None,
) -> int:
    """Pick the calendar year for a txn month.

    If both period start year AND end year are known (Jan 2023+ RBC
    format), use the unambiguous rule: month matches start → start year;
    otherwise → end year. Falls back to the old heuristic (txn_month >
    period_end_month → end_year - 1) when start year is not available.
    """
    if period_start_year is not None and period_start_month is not None:
        if txn_month == period_start_month and period_start_month != period_end_month:
            return period_start_year
        if txn_month == period_end_month:
            return period_end_year
        # Month is neither start nor end (rare — only happens for very
        # long statements). Use the closer of the two by ISO ordering.
        if period_start_year == period_end_year:
            return period_end_year
        return period_end_year if txn_month <= period_end_month else period_start_year
    if txn_month > period_end_month:
        return period_end_year - 1
    return period_end_year


def parse_statement_text(text: str) -> _ParseResult:
    """Parse an RBC Avion pdfplumber text dump."""
    summary = parse_summary(text)
    if summary.period_end_year is None or summary.period_end_month is None:
        raise PreParserError(
            "RBC Avion parser requires STATEMENT FROM ... TO ..., <year> "
            "line; none found in PDF"
        )
    end_m = summary.period_end_month
    end_y = summary.period_end_year

    raw_txns: list[_RawTxn] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _TXN_LINE_RE.match(line)
        if not m:
            continue
        pm = _MONTHS[m.group("pm").upper()]
        pd = int(m.group("pd"))
        year = _year_for_txn(
            pm,
            period_end_month=end_m,
            period_end_year=end_y,
            period_start_month=summary.period_start_month,
            period_start_year=summary.period_start_year,
        )
        amount = _to_decimal(m.group("amount"))
        if m.group("neg"):
            amount = -amount
        raw_txns.append(
            _RawTxn(
                posting_date=date(year, pm, pd),
                description=m.group("desc").strip(),
                pdf_amount=amount,
                raw_line=line,
            )
        )

    return _ParseResult(summary=summary, raw_txns=raw_txns)


def resolve_signs(
    parse_result: _ParseResult, *, currency: str
) -> list[ExtractedTransaction]:
    """Convert PDF-sign amounts to Beancount liability deltas + self-validate.

    PDF sign: charge=positive, payment=negative.
    Liability delta: charge=negative (owe more), payment=positive (owe less).
    So liability_delta = -pdf_amount.

    Reconciliation: sum of liability_deltas must equal ledger_closing -
    ledger_opening (both negative in Beancount liability convention).
    """
    summary = parse_result.summary
    if summary.previous_balance is None or summary.new_balance is None:
        raise PreParserError(
            "RBC Avion parser requires both PREVIOUSACCOUNTBALANCE and "
            "NEW BALANCE lines"
        )

    signed: list[ExtractedTransaction] = []
    delta_sum = Decimal("0")
    for raw in parse_result.raw_txns:
        liability_delta = -raw.pdf_amount
        delta_sum += liability_delta
        signed.append(
            ExtractedTransaction(
                posting_date=raw.posting_date,
                description=raw.description,
                amount=liability_delta,
                currency=currency,
                raw_line=raw.raw_line,
            )
        )

    ledger_opening = -summary.previous_balance
    ledger_closing = -summary.new_balance
    expected_delta = ledger_closing - ledger_opening
    if abs(delta_sum - expected_delta) > _RECONCILE_TOLERANCE:
        raise PreParserError(
            f"RBC Avion reconciliation failed: ledger_opening={ledger_opening} "
            f"sum_of_txns={delta_sum} ledger_closing={ledger_closing} "
            f"expected_delta={expected_delta} (diff={delta_sum - expected_delta}); "
            "parser likely missed a transaction or got a sign wrong"
        )

    return signed


# ---------------------------------------------------------------------------
# PreParser
# ---------------------------------------------------------------------------


@dataclass
class RbcAvionVisaPdfPreParser:
    """Profile-driven RBC Avion Visa PDF pre-parser."""

    profile: RbcAvionProfile

    @property
    def institution(self) -> str:
        return self.profile.slug

    def can_handle(self, path_or_bytes: bytes | str) -> bool:
        if not isinstance(path_or_bytes, str):
            return False
        return Path(path_or_bytes).suffix.lower() == ".pdf"

    def extract(self, path_or_bytes: bytes | str) -> StatementExtract:
        if not isinstance(path_or_bytes, str):
            raise PreParserError("RBC Avion pre-parser requires a path, not bytes")
        path = Path(path_or_bytes)
        if not path.is_file():
            raise PreParserError(f"PDF not found: {path}")

        text = self._extract_pdf_text(path)

        # Account verification — last-4 appears in the masked card number
        # like "451409******1847". Defensive match against just the digits.
        if self.profile.account_last4 not in text:
            raise PreParserError(
                f"PDF does not contain account last-4 {self.profile.account_last4!r}; "
                f"refusing to import. Are you sure this PDF belongs to "
                f"{self.profile.source_account}?"
            )

        parse_result = parse_statement_text(text)
        signed_txns = resolve_signs(parse_result, currency=self.profile.currency)
        summary = parse_result.summary

        # See bmo_cashback_mc_pdf.py for the rationale on opening_date:
        # PreviousAccountBalance is end-of-prior-period, so the assertion
        # belongs at the first day of the new period.
        opening_balance = -summary.previous_balance if summary.previous_balance is not None else None
        closing_balance = -summary.new_balance if summary.new_balance is not None else None
        opening_date = summary.period_start_date
        closing_date = summary.period_end_date

        return StatementExtract(
            transactions=signed_txns,
            opening_date=opening_date,
            opening_balance=opening_balance,
            closing_date=closing_date,
            closing_balance=closing_balance,
            statement_id=path.stem,
        )

    @staticmethod
    def _extract_pdf_text(path: Path) -> str:
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
            raise PreParserError(f"pdfplumber failed on {path}: {exc}") from exc
        return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------


@dataclass
class RbcAvionVisaImporter:
    """Render an RBC Avion StatementExtract into Beancount entries."""

    profile: RbcAvionProfile
    counter_account: str = COUNTER_ACCOUNT

    @property
    def institution(self) -> str:
        return self.profile.slug

    @property
    def source_account(self) -> str:
        return self.profile.source_account

    @property
    def currency(self) -> str:
        return self.profile.currency

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


# ---------------------------------------------------------------------------
# Registry factories
# ---------------------------------------------------------------------------


def _make_pre_parser_factory(profile: RbcAvionProfile):
    def build() -> PreParser:
        return RbcAvionVisaPdfPreParser(profile=profile)
    return build


def _make_importer_factory(profile: RbcAvionProfile):
    def build() -> Importer:
        return RbcAvionVisaImporter(profile=profile)
    return build


PRE_PARSER_FACTORIES = {
    slug: _make_pre_parser_factory(profile) for slug, profile in PROFILES.items()
}
IMPORTER_FACTORIES = {
    slug: _make_importer_factory(profile) for slug, profile in PROFILES.items()
}
