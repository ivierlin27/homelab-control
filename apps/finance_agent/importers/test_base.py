"""Unit tests for the importer base types (F4a)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.finance_agent.importers.base import (
    BeancountEntry,
    ExtractedTransaction,
    ImporterError,
    PreParserError,
    normalize_bmo_description,
    render_simple_entry,
)


# --- ExtractedTransaction validation -------------------------------------

def test_extracted_transaction_happy_path() -> None:
    t = ExtractedTransaction(
        posting_date=date(2024, 1, 15),
        description="ETRANSFER FROM JENNIFER",
        amount=Decimal("125.00"),
        currency="CAD",
    )
    assert t.currency == "CAD"
    assert t.amount == Decimal("125.00")


def test_extracted_transaction_rejects_blank_description() -> None:
    with pytest.raises(PreParserError, match="description"):
        ExtractedTransaction(
            posting_date=date(2024, 1, 15),
            description="   ",
            amount=Decimal("1.00"),
            currency="CAD",
        )


def test_extracted_transaction_rejects_unknown_currency() -> None:
    with pytest.raises(PreParserError, match="currency"):
        ExtractedTransaction(
            posting_date=date(2024, 1, 15),
            description="foo",
            amount=Decimal("1.00"),
            currency="EUR",
        )


def test_extracted_transaction_rejects_float_amount() -> None:
    with pytest.raises(PreParserError, match="Decimal"):
        ExtractedTransaction(
            posting_date=date(2024, 1, 15),
            description="foo",
            amount=1.50,  # type: ignore[arg-type]
            currency="CAD",
        )


# --- BeancountEntry validation -------------------------------------------

def test_beancount_entry_requires_trailing_newline() -> None:
    with pytest.raises(ImporterError, match="newline-terminated"):
        BeancountEntry(text="2024-01-01 ! \"x\"", posting_date=date(2024, 1, 1))


def test_normalize_bmo_description_strips_statement_footer() -> None:
    raw = (
        "InterestEarned Pleasereportanyerrors,omissionsorirregularities "
        "addressofeachbeneficiary"
    )
    assert normalize_bmo_description(raw) == "InterestEarned"


def test_render_simple_entry_strips_bmo_footer_from_payee() -> None:
    txn = ExtractedTransaction(
        posting_date=date(2024, 2, 1),
        description=",PROMOTIONALINTEREST,NEWMONEYOFFER Pleasereportanyerrors,omissions",
        amount=Decimal("16.24"),
        currency="CAD",
    )
    entry = render_simple_entry(
        txn,
        source_account="Assets:CA:BMO:Savings:Joint-CAD-8327",
        counter_account="Expenses:Uncategorized",
        importer_slug="bmo-joint-savings-cad-8327",
    )
    assert '",PROMOTIONALINTEREST,NEWMONEYOFFER"' in entry.text
    assert "Pleasereportanyerrors" not in entry.text


# --- render_simple_entry --------------------------------------------------

def test_render_simple_entry_debit_balances() -> None:
    txn = ExtractedTransaction(
        posting_date=date(2024, 1, 15),
        description="LOBLAWS #1234",
        amount=Decimal("-87.43"),
        currency="CAD",
        raw_line="2024-01-15  LOBLAWS #1234  -87.43",
    )
    entry = render_simple_entry(
        txn,
        source_account="Assets:CA:BMO:Chequing:Joint-4969",
        counter_account="Expenses:Uncategorized",
        importer_slug="bmo-joint-chequing",
    )

    assert entry.posting_date == date(2024, 1, 15)
    assert entry.source_account == "Assets:CA:BMO:Chequing:Joint-4969"
    assert entry.counter_account == "Expenses:Uncategorized"
    assert entry.amount == Decimal("-87.43")
    assert entry.currency == "CAD"

    txt = entry.text
    assert txt.endswith("\n")
    assert "2024-01-15" in txt
    assert "LOBLAWS" in txt
    assert "Assets:CA:BMO:Chequing:Joint-4969" in txt
    assert "Expenses:Uncategorized" in txt
    assert "-87.43 CAD" in txt
    assert "87.43 CAD" in txt  # the inverse leg
    assert "source_importer: \"bmo-joint-chequing\"" in txt
    # Pending operator review flag (F6 promotes to *)
    assert "! \"LOBLAWS #1234\"" in txt


def test_render_simple_entry_credit_balances() -> None:
    txn = ExtractedTransaction(
        posting_date=date(2024, 1, 16),
        description="PAYROLL DEPOSIT",
        amount=Decimal("2500.00"),
        currency="CAD",
    )
    entry = render_simple_entry(
        txn,
        source_account="Assets:CA:BMO:Chequing:Kevin-4256",
        counter_account="Income:Uncategorized",
        importer_slug="bmo-kevin-chequing",
    )
    assert "2500.00 CAD" in entry.text
    assert "-2500.00 CAD" in entry.text
    assert "Income:Uncategorized" in entry.text


def test_render_simple_entry_escapes_quotes_in_description() -> None:
    txn = ExtractedTransaction(
        posting_date=date(2024, 1, 15),
        description='WEIRD "QUOTED" MERCHANT',
        amount=Decimal("-1.00"),
        currency="CAD",
    )
    entry = render_simple_entry(
        txn,
        source_account="Assets:CA:BMO:Chequing:Joint-4969",
        counter_account="Expenses:Uncategorized",
        importer_slug="bmo-joint-chequing",
    )
    # Beancount narrations are double-quoted; we replace inner quotes with single.
    # First line should be exactly: 2024-01-15 ! "WEIRD 'QUOTED' MERCHANT"
    first_line = entry.text.splitlines()[0]
    assert first_line == '2024-01-15 ! "WEIRD \'QUOTED\' MERCHANT"'


def test_render_simple_entry_rejects_unsupported_currency() -> None:
    # ExtractedTransaction's own validator catches this first, but the
    # helper has a defensive check too.
    with pytest.raises(PreParserError):
        ExtractedTransaction(
            posting_date=date(2024, 1, 15),
            description="x",
            amount=Decimal("1.00"),
            currency="GBP",
        )
