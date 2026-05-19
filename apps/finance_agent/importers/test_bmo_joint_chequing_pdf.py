"""F4b: BMO joint chequing PDF parsing tests.

The fixture below is SYNTHETIC. It mirrors the structural quirks of real
BMO PDFs (continuation lines, page-2 header noise, opening/closing
sentinels, comma-thousands amounts, year boundary) but contains fake
merchant names + fake amounts. We never commit real bank data.

Anatomy of the fixture (each block exercises a parser branch):
  1. Page-1 account header + owner block          → noise (filtered)
  2. Opening balance line                          → metadata
  3. Single-day, single-token-description txn      → happy path debit
  4. Direct deposit (credit, no continuation)      → happy path credit
  5. Wire payment with multi-line description      → continuation handling
  6. End-of-page-1 marker                          → noise (filtered)
  7. Page-2 column-header + account-continued line → noise (filtered)
  8. Pre-authorized payment with continuation      → continuation in middle of stream
  9. Two transactions on the same date             → ordering preservation
 10. Closing totals line                           → metadata (skipped as txn)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.finance_agent.importers.base import PreParserError
from apps.finance_agent.importers.bmo_joint_chequing_pdf import (
    BmoJointChequingImporter,
    BmoJointChequingPdfPreParser,
    infer_statement_year_from_filename,
    parse_statement_text,
    resolve_signs,
)


FIXTURE_PDF_TEXT = """\
PrimaryChequingAccount#4-969
Owners:
MRSJANEDOE,
MRJOHNDOE
Mar19 Openingbalance 1,000.00
Mar20 GroceryStorePurchase 50.25 949.75
Mar21 PayrollDeposit,EMPLOYERINC/PAY 2,500.00 3,449.75
Mar22 IncomingWirePayment,INCOMINGWIRE 10,000.00 13,449.75
PAYMENT,FOREIGNCORP
continued
Page1of2

