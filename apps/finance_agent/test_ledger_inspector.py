"""Unit tests for apps.finance_agent.ledger_inspector."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from apps.finance_agent.ledger_inspector import find_last_balance_assertion


ACCT = "Assets:CA:BMO:Chequing:Joint-4969"
OTHER = "Assets:CA:BMO:Chequing:Kevin-4256"


def _write_ledger(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_find_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert find_last_balance_assertion(tmp_path / "missing.beancount", ACCT) is None


def test_find_returns_none_when_no_assertions(tmp_path: Path) -> None:
    p = _write_ledger(
        tmp_path / "t.beancount",
        "2021-08-04 ! \"GroceryStore\"\n"
        f"  {ACCT}            -50.00 CAD\n"
        "  Expenses:Uncategorized            50.00 CAD\n",
    )
    assert find_last_balance_assertion(p, ACCT) is None


def test_find_happy_path_single_assertion(tmp_path: Path) -> None:
    p = _write_ledger(
        tmp_path / "t.beancount",
        f"2021-08-19 balance {ACCT}    2012.46 CAD\n",
    )
    assert find_last_balance_assertion(p, ACCT) == Decimal("2012.46")


def test_find_returns_most_recent_when_multiple(tmp_path: Path) -> None:
    p = _write_ledger(
        tmp_path / "t.beancount",
        f"2021-08-19 balance {ACCT}    2012.46 CAD\n"
        f"2021-09-19 balance {ACCT}    3500.00 CAD\n"
        f"2021-07-19 balance {ACCT}     100.00 CAD\n",
    )
    assert find_last_balance_assertion(p, ACCT) == Decimal("3500.00")


def test_find_ignores_assertions_for_other_accounts(tmp_path: Path) -> None:
    p = _write_ledger(
        tmp_path / "t.beancount",
        f"2021-09-19 balance {OTHER}    9999.99 CAD\n"
        f"2021-08-19 balance {ACCT}    2012.46 CAD\n",
    )
    assert find_last_balance_assertion(p, ACCT) == Decimal("2012.46")
    assert find_last_balance_assertion(p, OTHER) == Decimal("9999.99")


def test_find_handles_comma_thousands(tmp_path: Path) -> None:
    p = _write_ledger(
        tmp_path / "t.beancount",
        f"2024-04-19 balance {ACCT}    22,888.34 CAD\n",
    )
    assert find_last_balance_assertion(p, ACCT) == Decimal("22888.34")


def test_find_handles_negative_balance(tmp_path: Path) -> None:
    """Liability accounts have negative balances when paid down past zero."""
    p = _write_ledger(
        tmp_path / "t.beancount",
        f"2024-04-19 balance Liabilities:CA:BMO:CreditCard:CashbackMC-Joint-0706    "
        f"-150.00 CAD\n",
    )
    assert find_last_balance_assertion(
        p, "Liabilities:CA:BMO:CreditCard:CashbackMC-Joint-0706"
    ) == Decimal("-150.00")


def test_find_ignores_non_balance_directives(tmp_path: Path) -> None:
    """pad, open, close, txn — none of these are balance assertions."""
    p = _write_ledger(
        tmp_path / "t.beancount",
        f"2021-08-03 pad {ACCT} Equity:Opening-Balances\n"
        f"2020-01-01 open {ACCT} CAD\n"
        f"2021-08-04 ! \"x\"\n"
        f"  {ACCT}            10.00 CAD\n"
        f"  Expenses:Uncategorized           -10.00 CAD\n",
    )
    assert find_last_balance_assertion(p, ACCT) is None
