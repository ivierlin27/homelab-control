"""BMO Cash Back Mastercard — PDF importer.

Statement layout differs significantly from BMO chequing:

  * Liability account (Beancount sign convention: balance is NEGATIVE
    because it represents money owed).
  * Two dates per transaction (trans date + posting date); we use
    posting date for the ledger entry.
  * Sign is explicit via a "CR" suffix on payment/credit amounts
    (charges have no suffix). No running-balance column — can't
    infer via reconciliation like the chequing parser does.
  * Period line is "PERIODCOVEREDBYTHISSTATEMENT\\n<start>-<end>"
    (not "For the period ending ..." like chequing).
  * Date format is "Oct.19" (with period after month abbrev).
  * Opening/closing balances appear in the summary block as
    "PreviousBalance,Oct.19,2021 $402.23" and
    "NewBalance,Nov.19,2021 $2,190.30".

Self-validation (parallel to chequing parser's running-balance check):
sum of signed txn amounts must equal (closing - opening) where both
balances are in Beancount-liability convention (negative). Mismatch
raises PreParserError — same loud-failure posture as chequing.
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
class BmoCashbackMcProfile:
    """Per-card configuration for BMO Cash Back Mastercard PDF imports.

    Only one card today (Kevin+Jennifer joint cashback MC, last-4 0706)
    but the profile pattern matches bmo_chequing_pdf so future BMO MC
    products plug in the same way.
    """

    slug: str
    source_account: str
    account_last4: str
    currency: str = CURRENCY


PROFILES: dict[str, BmoCashbackMcProfile] = {
    "bmo-cashback-mc-0706": BmoCashbackMcProfile(
        slug="bmo-cashback-mc-0706",
        source_account="Liabilities:CA:BMO:CreditCard:CashbackMC-Joint-0706",
        account_last4="0706",
    ),
}


# ---------------------------------------------------------------------------
# Regex / constants
# ---------------------------------------------------------------------------

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_MONTH_PATTERN = "|".join(_MONTHS.keys())

# Balance summary lines.
# "PreviousBalance,Oct.19,2021 $402.23"
# "NewBalance,Nov.19,2021 $2,190.30"
_PREV_BAL_RE = re.compile(
    rf"PreviousBalance,\s*(?P<month>{_MONTH_PATTERN})\.\s*"
    r"(?P<day>\d{1,2}),\s*(?P<year>\d{4})\s+\$?(?P<amount>[\d,]+\.\d{2})",
    re.IGNORECASE,
)
_NEW_BAL_RE = re.compile(
    rf"NewBalance,\s*(?P<month>{_MONTH_PATTERN})\.\s*"
    r"(?P<day>\d{1,2}),\s*(?P<year>\d{4})\s+\$?(?P<amount>[\d,]+\.\d{2})",
    re.IGNORECASE,
)

# Statement date line (fallback for closing date if NewBalance line missing).
# "StatementDate Nov.19,2021" or "StatementDate: Nov.19,2021"
_STMT_DATE_RE = re.compile(
    rf"StatementDate\s*:?\s*(?P<month>{_MONTH_PATTERN})\.\s*"
    r"(?P<day>\d{1,2}),\s*(?P<year>\d{4})",
    re.IGNORECASE,
)

# Period line — appears below the literal "PERIODCOVEREDBYTHISSTATEMENT" header.
# "Oct.20,2021-Nov.19,2021"
_PERIOD_RANGE_RE = re.compile(
    rf"(?P<m1>{_MONTH_PATTERN})\.\s*(?P<d1>\d{{1,2}}),\s*(?P<y1>\d{{4}})\s*-\s*"
    rf"(?P<m2>{_MONTH_PATTERN})\.\s*(?P<d2>\d{{1,2}}),\s*(?P<y2>\d{{4}})"
)

# Transaction line:
#   <trans-date> <posting-date> <description> <ref-no> <amount>[CR]
# where dates are "Oct.19" form. Description + ref-no are space-separated
# tokens; ref-no is a long digit string (10-12 digits). Amount has the
# usual decimal-comma format. CR suffix marks credits/payments.
_TXN_LINE_RE = re.compile(
    rf"^(?P<tm>{_MONTH_PATTERN})\.\s*(?P<td>\d{{1,2}})\s+"
    rf"(?P<pm>{_MONTH_PATTERN})\.\s*(?P<pd>\d{{1,2}})\s+"
    # Greedy desc anchored from the right by ref + amount. The ref token
    # accepts alphanumerics + hyphens (BMO uses both: pure-digit ref like
    # "004011680156" for purchases, alphanumeric like "S670159OBPP" for
    # internal transfers). 6-20 chars covers both.
    r"(?P<desc>.+)\s+"
    r"(?P<ref>[A-Za-z0-9]{6,20})\s+"
    r"(?P<amount>[\d,]+\.\d{2})(?P<cr>CR)?\s*$"
)

# Lines we deliberately drop. Many of these are page chrome,
# block headers, or summary/footer rows that look like txns
# to a naive matcher but aren't.
_NOISE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^BMOCashBackMastercard\b",
        r"^StatementDate\b",
        r"^CardNumber\b",
        r"^CustomerName\b",
        r"^PERIODCOVEREDBYTHISSTATEMENT$",
        r"^TRANS\b.*POSTING\b",
        r"^DATE\s+DATE\s+DESCRIPTION",
        r"^Continuedon\s*page\s*\d+",
        r"^Page\s*\d+\s*of\s*\d+",
        r"^INTEREST\b.*ANNUAL",
        r"^CHARGES",
        r"^Purchases\s+\d+\.\d{2}",
        r"^CashAdvances\d?\s+\d+\.\d{2}",
        r"^Bonusreward\b",
        r"^GroceryBonus\b",
        r"^RecurringBillBonus\b",
        r"^PromotionalOffers\b",
        r"^EstimatedTimeToRepay\b",
        # Summary-block lines that contain dollar amounts but aren't txns.
        r"^Purchasesandothercharges\b",
        r"^TotalInterestCharges\b",
        r"^PaymentsandCredits\b",
        r"^YOURREWARDS\b",
        r"^RewardsEarned\b",
        r"^Bonusrewardsearned\b",
        r"^Rewardsadjusted\b",
        r"^RewardsRedeemed\b",
        r"^Totalrewardsearned\b",
        r"^Rewardsbalanceyeartodate\b",
        r"^Redeemnowat\b",
        r"^MinimumPaymentDue\b",
        r"^PaymentDueDate\b",
        r"^YourCreditLimit\b",
        r"^YourAvailableCredit\b",
        r"^AmountOverCreditLimit\b",
        r"^NewBalance\b",
        r"^PreviousBalance\b",
        r"^Fees\b\s+\d+\.\d{2}",
        # Long boilerplate paragraphs.
        r"^Importantinformation\b",
        r"^ImportantPaymentInformation",
        r"^Interestchargesand",
        r"^Skipthepublic",
        r"^Trade-marks\b",
        r"^TM/®",
        r"^®[IiC*]\s+Trademarks",
        r"^®\+\+",
        r"^Registrationnumbers",
        r"^GST-R\d+",
        r"^AmemberofBMO",
        r"^BMOBANKOFMONTREAL",
        r"^P\.O\.\s*BOX",
        r"^STATION",
        r"^MONTREAL",
        r"^LANGLEY",  # mailing address line (vs txn line which starts with date)
        r"^MR\s",
        r"^MRS\s",
        r"^MISS\s",
        r"^Owners?:",
        r"^Amountyou'repaying",
        r"^\d{16}\s+\d+\s+\d+$",  # MICR-style coupon line
    ]
]


def _is_noise(line: str) -> bool:
    return any(p.match(line) for p in _NOISE_PATTERNS)


def _to_decimal(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


# ---------------------------------------------------------------------------
# Header / period parsers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Summary:
    """Header-summary block: previous + new balance with their dates."""

    previous_balance: Optional[Decimal]
    previous_date: Optional[date]
    new_balance: Optional[Decimal]
    new_date: Optional[date]
    period_start: Optional[date]
    period_end: Optional[date]
    statement_date: Optional[date]


def parse_summary(text: str) -> _Summary:
    """Pull the PreviousBalance / NewBalance / Period lines from the header."""
    def _parse_date(month: str, day: str, year: str) -> date:
        return date(int(year), _MONTHS[month.capitalize()], int(day))

    previous_balance = previous_date = None
    new_balance = new_date = None
    period_start = period_end = None
    statement_date = None

    m = _PREV_BAL_RE.search(text)
    if m:
        previous_balance = _to_decimal(m.group("amount"))
        previous_date = _parse_date(m.group("month"), m.group("day"), m.group("year"))

    m = _NEW_BAL_RE.search(text)
    if m:
        new_balance = _to_decimal(m.group("amount"))
        new_date = _parse_date(m.group("month"), m.group("day"), m.group("year"))

    # Period range — first occurrence of MMM.DD,YYYY-MMM.DD,YYYY after the
    # PERIODCOVEREDBYTHISSTATEMENT marker.
    period_marker = re.search(
        r"PERIODCOVEREDBYTHISSTATEMENT", text, re.IGNORECASE,
    )
    search_from = period_marker.end() if period_marker else 0
    m = _PERIOD_RANGE_RE.search(text, search_from)
    if m:
        period_start = _parse_date(m.group("m1"), m.group("d1"), m.group("y1"))
        period_end = _parse_date(m.group("m2"), m.group("d2"), m.group("y2"))

    m = _STMT_DATE_RE.search(text)
    if m:
        statement_date = _parse_date(m.group("month"), m.group("day"), m.group("year"))

    return _Summary(
        previous_balance=previous_balance,
        previous_date=previous_date,
        new_balance=new_balance,
        new_date=new_date,
        period_start=period_start,
        period_end=period_end,
        statement_date=statement_date,
    )


# ---------------------------------------------------------------------------
# Transaction parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RawTxn:
    posting_date: date
    description: str
    amount: Decimal       # POSITIVE — sign assigned later from cr_flag
    cr_flag: bool         # True if "CR" suffix → payment/credit (decreases liability)
    raw_line: str


@dataclass
class _ParseResult:
    summary: _Summary
    raw_txns: list[_RawTxn] = field(default_factory=list)


def _year_for_txn(
    txn_month: int,
    *,
    period_end_month: Optional[int],
    period_end_year: Optional[int],
) -> int:
    """Pick the calendar year for a transaction month, given the period end.

    Same rule as the chequing parser's period-end mode: txn months past
    period_end belong to period_end_year - 1. Mastercard statements span
    at most ~1 month so this is exact.
    """
    if period_end_month is None or period_end_year is None:
        raise PreParserError(
            "BMO MC parser requires a period end date; none found in PDF"
        )
    if txn_month > period_end_month:
        return period_end_year - 1
    return period_end_year


def parse_statement_text(text: str) -> _ParseResult:
    """Parse a BMO Cash Back Mastercard pdfplumber text dump."""
    summary = parse_summary(text)

    # We need at minimum a period end to date transactions correctly.
    period_end = summary.period_end or summary.new_date or summary.statement_date
    if period_end is None:
        raise PreParserError(
            "BMO MC PDF missing period end (no PERIODCOVEREDBYTHISSTATEMENT, "
            "NewBalance, or StatementDate line found)"
        )
    period_end_month = period_end.month
    period_end_year = period_end.year

    raw_txns: list[_RawTxn] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _is_noise(line):
            continue
        m = _TXN_LINE_RE.match(line)
        if not m:
            continue
        pm = _MONTHS[m.group("pm").capitalize()]
        pd = int(m.group("pd"))
        year = _year_for_txn(
            pm, period_end_month=period_end_month, period_end_year=period_end_year,
        )
        raw_txns.append(
            _RawTxn(
                posting_date=date(year, pm, pd),
                description=m.group("desc").strip(),
                amount=_to_decimal(m.group("amount")),
                cr_flag=bool(m.group("cr")),
                raw_line=line,
            )
        )

    return _ParseResult(summary=summary, raw_txns=raw_txns)


def resolve_signs(
    parse_result: _ParseResult, *, currency: str
) -> list[ExtractedTransaction]:
    """Apply liability sign convention + self-validate against summary.

    Sign rule (Beancount liability — balance is stored as negative):
      * Charge (no CR): liability delta = -amount  (we owe more)
      * Payment (CR):  liability delta = +amount  (we owe less)

    Self-validation: sum of signed deltas + ledger_opening must equal
    ledger_closing, where ledger_opening = -summary.previous_balance and
    ledger_closing = -summary.new_balance (both negative). Mismatch
    raises PreParserError loudly.
    """
    summary = parse_result.summary
    if summary.previous_balance is None or summary.new_balance is None:
        raise PreParserError(
            "BMO MC parser requires both PreviousBalance and NewBalance lines"
        )

    signed: list[ExtractedTransaction] = []
    delta_sum = Decimal("0")
    for raw in parse_result.raw_txns:
        signed_amount = raw.amount if raw.cr_flag else -raw.amount
        delta_sum += signed_amount
        signed.append(
            ExtractedTransaction(
                posting_date=raw.posting_date,
                description=raw.description,
                amount=signed_amount,
                currency=currency,
                raw_line=raw.raw_line,
            )
        )

    ledger_opening = -summary.previous_balance     # negative
    ledger_closing = -summary.new_balance          # negative
    expected_delta = ledger_closing - ledger_opening
    if abs(delta_sum - expected_delta) > _RECONCILE_TOLERANCE:
        raise PreParserError(
            f"BMO MC reconciliation failed: ledger_opening={ledger_opening} "
            f"sum_of_txns={delta_sum} ledger_closing={ledger_closing} "
            f"expected_delta={expected_delta} (diff={delta_sum - expected_delta}); "
            "parser likely missed a transaction or got a sign wrong"
        )

    return signed


# ---------------------------------------------------------------------------
# PreParser
# ---------------------------------------------------------------------------


@dataclass
class BmoCashbackMcPdfPreParser:
    """Profile-driven BMO Cash Back Mastercard PDF pre-parser."""

    profile: BmoCashbackMcProfile

    @property
    def institution(self) -> str:
        return self.profile.slug

    def can_handle(self, path_or_bytes: bytes | str) -> bool:
        if not isinstance(path_or_bytes, str):
            return False
        return Path(path_or_bytes).suffix.lower() == ".pdf"

    def extract(self, path_or_bytes: bytes | str) -> StatementExtract:
        if not isinstance(path_or_bytes, str):
            raise PreParserError("BMO MC pre-parser requires a path, not bytes")
        path = Path(path_or_bytes)
        if not path.is_file():
            raise PreParserError(f"PDF not found: {path}")

        text = self._extract_pdf_text(path)

        # Account verification — same multi-format matching as chequing.
        last4 = self.profile.account_last4
        candidates = [last4, f"{last4[0]}-{last4[1:]}", f"{last4[:2]}-{last4[2:]}"]
        if not any(c in text for c in candidates):
            raise PreParserError(
                f"PDF does not contain a digit pattern matching account "
                f"last-4 {last4!r}; refusing to import. Are you sure this "
                f"PDF belongs to {self.profile.source_account}?"
            )

        parse_result = parse_statement_text(text)
        signed_txns = resolve_signs(parse_result, currency=self.profile.currency)
        summary = parse_result.summary

        # opening/closing in Beancount liability convention (negative).
        opening_balance = -summary.previous_balance if summary.previous_balance is not None else None
        closing_balance = -summary.new_balance if summary.new_balance is not None else None
        opening_date = summary.previous_date or summary.period_start
        closing_date = summary.new_date or summary.period_end

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
class BmoCashbackMcImporter:
    """Render a BMO Cash Back MC StatementExtract into Beancount entries."""

    profile: BmoCashbackMcProfile
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


def _make_pre_parser_factory(profile: BmoCashbackMcProfile):
    def build() -> PreParser:
        return BmoCashbackMcPdfPreParser(profile=profile)
    return build


def _make_importer_factory(profile: BmoCashbackMcProfile):
    def build() -> Importer:
        return BmoCashbackMcImporter(profile=profile)
    return build


PRE_PARSER_FACTORIES = {
    slug: _make_pre_parser_factory(profile) for slug, profile in PROFILES.items()
}
IMPORTER_FACTORIES = {
    slug: _make_importer_factory(profile) for slug, profile in PROFILES.items()
}