Here'swhathappenedinyouraccount(continued)
Amountsdeducted Amountsadded
Date Description fromyouraccount($) toyouraccount($) Balance($)
PrimaryChequingAccount#X4-969 (continued)
Mar25 Pre-AuthorizedPayment,UTILITYCO 200.00 13,249.75
MSP/DIV
Mar30 ScheduledTransfer,TF0764#1234-567 100.00 13,149.75
Mar30 OnlineBillPayment,VISACARD 1,500.00 11,649.75
Apr01 Closingtotals 1,850.25 12,500.00
"""


def test_parse_statement_text_extracts_opening_balance() -> None:
    result = parse_statement_text(FIXTURE_PDF_TEXT, anchor_year=2024)
    assert result.opening_balance == Decimal("1000.00")
    assert result.opening_date == date(2024, 3, 19)


def test_parse_statement_text_extracts_six_transactions() -> None:
    """The fixture has 6 transactions (not counting the closing totals line)."""
    result = parse_statement_text(FIXTURE_PDF_TEXT, anchor_year=2024)
    assert len(result.raw_txns) == 6

    # First three: GroceryStorePurchase, PayrollDeposit, IncomingWirePayment
    assert result.raw_txns[0].description == "GroceryStorePurchase"
    assert result.raw_txns[0].amount == Decimal("50.25")
    assert result.raw_txns[0].balance == Decimal("949.75")

    assert result.raw_txns[1].description == "PayrollDeposit,EMPLOYERINC/PAY"
    assert result.raw_txns[1].amount == Decimal("2500.00")


def test_parse_statement_text_glues_continuation_lines() -> None:
    result = parse_statement_text(FIXTURE_PDF_TEXT, anchor_year=2024)
    # Wire payment with PAYMENT,FOREIGNCORP continuation
    wire = result.raw_txns[2]
    assert wire.description.startswith("IncomingWirePayment,INCOMINGWIRE")
    assert "PAYMENT,FOREIGNCORP" in wire.description

    # Pre-authorized payment with MSP/DIV continuation (after page break noise)
    pre_auth = result.raw_txns[3]
    assert "UTILITYCO" in pre_auth.description
    assert "MSP/DIV" in pre_auth.description


def test_parse_statement_text_skips_closing_totals_line() -> None:
    """The Apr01 Closingtotals line should NOT appear as a transaction."""
    result = parse_statement_text(FIXTURE_PDF_TEXT, anchor_year=2024)
    descriptions = [t.description for t in result.raw_txns]
    assert not any("Closingtotals" in d for d in descriptions)
    assert not any(t.posting_date == date(2024, 4, 1) for t in result.raw_txns)


def test_parse_statement_text_filters_page2_header_noise() -> None:
    """The 4 header lines on page 2 must NOT become continuations of the wire txn."""
    result = parse_statement_text(FIXTURE_PDF_TEXT, anchor_year=2024)
    wire = result.raw_txns[2]
    # If page-2 noise leaked in, we'd see "Here's" or "Amounts" or "Primary" in the desc
    assert "Here's" not in wire.description
    assert "Amounts" not in wire.description
    assert "Primary" not in wire.description
    assert "Date Description" not in wire.description


def test_parse_statement_text_preserves_same_day_ordering() -> None:
    """Mar30 has two transactions (ScheduledTransfer, then OnlineBillPayment)."""
    result = parse_statement_text(FIXTURE_PDF_TEXT, anchor_year=2024)
    mar30 = [t for t in result.raw_txns if t.posting_date == date(2024, 3, 30)]
    assert len(mar30) == 2
    assert "ScheduledTransfer" in mar30[0].description
    assert "OnlineBillPayment" in mar30[1].description


def test_parse_statement_text_raises_when_no_opening_balance() -> None:
    no_opening = "Mar20 GroceryStorePurchase 50.25 949.75\n"
    with pytest.raises(PreParserError, match="Openingbalance"):
        parse_statement_text(no_opening, anchor_year=2024)


def test_parse_statement_text_handles_year_rollover() -> None:
    """Statement period Dec → Jan should bump year forward at the wrap."""
    rollover = (
        "Dec28 Openingbalance 100.00\n"
        "Dec29 SomethingDebit 10.00 90.00\n"
        "Jan02 SomethingCredit 50.00 140.00\n"
    )
    result = parse_statement_text(rollover, anchor_year=2023)
    assert result.opening_date == date(2023, 12, 28)
    assert result.raw_txns[0].posting_date == date(2023, 12, 29)
    assert result.raw_txns[1].posting_date == date(2024, 1, 2)


# --- resolve_signs --------------------------------------------------------


def test_resolve_signs_assigns_debit_and_credit_correctly() -> None:
    parsed = parse_statement_text(FIXTURE_PDF_TEXT, anchor_year=2024)
    signed, closing_balance, closing_date = resolve_signs(parsed, currency="CAD")

    assert len(signed) == 6
    # GroceryStorePurchase: 1000 - 50.25 = 949.75 → debit
    assert signed[0].amount == Decimal("-50.25")
    # PayrollDeposit: 949.75 + 2500 = 3449.75 → credit
    assert signed[1].amount == Decimal("2500.00")
    # IncomingWirePayment: 3449.75 + 10000 = 13449.75 → credit
    assert signed[2].amount == Decimal("10000.00")
    # Final closing balance comes from last txn's running balance
    assert closing_balance == Decimal("11649.75")
    assert closing_date == date(2024, 3, 30)


def test_resolve_signs_raises_on_balance_drift() -> None:
    """If the running balance doesn't reconcile, parser must raise loudly."""
    parsed = parse_statement_text(FIXTURE_PDF_TEXT, anchor_year=2024)
    # Mutate a balance so the chain breaks
    parsed.raw_txns[0] = type(parsed.raw_txns[0])(
        posting_date=parsed.raw_txns[0].posting_date,
        description=parsed.raw_txns[0].description,
        amount=parsed.raw_txns[0].amount,
        balance=Decimal("999.99"),  # wrong! should be 949.75
        raw_line=parsed.raw_txns[0].raw_line,
    )
    with pytest.raises(PreParserError, match="failed reconciliation"):
        resolve_signs(parsed, currency="CAD")


def test_resolve_signs_currency_is_propagated() -> None:
    parsed = parse_statement_text(FIXTURE_PDF_TEXT, anchor_year=2024)
    signed, _, _ = resolve_signs(parsed, currency="CAD")
    assert all(t.currency == "CAD" for t in signed)


# --- infer_statement_year_from_filename -----------------------------------


def test_infer_year_from_unambiguous_filename() -> None:
    assert infer_statement_year_from_filename("bmo-joint-2024-03.pdf") == 2024
    assert infer_statement_year_from_filename("statement_2023_dec.pdf") == 2023


def test_infer_year_returns_none_when_no_year_in_filename() -> None:
    assert infer_statement_year_from_filename("statement.pdf") is None


