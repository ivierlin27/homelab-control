"""Accounts that categorize may post to; used to validate before a real run."""

from __future__ import annotations

# Subset of F6 policy targets — extend when policy.yaml adds categories.
CATEGORIZE_ACCOUNTS: tuple[str, ...] = (
    "Expenses:Auto:Fuel",
    "Expenses:Food:Groceries",
    "Expenses:Food:Dining",
    "Expenses:Utilities:Electric",
    "Expenses:Utilities:Telecom",
    "Expenses:Household:Allowance",
    "Expenses:Household:CC-Reimbursement",
    "Expenses:Cash:ATM",
    "Expenses:Misc",
    "Income:Salary:Employer",
    "Income:Interest:Bank",
    "Income:Household:Allowance",
    "Income:Household:FamilyReimbursement",
    "Income:Misc:Deposit",
)


def ensure_accounts_open(accounts_path, *, open_date: str = "2000-01-01") -> list[str]:
    """Append ``open`` directives for any categorize account missing from the chart."""
    from pathlib import Path

    path = Path(accounts_path).expanduser()
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    added: list[str] = []
    lines_to_append: list[str] = []
    for account in CATEGORIZE_ACCOUNTS:
        if f"open {account}" in text:
            continue
        lines_to_append.append(f"{open_date} open {account}\n")
        added.append(account)
    if lines_to_append:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n;; Added by agent:finance categorize (F6)\n"
        text += "".join(lines_to_append)
        path.write_text(text, encoding="utf-8")
    return added
