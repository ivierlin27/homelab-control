"""Map Beancount source accounts to household roles for context rules."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Iterable

# BMO joint chequing last-4 appears in transfer memos (TF0764#3954-969).
JOINT_CHEQUING_MARKER = re.compile(r"(?i)(#3954-969|joint-4969|:joint-4969)")

_TRANSFER_DESC = re.compile(
    r"(?i)(e-?transfer|interac|online\s*transfer|scheduled\s*transfer|"
    r"onlinetransfer|scheduledtransfer|xfer|\btfr\b|#3954)"
)
_RECURRING_DESC = re.compile(r"(?i)(recurringpymnt|recurring\s*payment)")
_DEPOSIT_DESC = re.compile(r"(?i)(?:^deposit$|deposit,)")
_PAYROLL_DESC = re.compile(r"(?i)(directdeposit|payroll|pay\s*cheque|blackbaud)")
_ATM_DESC = re.compile(r"(?i)abmwithdrawal|atm\s*withdrawal")


def source_flow(source_amount: Decimal) -> str:
    """``inflow`` = money into source account; ``outflow`` = money leaving."""
    if source_amount > 0:
        return "inflow"
    if source_amount < 0:
        return "outflow"
    return "any"


def resolve_account_role(
    source_account: str,
    role_patterns: dict[str, tuple[str, ...]],
) -> str:
    """Return the first matching role id for a Beancount account path."""
    for role_id, fragments in role_patterns.items():
        for frag in fragments:
            if frag in source_account:
                return role_id
    if source_account.startswith("Liabilities:") and "CreditCard" in source_account:
        return "credit_card"
    if ":Joint-" in source_account or source_account.endswith("Joint"):
        return "house_other"
    return "unknown"


def references_joint_house(description: str) -> bool:
    return bool(JOINT_CHEQUING_MARKER.search(description))


def is_transfer_description(description: str) -> bool:
    return bool(_TRANSFER_DESC.search(description))


def is_recurring_description(description: str) -> bool:
    return bool(_RECURRING_DESC.search(description))


def is_generic_deposit(description: str) -> bool:
    return bool(_DEPOSIT_DESC.search(description)) and not _PAYROLL_DESC.search(description)


def is_atm_withdrawal(description: str) -> bool:
    return bool(_ATM_DESC.search(description))
