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
# BMO has shipped at least two template generations on the Cash Back MC and
# is inconsistent within each. To handle both:
#
#   Old layout (Sep 2021 - Jan 2023):
#     PreviousBalance,Oct.19,2021 $402.23
#     NewBalance,Nov.19,2021 $2,190.30
#     PERIODCOVEREDBYTHISSTATEMENT
#     Oct.20,2021-Nov.19,2021
#     StatementDate Nov.19,2021      (or "May19,2022" without the period)
#     CardNumber 5191230213430706
#
#   New layout (Feb 2023+):
#     Previous balance, Jan. 19, 2023 $899.74 Statement date Feb. 19, 2023
#     Total balance $722.04                       (no date, paired with stmt date)
#     Statement period Jan. 20, 2023 - Feb. 19, 2023
#     Card number XXXX XXXX XXXX 0706
#     <txns from card 0706 section>
#     Card number: XXXX XXXX XXXX 4004 JENNIFER MOORE
#     <txns from card 4004 section — same source account, joint billing>
#
# The `\s*` between label words + `\.?\s*` in every date pattern absorb both
# the no-spaces (old) and spaced (new) variants in a single regex.
# BMO also ships a "Previous total balance, ..." variant (seen Oct 2023+)
# — extra word between "Previous" and "balance". Tolerate optional middle
# words via `(?:\w+\s+)*`.
_PREV_BAL_RE = re.compile(
    rf"Previous\s*(?:\w+\s+)*Balance,\s*(?P<month>{_MONTH_PATTERN})\.?\s*"
    r"(?P<day>\d{1,2}),\s*(?P<year>\d{4})\s+\$?(?P<amount>[\d,]+\.\d{2})",
    re.IGNORECASE,
)
_NEW_BAL_RE = re.compile(
    rf"New\s*Balance,\s*(?P<month>{_MONTH_PATTERN})\.?\s*"
    r"(?P<day>\d{1,2}),\s*(?P<year>\d{4})\s+\$?(?P<amount>[\d,]+\.\d{2})",
    re.IGNORECASE,
)

# New-template fallback: "Total balance $722.04" with no date — closing date
# must be sourced from the Statement-date line instead.
_TOTAL_BAL_RE = re.compile(
    r"Total\s+balance\s+\$?(?P<amount>[\d,]+\.\d{2})",
    re.IGNORECASE,
)

# Statement date line. Old: "StatementDate Nov.19,2021". New:
# "Statement date Feb. 19, 2023".
_STMT_DATE_RE = re.compile(
    rf"Statement\s*date\s*:?\s*(?P<month>{_MONTH_PATTERN})\.?\s*"
    r"(?P<day>\d{1,2}),\s*(?P<year>\d{4})",
    re.IGNORECASE,
)

# Period range. Old: bare "Oct.20,2021-Nov.19,2021" under the
# "PERIODCOVEREDBYTHISSTATEMENT" header. New: "Statement period
# Jan. 20, 2023 - Feb. 19, 2023" inline. Regex matches both anywhere
# in the text; parse_summary scopes the search to the relevant anchor.
_PERIOD_RANGE_RE = re.compile(
    rf"(?P<m1>{_MONTH_PATTERN})\.?\s*(?P<d1>\d{{1,2}}),\s*(?P<y1>\d{{4}})\s*-\s*"
    rf"(?P<m2>{_MONTH_PATTERN})\.?\s*(?P<d2>\d{{1,2}}),\s*(?P<y2>\d{{4}})"
)
_PERIOD_ANCHOR_RE = re.compile(
    r"PERIODCOVEREDBYTHISSTATEMENT|Statement\s*period",
    re.IGNORECASE,
)

# Transaction line:
#   <trans-date> <posting-date> <description> <ref-no> <amount>[CR]
# where dates are "Oct.19" form. Description + ref-no are space-separated
# tokens; ref-no is a long digit string (10-12 digits). Amount has the
# usual decimal-comma format. CR suffix marks credits/payments.
_TXN_LINE_RE = re.compile(
    rf"^(?P<tm>{_MONTH_PATTERN})\.?\s*(?P<td>\d{{1,2}})\s+"
    rf"(?P<pm>{_MONTH_PATTERN})\.?\s*(?P<pd>\d{{1,2}})\s+"
    # Non-greedy desc anchored from the right by optional-ref + amount.
    # Ref is alphanumeric 6-20 chars REQUIRING at least one digit (lookahead
    # `(?=[A-Za-z0-9]*\d)`) — without that constraint, pure-letter tokens
    # like "ADVANCES" or "PURCHASES" would be mis-attributed as ref on
    # system-posted lines ("INTEREST ADVANCES 0.71"). With it, INTEREST
    # ADVANCES correctly captures into desc and ref stays empty.
    # CR suffix may have an optional space before it (new template:
    # "899.74 CR"; old template: "402.23CR").
    r"(?P<desc>.+?)"
    r"\s+(?:(?P<ref>(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,20})\s+)?"
    r"(?P<amount>[\d,]+\.\d{2})\s*(?P<cr>CR)?\s*$"
)

