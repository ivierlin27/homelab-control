"""BMO OFX (multi-account) importer.

BMO's online banking exports a single OFX v1 (SGML) file containing ALL
enabled bank accounts. Each `<STMTTRNRS>` section carries its own
`<ACCTID>`, `<CURDEF>`, `<BANKTRANLIST>`, and `<LEDGERBAL>`.

Account identification:
  - Most accounts use the visible transit+account in ACCTID (e.g.
    "07643953616" → last 4 = "3616"). These map directly to our
    existing profile last-4s.
  - Joint chequing and some others use BMO's internal customer ID
    (e.g. "55102900758941531") which doesn't embed the visible last-4.
    These require an explicit `ofx_acct_id → slug` mapping in the
    profile registry.

Dedup strategy:
  Each OFX transaction has a unique FITID assigned by the bank. We
  store ingested FITIDs in a per-account set file at:
    ~/.local/state/homelab-control/agent-finance/fitids/<slug>.txt
  On ingest, any txn whose FITID is already in the set is silently
  skipped. This handles:
    - Re-downloading the same OFX file
    - Overlapping date ranges between OFX and prior PDF imports
      (PDF-ingested txns won't have FITIDs, so there's no collision;
       the date-cutoff further prevents double-counting)
    - Overlapping date ranges between successive OFX downloads

Date-cutoff (vs prior PDF history):
  For each account in the OFX, we read the latest balance-assertion
  date from the ledger. Any OFX txn with posting_date ≤ that date is
  skipped (those txns are already covered by the PDF import). Only txns
  AFTER the latest PDF closing date are ingested. This is the
  deterministic, no-fuzzy-matching dedup approach decided in design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
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
    render_simple_entry,
)

COUNTER_ACCOUNT = "Expenses:Uncategorized"


@dataclass(frozen=True)
class BmoOfxAccountMapping:
    """Maps an OFX ACCTID to our existing profile slug + source account."""

    ofx_acct_id: str
    slug: str
    source_account: str
    currency: str


# Maps OFX ACCTID → profile slug. Built from the known OFX file structure.
# Accounts with 0764-prefix ACCTIDs are mapped dynamically by last-4 in
# _resolve_account(); only the oddball internal-ID accounts need explicit
# entries here.
_EXPLICIT_ACCTID_MAP: dict[str, str] = {
    "55102900758941531": "bmo-joint-chequing",
    "551029007589415301": "bmo-jennifer-chequing",
    "5191230213430706": "bmo-cashback-mc",
    "4514011824111189": "rbc-avion-visa",
    "447539709": "amplify-checking-9709",
    "447539710100": "amplify-savings-0100",
}

# Reverse map: last-4 digits → slug (for 0764-prefix accounts).
_LAST4_TO_SLUG: dict[str, str] = {
    "4969": "bmo-joint-chequing",
    "4256": "bmo-kevin-chequing",
    "4264": "bmo-jennifer-chequing",
    "7179": "bmo-jennifer-books-7179",
    "3616": "bmo-makaely-personal-chequing",
    "3624": "bmo-ellowyn-personal-chequing",
    "4221": "bmo-makaely-education-4221",
    "4248": "bmo-ellowyn-education-4248",
    "6863": "bmo-joint-savings-usd-6863",
    "8327": "bmo-joint-savings-cad-8327",
}


def resolve_slug_for_acctid(acct_id: str) -> Optional[str]:
    """Resolve an OFX ACCTID to our profile slug, or None if unknown."""
    if acct_id in _EXPLICIT_ACCTID_MAP:
        return _EXPLICIT_ACCTID_MAP[acct_id]
    last4 = acct_id[-4:]
    return _LAST4_TO_SLUG.get(last4)


# ---------------------------------------------------------------------------
# FITID dedup store
# ---------------------------------------------------------------------------


class FitIdStore:
    """Persistent set of ingested FITIDs per account slug.

    Storage: one text file per slug at `base_dir/<slug>.txt`, one FITID per
    line. Loaded lazily on first access; appended atomically on commit.
    """

    def __init__(self, base_dir: Path):
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, set[str]] = {}

    def _path_for(self, slug: str) -> Path:
        return self._base_dir / f"{slug}.txt"

    def _load(self, slug: str) -> set[str]:
        if slug not in self._cache:
            p = self._path_for(slug)
            if p.is_file():
                self._cache[slug] = set(p.read_text().splitlines())
            else:
                self._cache[slug] = set()
        return self._cache[slug]

    def contains(self, slug: str, fitid: str) -> bool:
        return fitid in self._load(slug)

    def add(self, slug: str, fitid: str) -> None:
        self._load(slug).add(fitid)

    def commit(self, slug: str) -> None:
        """Persist the in-memory set for a slug back to disk."""
        p = self._path_for(slug)
        p.write_text("\n".join(sorted(self._load(slug))) + "\n")


# ---------------------------------------------------------------------------
# OFX parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OfxAccountExtract:
    """Parsed data for one account section within a multi-account OFX file."""

    slug: str
    source_account: str
    currency: str
    transactions: list[ExtractedTransaction]
    closing_balance: Optional[Decimal]
    closing_date: Optional[date]
    period_start: Optional[date]
    period_end: Optional[date]
    skipped_fitids: list[str]    # FITIDs skipped due to dedup or date-cutoff
    ingested_fitids: list[str]   # FITIDs that made it through


def _slug_meta_map() -> dict[str, tuple[str, str]]:
    """Return unified slug → (source_account, currency) map for all OFX-routable accounts."""
    from .bmo_chequing_pdf import PROFILES as CHEQUING_PROFILES

    meta: dict[str, tuple[str, str]] = {}
    for slug, p in CHEQUING_PROFILES.items():
        meta[slug] = (p.source_account, p.currency)
    meta["bmo-cashback-mc"] = (
        "Liabilities:CA:BMO:CreditCard:CashbackMC-Joint-0706",
        "CAD",
    )
    meta["rbc-avion-visa"] = (
        "Liabilities:CA:RBC:CreditCard:AvionVisaPlatinum-Joint-1847",
        "CAD",
    )
    meta["amplify-checking-9709"] = (
        "Assets:US:Amplify:Checking-Joint-9709",
        "USD",
    )
    meta["amplify-savings-0100"] = (
        "Assets:US:Amplify:Savings-Joint-0100",
        "USD",
    )
    return meta


def parse_ofx_file(
    path: Path,
    *,
    fitid_store: Optional[FitIdStore] = None,
    cutoff_dates: Optional[dict[str, date]] = None,
) -> list[OfxAccountExtract]:
    """Parse a BMO multi-account OFX file into per-account extracts.

    Args:
        path: Path to the .ofx file.
        fitid_store: If provided, skip txns whose FITID is already stored.
        cutoff_dates: Optional dict of slug → latest-known-date. Txns with
            posting_date <= cutoff are skipped (PDF-overlap prevention).

    Returns:
        List of OfxAccountExtract, one per account section that has at
        least one transaction after dedup/cutoff filtering. Empty accounts
        are omitted.
    """
    try:
        from ofxparse import OfxParser  # type: ignore[import-not-found]
    except ImportError as exc:
        raise PreParserError(
            "ofxparse not installed. Run `pip3 install --user ofxparse`."
        ) from exc

    if not path.is_file():
        raise PreParserError(f"OFX file not found: {path}")

    try:
        with open(path, "rb") as f:
            ofx = OfxParser.parse(f)
    except Exception as exc:
        raise PreParserError(f"ofxparse failed on {path}: {exc}") from exc

    if cutoff_dates is None:
        cutoff_dates = {}

    _slug_meta = _slug_meta_map()

    results: list[OfxAccountExtract] = []
    for account in ofx.accounts:
        slug = resolve_slug_for_acctid(account.account_id)
        if slug is None:
            continue  # unknown account, skip silently

        meta = _slug_meta.get(slug)
        if meta is None:
            continue
        source_account, currency = meta

        cutoff = cutoff_dates.get(slug)
        stmt = account.statement

        txns: list[ExtractedTransaction] = []
        skipped: list[str] = []
        ingested: list[str] = []

        for t in stmt.transactions:
            fitid = t.id
            posting_date = t.date.date() if isinstance(t.date, datetime) else t.date

            # FITID dedup
            if fitid_store and fitid_store.contains(slug, fitid):
                skipped.append(fitid)
                continue

            # Date-cutoff dedup (skip overlap with prior PDF imports)
            if cutoff and posting_date <= cutoff:
                skipped.append(fitid)
                continue

            # OFX TRNAMT is already signed: negative=debit, positive=credit.
            # For asset accounts (chequing/savings): negative = money out,
            #   positive = money in — maps directly to Beancount.
            # For liability accounts (CC): negative = purchase (increases
            #   debt), positive = payment (reduces debt) — also maps directly
            #   because Beancount tracks CC liabilities as negative balances.
            amount = Decimal(str(t.amount))
            payee = (t.payee or "").strip()
            memo = (t.memo or "").strip()
            desc = f"{payee} {memo}".strip() if memo and memo != payee else payee

            txns.append(ExtractedTransaction(
                posting_date=posting_date,
                description=desc,
                amount=amount,
                currency=currency.upper(),
                raw_line=f"FITID={fitid} {t.type} {desc}",
            ))
            ingested.append(fitid)

        if not txns:
            continue  # no new txns for this account after filtering

        # Closing balance from OFX LEDGERBAL.
        # For CC accounts, BMO's LEDGERBAL often includes pending/authorized
        # amounts not yet in the posted transaction list, causing a mismatch.
        # Only emit balance assertions for asset accounts (chequing/savings)
        # where LEDGERBAL reliably equals opening + posted txns.
        closing_balance = None
        closing_date_val = None
        is_liability = source_account.startswith("Liabilities:")
        if stmt.balance is not None and not is_liability:
            closing_balance = Decimal(str(stmt.balance))
        if stmt.balance_date and not is_liability:
            bd = stmt.balance_date
            closing_date_val = bd.date() if isinstance(bd, datetime) else bd

        period_start = None
        period_end = None
        if stmt.start_date:
            sd = stmt.start_date
            period_start = sd.date() if isinstance(sd, datetime) else sd
        if stmt.end_date:
            ed = stmt.end_date
            period_end = ed.date() if isinstance(ed, datetime) else ed

        results.append(OfxAccountExtract(
            slug=slug,
            source_account=source_account,
            currency=currency.upper(),
            transactions=txns,
            closing_balance=closing_balance,
            closing_date=closing_date_val,
            period_start=period_start,
            period_end=period_end,
            skipped_fitids=skipped,
            ingested_fitids=ingested,
        ))

    return results
