"""Bank of America CSV importer.

BofA's "Excel" download is a CSV with this structure:

    Description,,Summary Amt.
    Beginning balance as of MM/DD/YYYY,,"X,XXX.XX"
    Total credits,,"X,XXX.XX"
    Total debits,,"-X,XXX.XX"
    Ending balance as of MM/DD/YYYY,,"X,XXX.XX"
    <blank line>
    Date,Description,Amount,Running Bal.
    MM/DD/YYYY,Beginning balance as of ...,,<running_bal>
    MM/DD/YYYY,"<description>","<amount>","<running_bal>"
    ...

Key characteristics:
  - No FITIDs — dedup uses content-hash (date + amount + description)
  - Amounts are quoted, may contain commas: "-345.09", "1,281.00"
  - Running balance on every row (useful for reconciliation)
  - First data row is "Beginning balance" (amount is empty)
  - All accounts are USD

Content-hash dedup:
  We hash (date_iso, amount_str, description_normalized) per account slug.
  Stored in the same FITID store directory as OFX, at:
    ~/.local/state/homelab-control/agent-finance/fitids/<slug>.txt
  This prevents double-counting on re-import of the same CSV or
  overlapping downloads.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Optional

from .base import (
    BeancountEntry,
    ExtractedTransaction,
    render_closing_balance_assertion,
    render_simple_entry,
)
from .bmo_ofx import FitIdStore


@dataclass(frozen=True)
class BofaCsvProfile:
    slug: str
    source_account: str
    currency: str
    account_last4: str


BOFA_PROFILES: dict[str, BofaCsvProfile] = {
    "bofa-checking-5396": BofaCsvProfile(
        slug="bofa-checking-5396",
        source_account="Assets:US:BofA:Checking-Joint-5396",
        currency="USD",
        account_last4="5396",
    ),
    "bofa-savings-8762": BofaCsvProfile(
        slug="bofa-savings-8762",
        source_account="Assets:US:BofA:Savings-Joint-8762",
        currency="USD",
        account_last4="8762",
    ),
}


@dataclass(frozen=True)
class BofaCsvExtract:
    slug: str
    source_account: str
    currency: str
    transactions: list[ExtractedTransaction]
    opening_balance: Decimal
    opening_date: date
    closing_balance: Decimal
    closing_date: date
    ingested_hashes: list[str]
    skipped_hashes: list[str]


def _parse_amount(raw: str) -> Decimal:
    """Parse a BofA amount like '-345.09' or '1,281.00' (already unquoted by csv reader)."""
    cleaned = raw.replace(",", "").strip()
    return Decimal(cleaned)


def _parse_date(raw: str) -> date:
    """Parse MM/DD/YYYY."""
    parts = raw.strip().split("/")
    return date(int(parts[2]), int(parts[0]), int(parts[1]))


def _content_hash(posting_date: date, amount: Decimal, description: str) -> str:
    """Deterministic hash for dedup (no FITID available)."""
    key = f"{posting_date.isoformat()}|{amount}|{description.strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


_BEGINNING_BAL_RE = re.compile(
    r"Beginning balance as of (\d{2}/\d{2}/\d{4})", re.IGNORECASE
)
_ENDING_BAL_RE = re.compile(
    r"Ending balance as of (\d{2}/\d{2}/\d{4})", re.IGNORECASE
)


def parse_bofa_csv(
    path: Path,
    profile: BofaCsvProfile,
    *,
    fitid_store: Optional[FitIdStore] = None,
    cutoff_date: Optional[date] = None,
) -> BofaCsvExtract:
    """Parse a Bank of America CSV into an extract for one account.

    Args:
        path: Path to the .csv file.
        profile: Account profile with slug, source_account, currency.
        fitid_store: If provided, skip txns whose content-hash is already stored.
        cutoff_date: If provided, skip txns with posting_date <= cutoff.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = text.splitlines()

    # Find the header row "Date,Description,Amount,Running Bal."
    data_start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("Date,Description,Amount"):
            data_start = i
            break
    if data_start is None:
        raise ValueError(f"Could not find data header in {path}")

    # Parse summary header for opening/closing
    opening_balance = None
    opening_date_val = None
    closing_balance = None
    closing_date_val = None
    for line in lines[:data_start]:
        m = _BEGINNING_BAL_RE.search(line)
        if m:
            # Amount is in the third column
            parts = next(csv.reader(StringIO(line)))
            opening_balance = _parse_amount(parts[2])
            opening_date_val = _parse_date(m.group(1))
        m = _ENDING_BAL_RE.search(line)
        if m:
            parts = next(csv.reader(StringIO(line)))
            closing_balance = _parse_amount(parts[2])
            closing_date_val = _parse_date(m.group(1))

    if opening_balance is None or closing_balance is None:
        raise ValueError(f"Could not find opening/closing balance in {path}")

    # Parse transaction rows
    data_text = "\n".join(lines[data_start:])
    reader = csv.reader(StringIO(data_text))
    next(reader)  # skip header

    txns: list[ExtractedTransaction] = []
    ingested: list[str] = []
    skipped: list[str] = []

    for row in reader:
        if not row or len(row) < 3:
            continue
        date_str, desc, amount_str = row[0], row[1], row[2]
        if not amount_str.strip():
            continue  # "Beginning balance" row has no amount

        posting_date = _parse_date(date_str)
        amount = _parse_amount(amount_str)
        content_hash = _content_hash(posting_date, amount, desc)

        # Dedup by content hash
        if fitid_store and fitid_store.contains(profile.slug, content_hash):
            skipped.append(content_hash)
            continue

        # Date-cutoff
        if cutoff_date and posting_date <= cutoff_date:
            skipped.append(content_hash)
            continue

        txns.append(ExtractedTransaction(
            posting_date=posting_date,
            description=desc.strip(),
            amount=amount,
            currency=profile.currency,
            raw_line=f"HASH={content_hash} {date_str} {desc.strip()} {amount_str}",
        ))
        ingested.append(content_hash)

    # Reconciliation: opening + sum(amounts) should equal closing
    txn_sum = sum(t.amount for t in txns) + sum(
        _parse_amount(row[2])
        for row in csv.reader(StringIO(data_text))
        if row and len(row) >= 3 and row[2].strip()
        and _content_hash(_parse_date(row[0]), _parse_amount(row[2]), row[1]) in skipped
    ) if skipped else sum(t.amount for t in txns)

    # Simple reconciliation using ALL amounts from the CSV (not just ingested)
    all_reader = csv.reader(StringIO(data_text))
    next(all_reader)  # skip header
    all_sum = Decimal("0")
    for row in all_reader:
        if not row or len(row) < 3 or not row[2].strip():
            continue
        all_sum += _parse_amount(row[2])
    expected_closing = opening_balance + all_sum
    if expected_closing != closing_balance:
        raise ValueError(
            f"BofA CSV reconciliation failed for {profile.slug}: "
            f"opening {opening_balance} + txns {all_sum} = {expected_closing}, "
            f"expected closing {closing_balance}"
        )

    return BofaCsvExtract(
        slug=profile.slug,
        source_account=profile.source_account,
        currency=profile.currency,
        transactions=txns,
        opening_balance=opening_balance,
        opening_date=opening_date_val,
        closing_balance=closing_balance,
        closing_date=closing_date_val,
        ingested_hashes=ingested,
        skipped_hashes=skipped,
    )
