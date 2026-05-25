"""Parse and rewrite pending (!) uncategorized Beancount transactions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

UNCATEGORIZED = "Expenses:Uncategorized"

# 2021-08-04 ! "DEPOSIT PAYROLL"
_HEADER_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+!\s+\"(?P<desc>.*)\"\s*$"
)
#   Assets:CA:BMO:Chequing:Joint-4969                      1000.00 CAD
_POSTING_RE = re.compile(
    r"^\s+(?P<account>[A-Z][A-Za-z0-9:_-]+)\s+"
    r"(?P<amount>-?[\d,]+\.\d{2})\s+"
    r"(?P<currency>[A-Z]{3})\s*$"
)
#   source_importer: "bmo-ofx:slug"
_META_RE = re.compile(r"^\s+(?P<key>[a-z_]+):\s+\"(?P<value>.*)\"\s*$")
# Next directive starts a new entry — do not attach to the pending transaction.
_DIRECTIVE_START_RE = re.compile(
    r"^\s*(?P<date>\d{4}-\d{2}-\d{2})\s+(balance|pad)\s+"
)


@dataclass(frozen=True)
class PendingTransaction:
    """One pending (!) entry still on Expenses:Uncategorized."""

    block_start: int
    block_end: int
    date: str
    description: str
    source_account: str
    source_amount: Decimal
    amount: Decimal
    currency: str
    lines: tuple[str, ...]

    @property
    def block_text(self) -> str:
        return "".join(self.lines)


def _parse_amount(raw: str) -> Decimal:
    return Decimal(raw.replace(",", ""))


def find_pending(
    transactions_path: Path,
    *,
    uncategorized_account: str = UNCATEGORIZED,
    limit: int | None = None,
) -> list[PendingTransaction]:
    path = Path(transactions_path).expanduser()
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    pending: list[PendingTransaction] = []
    i = 0
    while i < len(lines):
        header = _HEADER_RE.match(lines[i].rstrip("\n"))
        if not header:
            i += 1
            continue
        start = i
        i += 1
        block: list[str] = [lines[start]]
        source_account = ""
        source_amount = Decimal("0")
        has_uncategorized = False
        amount = Decimal("0")
        currency = "CAD"
        while i < len(lines):
            stripped = lines[i].rstrip("\n")
            if _HEADER_RE.match(stripped):
                break
            if _DIRECTIVE_START_RE.match(stripped):
                break
            line = lines[i]
            block.append(line)
            meta = _META_RE.match(stripped)
            if meta:
                i += 1
                continue
            posting = _POSTING_RE.match(stripped)
            if posting:
                acct = posting.group("account")
                if acct == uncategorized_account:
                    has_uncategorized = True
                    amount = _parse_amount(posting.group("amount"))
                    currency = posting.group("currency")
                elif acct.startswith(("Assets:", "Liabilities:")):
                    if not source_account:
                        source_account = acct
                        source_amount = _parse_amount(posting.group("amount"))
            i += 1
        if has_uncategorized and source_account:
            txn = PendingTransaction(
                block_start=start,
                block_end=i,
                date=header.group("date"),
                description=header.group("desc"),
                source_account=source_account,
                source_amount=source_amount,
                amount=amount,
                currency=currency,
                lines=tuple(block),
            )
            pending.append(txn)
            if limit is not None and len(pending) >= limit:
                break
    return pending


def apply_category(
    txn: PendingTransaction,
    *,
    category: str,
    confidence: float,
    correlation_id: str,
) -> list[str]:
    """Return replacement lines: promote ! → *, swap counter leg, add metadata."""
    out: list[str] = []
    # Counter posting must balance the source leg (not the uncategorized leg copy).
    inverse = -txn.source_amount
    meta_lines = [
        f'  categorize_confidence: "{confidence:.3f}"\n',
        f'  categorize_run: "{correlation_id}"\n',
    ]
    for line in txn.lines:
        stripped = line.rstrip("\n")
        if _HEADER_RE.match(stripped):
            out.append(f'{txn.date} * "{txn.description}"\n')
            out.extend(meta_lines)
            continue
        posting = _POSTING_RE.match(stripped)
        if posting and posting.group("account") == UNCATEGORIZED:
            out.append(f"  {category:<55} {inverse:>14.2f} {txn.currency}\n")
            continue
        out.append(line)
    return out


def rewrite_blocks(
    transactions_path: Path,
    replacements: list[tuple[PendingTransaction, list[str]]],
) -> None:
    """Apply in-memory replacements and write the ledger file back."""
    if not replacements:
        return
    path = Path(transactions_path).expanduser()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    # Apply from bottom to top so indices stay valid.
    for txn, new_lines in sorted(replacements, key=lambda x: x[0].block_start, reverse=True):
        lines[txn.block_start : txn.block_end] = new_lines
    path.write_text("".join(lines), encoding="utf-8")