def test_infer_year_returns_none_when_multiple_years_in_filename() -> None:
    # Operator must explicitly disambiguate
    assert infer_statement_year_from_filename("2023-archive-2024.pdf") is None


# --- Importer.render full output (with pad + balance) ---------------------


def test_importer_render_includes_pad_and_balance_directives() -> None:
    """End-to-end on the synthetic fixture: parse → resolve → render."""
    parsed = parse_statement_text(FIXTURE_PDF_TEXT, anchor_year=2024)
    signed, closing_balance, closing_date = resolve_signs(parsed, currency="CAD")

    from apps.finance_agent.importers.base import StatementExtract

    extract = StatementExtract(
        transactions=signed,
        opening_date=parsed.opening_date,
        opening_balance=parsed.opening_balance,
        closing_date=closing_date,
        closing_balance=closing_balance,
    )

    importer = BmoJointChequingImporter()
    entries = importer.render(extract)

    # 6 txns + 1 pad + 1 opening balance + 1 closing balance = 9
    assert len(entries) == 9

    all_text = "".join(e.text for e in entries)
    # pad line
    assert "pad Assets:CA:BMO:Chequing:Joint-4969 Equity:Opening-Balances" in all_text
    # opening balance assertion (1,000.00 on Mar 19)
    assert "balance Assets:CA:BMO:Chequing:Joint-4969" in all_text
    assert "1000.00 CAD" in all_text
    # closing balance assertion (11,649.75 — last running balance)
    assert "11649.75 CAD" in all_text

    # The pad date is one day BEFORE the opening date (Mar 18)
    assert "2024-03-18 pad" in all_text
    # Closing balance assertion is dated one day AFTER last txn (Mar 31)
    assert "2024-03-31 balance" in all_text


# --- PreParser surface ----------------------------------------------------


def test_preparser_can_handle_pdf_extension() -> None:
    p = BmoJointChequingPdfPreParser()
    assert p.can_handle("foo.pdf") is True
    assert p.can_handle("FOO.PDF") is True
    assert p.can_handle("foo.csv") is False


def test_preparser_rejects_bytes_input() -> None:
    p = BmoJointChequingPdfPreParser()
    with pytest.raises(PreParserError, match="path, not bytes"):
        p.extract(b"%PDF-1.4")


def test_preparser_raises_if_year_cannot_be_inferred(tmp_path) -> None:
    pdf = tmp_path / "no-year-in-name.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    p = BmoJointChequingPdfPreParser()
    with pytest.raises(PreParserError, match="statement year"):
        p.extract(str(pdf))


def test_preparser_raises_if_pdf_not_found(tmp_path) -> None:
    p = BmoJointChequingPdfPreParser(statement_year=2024)
    with pytest.raises(PreParserError, match="not found"):
        p.extract(str(tmp_path / "does-not-exist.pdf"))


def test_preparser_account_suffix_mismatch_refuses_import(
    tmp_path, monkeypatch
) -> None:
    """If the PDF doesn't contain the expected 4-969 marker, refuse."""
    pdf = tmp_path / "bmo-2024.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    # Stub out _extract_pdf_text to return content that LACKS the suffix
    monkeypatch.setattr(
        BmoJointChequingPdfPreParser,
        "_extract_pdf_text",
        staticmethod(lambda _path: "PrimaryChequingAccount#9-999\nMar19 Openingbalance 1.00\n"),
    )

    p = BmoJointChequingPdfPreParser(statement_year=2024)
    with pytest.raises(PreParserError, match="account suffix"):
        p.extract(str(pdf))


def test_preparser_happy_path_with_stubbed_pdf_text(tmp_path, monkeypatch) -> None:
    """Full pre-parser pipeline with pdfplumber stubbed out (returns our fixture)."""
    pdf = tmp_path / "bmo-2024-03.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(
        BmoJointChequingPdfPreParser,
        "_extract_pdf_text",
        staticmethod(lambda _path: FIXTURE_PDF_TEXT),
    )

    p = BmoJointChequingPdfPreParser()  # year inferred from filename
    extract = p.extract(str(pdf))

    assert extract.opening_balance == Decimal("1000.00")
    assert extract.opening_date == date(2024, 3, 19)
    assert extract.closing_balance == Decimal("11649.75")
    assert extract.closing_date == date(2024, 3, 30)
    assert len(extract.transactions) == 6
    assert extract.statement_id == "bmo-2024-03"
