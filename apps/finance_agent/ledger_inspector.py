"""Read-only inspector for the ledger file — used by the ingest layer
to make state-aware decisions (e.g. should we emit a pad directive?).

Kept narrow on purpose: we parse `balance` directives only, by regex.
No Beancount runtime dependency — keeps tests fast and lets the
ingest layer work even when bean-check isn't on PATH.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Optional

# Matches one Beancount `balance` directive line, e.g.:
#   2021-08-19 balance Assets:CA:BMO:Chequing:Joint-4969    2012.46 CAD
# Account names may contain colons, hyphens, digits, capital letters.
_BALANCE_LINE_RE = re.compile(
    r"^\s*(?P<date>\d{4}-\d{2}-\d{2})\s+balance\s+"
    r"(?P<account>[A-Z][A-Za-z0-9:_-]+)\s+"
    r"(?P<amount>-?[\d,]+\.\d{2})\s+"
    r"(?P<currency>[A-Z]{3})\s*$"
)


def _parse_amount(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def find_last_balance_assertion(
    transactions_path: Path, source_account: str
) -> Optional[Decimal]:
    """Return the most recent `balance` directive's amount for an account.

    Returns ``None`` if the file doesn't exist, has no matching assertions,
    or can't be read for any reason — caller should treat None as "no prior
    state known about this account".

    "Most recent" is by ISO date string comparison (which is correct since
    Beancount dates are always ISO YYYY-MM-DD).
    """
    path = Path(transactions_path).expanduser()
    if not path.is_file():
        return None
    last_date = ""
    last_amount: Optional[Decimal] = None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        m = _BALANCE_LINE_RE.match(line)
        if not m:
            continue
        if m.group("account") != source_account:
            continue
        # Lexicographic compare works for ISO YYYY-MM-DD.
        if m.group("date") >= last_date:
            last_date = m.group("date")
            last_amount = _parse_amount(m.group("amount"))
    return last_amount


def find_last_balance_date(
    transactions_path: Path, source_account: str
) -> Optional[date]:
    """Return the date of the most recent `balance` directive for an account.

    Returns ``None`` if no matching assertion exists. Used by the OFX
    ingest layer to determine the date-cutoff for overlap prevention.
    """
    from datetime import date as date_type

    path = Path(transactions_path).expanduser()
    if not path.is_file():
        return None
    last_date_str = ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        m = _BALANCE_LINE_RE.match(line)
        if not m:
            continue
        if m.group("account") != source_account:
            continue
        if m.group("date") >= last_date_str:
            last_date_str = m.group("date")
    if not last_date_str:
        return None
    parts = last_date_str.split("-")
    return date_type(int(parts[0]), int(parts[1]), int(parts[2]))