# Lines we deliberately drop. The TXN regex requires a line to START with a
# month abbreviation + day, so most noise (starting with English words like
# "Total", "Card", "Statement", etc) never even reaches this filter — but
# we keep an explicit allow-list of label prefixes anyway so any future
# tightening of _TXN_LINE_RE doesn't introduce silent false-positives.
#
# Tokens that vary across templates use `\s*` between words (e.g.
# `BMO\s*CashBack` matches both "BMOCashBackMastercard" and
# "BMO CashBack Mastercard").
_NOISE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        # Page / template chrome
        r"^BMO\s*CashBack\s*Mastercard\b",
        r"^Statement\s*Date\b",
        r"^Statement\s*period\b",
        r"^Card\s*Number\b",
        r"^Card\s*number\b",
        r"^Customer\s*Name\b",
        r"^Summary\s+of\b",
        r"^PERIODCOVEREDBYTHISSTATEMENT$",
        r"^TRANS\b.*POSTING\b",
        r"^DATE\s+DATE\s+DESCRIPTION",
        r"^Continued\s*on\s*page\s*\d+",
        r"^Page\s*\d+\s*of\s*\d+",
        # Interest rate tables
        r"^INTEREST\b.*ANNUAL",
        r"^CHARGES",
        r"^Purchases\s+\d+\.\d{2}",
        r"^Cash\s*Advances\d?\s+\d+\.\d{2}",
        # Rewards block
        r"^Bonus\s*reward\b",
        r"^Grocery\s*Bonus\b",
        r"^Groceries\b",
        r"^Recurring\s*[Bb]ill\b",
        r"^Promotional\s*Offers\b",
        r"^Estimated\s*Time\s*[Tt]o\s*Repay\b",
        r"^Subtotal\s+bonus\b",
        # Summary-block lines that contain dollar amounts but aren't txns
        r"^Purchases\s*and\s*other\s*charges\b",
        r"^Total\s*Interest\s*Charges\b",
        r"^Total\s+interest\s+charges\b",
        r"^Payments\s*and\s*[Cc]redits\b",
        r"^YOUR\s*REWARDS\b",
        r"^Rewards\s*Earned\b",
        r"^Cashback\s+earned\b",
        r"^Bonus\s+Cashback\b",
        r"^Bonus\s*rewards\s*earned\b",
        r"^Rewards\s*adjusted\b",
        r"^Cashback\s+adjusted\b",
        r"^Rewards\s*Redeemed\b",
        r"^Cashback\s+redee?med\b",
        r"^Total\s*rewards\s*earned\b",
        r"^Total\s+Cashback\s+earned\b",
        r"^Rewards\s*balance\s*year\s*to\s*date\b",
        r"^Cashback\s+balance\s+year\s+to\s+date\b",
        r"^Redeem\s*now\s*at\b",
        r"^Minimum\s*payment\s*due\b",
        r"^Payment\s*[Dd]ue\s*[Dd]ate\b",
        r"^Your\s*[Cc]redit\s*[Ll]imit\b",
        r"^Your\s*[Aa]vailable\s*[Cc]redit\b",
        r"^Amount\s*[Oo]ver\s*[Cc]redit\s*[Ll]imit\b",
        r"^New\s*Balance\b",
        r"^Previous\s*Balance\b",
        r"^Total\s+balance\b",
        r"^Balance\s+due\b",
        r"^Fees?\b\s+\d+\.\d{2}",
        r"^Includes\s+any\s+installment\b",
        r"^plan\s+section\b",
        # Long boilerplate paragraphs
        r"^Important\s*[Ii]nformation\b",
        r"^Important\s*Payment\s*Information",
        r"^Interest\s*charges\s*and",
        r"^Skip\s*the\s*public",
        r"^Trade-marks\b",
        r"^TM/®",
        r"^®[IiC*]\s+Trademarks",
        r"^®\+\+",
        r"^Registration\s*numbers",
        r"^GST-R\d+",
        r"^A\s*member\s*of\s*BMO",
        r"^BMO\s*BANK\s*OF\s*MONTREAL",
        r"^P\.O\.\s*BOX",
        r"^STATION",
        r"^MONTREAL",
        r"^LANGLEY",  # mailing address line (vs txn line which starts with date)
        r"^MR\s",
        r"^MRS\s",
        r"^MISS\s",
        r"^Mr\s+",
        r"^Mrs\s+",
        r"^Owners?:",
        r"^Amount\s*you'?re\s*paying",
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

    m = _STMT_DATE_RE.search(text)
    if m:
        statement_date = _parse_date(m.group("month"), m.group("day"), m.group("year"))

    # New-template fallback: if there's no NewBalance line, try Total balance
    # and pair its amount with statement_date for the closing date.
    if new_balance is None:
        m = _TOTAL_BAL_RE.search(text)
        if m:
            new_balance = _to_decimal(m.group("amount"))
            new_date = statement_date  # may still be None; caller handles

    # Period range — first occurrence of MMM.DD,YYYY-MMM.DD,YYYY after the
    # period anchor (PERIODCOVEREDBYTHISSTATEMENT in old layout, "Statement
    # period" in new layout). Both anchors live in _PERIOD_ANCHOR_RE.
    period_marker = _PERIOD_ANCHOR_RE.search(text)
    search_from = period_marker.end() if period_marker else 0
    m = _PERIOD_RANGE_RE.search(text, search_from)
    if m:
        period_start = _parse_date(m.group("m1"), m.group("d1"), m.group("y1"))
        period_end = _parse_date(m.group("m2"), m.group("d2"), m.group("y2"))

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

        # IMPORTANT date-semantic difference vs the chequing parser:
        # chequing's "Openingbalance" line is dated the FIRST day of the
        # new period (so opening assertion fires at start of that day,
        # before any new-period transactions). MC's "PreviousBalance"
        # line is dated the LAST day of the PRIOR period (so the value
        # is end-of-day for that prior day). Beancount assertions fire
        # at START of the dated day, so to assert "balance at end of
        # PreviousBalance date" we need to date the assertion ONE DAY
        # LATER — which is exactly the period_start. Without this shift,
        # the opening assertion overlaps with end-of-day txns on
        # previous_date and conflicts with the prior statement's
        # closing assertion (which we DO emit at period_end + 1).
        opening_date = summary.period_start
        if opening_date is None and summary.previous_date is not None:
            from datetime import timedelta
            opening_date = summary.previous_date + timedelta(days=1)
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
