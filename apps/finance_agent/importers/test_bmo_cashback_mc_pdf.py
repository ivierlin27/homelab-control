"""Unit tests for the BMO Cash Back Mastercard PDF parser."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.finance_agent.importers.base import PreParserError
from apps.finance_agent.importers.bmo_cashback_mc_pdf import (
    PROFILES,
    BmoCashbackMcImporter,
    BmoCashbackMcPdfPreParser,
    parse_statement_text,
    parse_summary,
    resolve_signs,
)


# Synthetic fixture mirroring real BMO MC layout. Includes:
#   - Header with PreviousBalance + NewBalance lines
#   - Period range line
#   - 3 charges + 1 payment (CR)
#   - Year-rollover (Dec txns + Jan posting on a Jan-ending statement)
#   - Noise lines (Page footer, "Continued on page", Bonus reward block)
FIXTURE_TEXT = """\
BMOCashBackMastercard
StatementDate Jan.19,2022
CardNumber 5191230213430706 PreviousBalance,Dec.19,2021 $402.23
CustomerName MRKEVINENNS Purchasesandothercharges +250.00
TotalInterestCharges 0.00
PaymentsandCredits -402.23
NewBalance,Jan.19,2022 $250.00
MinimumPaymentDue $10.00
PaymentDueDate Feb.10,2022
YourCreditLimit $5,000.00
PERIODCOVEREDBYTHISSTATEMENT
Dec.20,2021-Jan.19,2022
TRANS POSTING
DATE DATE DESCRIPTION REFERENCENO. AMOUNT($)
CardNumber:5191230213430706
Dec.22 Dec.23 TACOHOLICMEXICAN LANGLEY BC 004011680156 50.00
Dec.29 Dec.29 TRSFFROM/DEACCT/CPT 0764-XXXX-969 S670159OBPP 402.23CR
Jan.05 Jan.06 SHOPPERSDRUGMART LANGLEY BC 463681212421 100.00
Jan.12 Jan.13 COSTCOWHOLESALE LANGLEY BC 800200825439 100.00
Continuedonpage2
Page1of2
Bonusreward(s)thisstatement Amountearned
GroceryBonus-2.5% $2.50
EstimatedTimeToRepay:Ifyouonlymaketheminimumpayment,etcetc
"""


# --- summary parsing ------------------------------------------------------


def test_parse_summary_extracts_previous_and_new_balance() -> None:
    s = parse_summary(FIXTURE_TEXT)
    assert s.previous_balance == Decimal("402.23")
    assert s.previous_date == date(2021, 12, 19)
    assert s.new_balance == Decimal("250.00")
    assert s.new_date == date(2022, 1, 19)
    assert s.period_start == date(2021, 12, 20)
    assert s.period_end == date(2022, 1, 19)
    assert s.statement_date == date(2022, 1, 19)


def test_parse_summary_handles_dollar_sign_and_commas() -> None:
    text = (
        "PreviousBalance,Apr.19,2024 $1,234.56\n"
        "NewBalance,May.19,2024 $9,876.54\n"
    )
    s = parse_summary(text)
    assert s.previous_balance == Decimal("1234.56")
    assert s.new_balance == Decimal("9876.54")


def test_parse_summary_handles_periodless_month_format() -> None:
    """BMO occasionally drops the period after the month abbreviation —
    "May19,2022" instead of "May.19,2022". Real example: 2022-05-19
    statement file. All date regexes accept either form."""
    text = (
        "StatementDate May19,2022\n"
        "PreviousBalance,Apr.19,2022 $1,109.67\n"
        "NewBalance,May19,2022 $1,634.02\n"
        "PERIODCOVEREDBYTHISSTATEMENT\n"
        "Apr.20,2022-May19,2022\n"
    )
    s = parse_summary(text)
    assert s.statement_date == date(2022, 5, 19)
    assert s.previous_date == date(2022, 4, 19)
    assert s.previous_balance == Decimal("1109.67")
    assert s.new_date == date(2022, 5, 19)
    assert s.new_balance == Decimal("1634.02")
    assert s.period_start == date(2022, 4, 20)
    assert s.period_end == date(2022, 5, 19)


def test_parse_summary_returns_none_fields_when_missing() -> None:
    s = parse_summary("Hello world")
    assert s.previous_balance is None
    assert s.new_balance is None
    assert s.period_end is None


# --- transaction parsing --------------------------------------------------


def test_parse_statement_text_extracts_4_transactions() -> None:
    result = parse_statement_text(FIXTURE_TEXT)
    assert len(result.raw_txns) == 4
    # Dec txns must roll back to 2021; Jan to 2022 (period ends Jan 2022)
    assert result.raw_txns[0].posting_date == date(2021, 12, 23)
    assert result.raw_txns[1].posting_date == date(2021, 12, 29)
    assert result.raw_txns[2].posting_date == date(2022, 1, 6)
    assert result.raw_txns[3].posting_date == date(2022, 1, 13)


def test_parse_statement_text_marks_cr_correctly() -> None:
    """The CR suffix is the sign discriminator — only the payment line has it."""
    result = parse_statement_text(FIXTURE_TEXT)
    cr_flags = [r.cr_flag for r in result.raw_txns]
    assert cr_flags == [False, True, False, False]


def test_parse_statement_text_extracts_descriptions() -> None:
    result = parse_statement_text(FIXTURE_TEXT)
    assert "TACOHOLICMEXICAN" in result.raw_txns[0].description
    assert "TRSFFROM/DEACCT/CPT" in result.raw_txns[1].description
    assert "SHOPPERSDRUGMART" in result.raw_txns[2].description


def test_parse_statement_text_filters_noise() -> None:
    """Bonus reward block, page footer, header rows — none become txns."""
    result = parse_statement_text(FIXTURE_TEXT)
    descs = " ".join(r.description for r in result.raw_txns)
    assert "Bonusreward" not in descs
    assert "GroceryBonus" not in descs
    assert "Continuedon" not in descs
    assert "Page" not in descs


def test_parse_statement_text_handles_txn_without_reference_number() -> None:
    """System-posted lines like INTERESTPURCHASES have no reference number
    — just dates + description + amount. Real example: 2022-10-19.pdf
    contains "Oct.19 Oct.19 INTERESTPURCHASES 48.48" which broke the
    first regex pass."""
    text = (
        "PreviousBalance,Sep.19,2022 $100.00\n"
        "NewBalance,Oct.19,2022 $148.48\n"
        "PERIODCOVEREDBYTHISSTATEMENT\n"
        "Sep.20,2022-Oct.19,2022\n"
        "Oct.19 Oct.19 INTERESTPURCHASES 48.48\n"
    )
    result = parse_statement_text(text)
    assert len(result.raw_txns) == 1
    assert result.raw_txns[0].description == "INTERESTPURCHASES"
    assert result.raw_txns[0].amount == Decimal("48.48")
    assert result.raw_txns[0].cr_flag is False
    assert result.raw_txns[0].posting_date == date(2022, 10, 19)


def test_parse_statement_text_raises_if_no_period_end() -> None:
    bare = (
        "BMOCashBackMastercard\n"
        "Dec.22 Dec.23 SOMEPURCHASE LANGLEY BC 004011680156 50.00\n"
    )
    with pytest.raises(PreParserError, match="period end"):
        parse_statement_text(bare)


# --- resolve_signs --------------------------------------------------------


def test_resolve_signs_assigns_liability_convention() -> None:
    """Charges = negative delta to liability (we owe more);
    payments (CR) = positive delta to liability (we owe less)."""
    result = parse_statement_text(FIXTURE_TEXT)
    signed = resolve_signs(result, currency="CAD")

    assert signed[0].amount == Decimal("-50.00")    # charge
    assert signed[1].amount == Decimal("402.23")    # payment (CR)
    assert signed[2].amount == Decimal("-100.00")   # charge
    assert signed[3].amount == Decimal("-100.00")   # charge


def test_resolve_signs_self_validates_against_summary() -> None:
    """Sum of signed deltas should equal closing - opening (both negative).
    Opening = -402.23, closing = -250.00, expected delta = +152.23.
    Sum of fixture txns: -50 + 402.23 - 100 - 100 = +152.23. ✓"""
    result = parse_statement_text(FIXTURE_TEXT)
    signed = resolve_signs(result, currency="CAD")  # should NOT raise

    delta = sum((t.amount for t in signed), Decimal("0"))
    expected = Decimal("-250.00") - Decimal("-402.23")
    assert delta == expected


def test_resolve_signs_raises_when_sum_mismatches() -> None:
    """If the parser missed a txn (synthesised here by tampering with the
    fixture's NewBalance), reconciliation must fail loudly."""
    tampered = FIXTURE_TEXT.replace("$250.00", "$999.99")
    result = parse_statement_text(tampered)
    with pytest.raises(PreParserError, match="reconciliation failed"):
        resolve_signs(result, currency="CAD")


# --- full importer pipeline ----------------------------------------------


def test_importer_renders_pad_balance_txns_closing_for_liability() -> None:
    profile = PROFILES["bmo-cashback-mc-0706"]
    result = parse_statement_text(FIXTURE_TEXT)
    signed = resolve_signs(result, currency="CAD")
    s = result.summary

    from apps.finance_agent.importers.base import StatementExtract

    extract = StatementExtract(
        transactions=signed,
        opening_date=s.previous_date,
        opening_balance=-s.previous_balance,    # liability convention
        closing_date=s.new_date,
        closing_balance=-s.new_balance,
    )

    importer = BmoCashbackMcImporter(profile=profile)
    entries = importer.render(extract)

    # 4 txns + 1 pad + 1 opening balance + 1 closing balance = 7 entries
    assert len(entries) == 7

    text = "".join(e.text for e in entries)
    # Opening balance asserted as NEGATIVE (liability convention)
    assert "balance Liabilities:CA:BMO:CreditCard:CashbackMC-Joint-0706" in text
    assert "-402.23 CAD" in text
    # Closing balance asserted as NEGATIVE
    assert "-250.00 CAD" in text
    # Pad emitted (opening != 0, no prior balance in ledger — ingest may drop)
    assert "pad Liabilities:CA:BMO:CreditCard:CashbackMC-Joint-0706" in text


# --- PreParser surface ----------------------------------------------------


def test_preparser_account_verification_rejects_wrong_pdf(tmp_path, monkeypatch) -> None:
    pdf = tmp_path / "wrong.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        BmoCashbackMcPdfPreParser,
        "_extract_pdf_text",
        staticmethod(lambda _path: "PreviousBalance,Oct.19,2021 $1.00\n"
                                   "NewBalance,Nov.19,2021 $2.00\n"
                                   "PERIODCOVEREDBYTHISSTATEMENT\n"
                                   "Oct.20,2021-Nov.19,2021\n"
                                   "CardNumber 9999\n"),
    )
    p = BmoCashbackMcPdfPreParser(profile=PROFILES["bmo-cashback-mc-0706"])
    with pytest.raises(PreParserError, match="last-4"):
        p.extract(str(pdf))


def test_preparser_full_pipeline_with_stubbed_pdf(tmp_path, monkeypatch) -> None:
    pdf = tmp_path / "bmo-mc-2022-01.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        BmoCashbackMcPdfPreParser,
        "_extract_pdf_text",
        staticmethod(lambda _path: FIXTURE_TEXT),
    )

    p = BmoCashbackMcPdfPreParser(profile=PROFILES["bmo-cashback-mc-0706"])
    extract = p.extract(str(pdf))

    assert len(extract.transactions) == 4
    # opening and closing in Beancount liability convention (negative)
    assert extract.opening_balance == Decimal("-402.23")
    assert extract.closing_balance == Decimal("-250.00")
    # opening_date is period_start (Dec 20), NOT previous_date (Dec 19).
    # See the PreParser comment for why — Beancount assertion semantics.
    assert extract.opening_date == date(2021, 12, 20)
    assert extract.closing_date == date(2022, 1, 19)
    assert extract.statement_id == "bmo-mc-2022-01"
