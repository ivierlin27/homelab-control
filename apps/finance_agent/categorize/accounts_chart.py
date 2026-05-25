"""Accounts that categorize may post to; used to validate before a real run."""

from __future__ import annotations

# Keep in sync with categories in policy.yaml.
CATEGORIZE_ACCOUNTS: tuple[str, ...] = (
    "Expenses:Auto:Fuel",
    "Expenses:Bank:Fees",
    "Expenses:Cash:ATM",
    "Expenses:Education:Tuition",
    "Expenses:Financial:CreditCardPayment",
    "Expenses:Food:Dining",
    "Expenses:Food:Groceries",
    "Expenses:Food:MealKits",
    "Expenses:Health:Medical",
    "Expenses:Household:Allowance",
    "Expenses:Household:CC-Reimbursement",
    "Expenses:Housing:Mortgage",
    "Expenses:Insurance:Life",
    "Expenses:Investments:Contribution",
    "Expenses:Misc",
    "Expenses:Shopping:Online",
    "Expenses:Shopping:Retail",
    "Expenses:Subscriptions:Digital",
    "Expenses:Transit:Public",
    "Expenses:Transfers:External",
    "Expenses:Transfers:Internal",
    "Expenses:Utilities:Electric",
    "Expenses:Utilities:Telecom",
    "Income:Household:Allowance",
    "Income:Household:FamilyReimbursement",
    "Income:Interest:Bank",
    "Income:Investments:Dividend",
    "Income:Misc:Deposit",
    "Income:Misc:TransferIn",
    "Income:Salary:Employer",
    "Income:Wire:Incoming",
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
