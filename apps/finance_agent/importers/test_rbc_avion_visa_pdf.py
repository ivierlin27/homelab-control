"""Unit tests for the RBC Avion Visa Platinum PDF parser."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.finance_agent.importers.base import PreParserError
from apps.finance_agent.importers.rbc_avion_visa_pdf import (
    PROFILES,
    RbcAvionVisaImporter,
    RbcAvionVisaPdfPreParser,
    parse_statement_text,
    parse_summary,
    resolve_signs,
)


# Synthetic fixture mirroring the real RBC Avion layout — includes:
#   - Two-column header bleed ("$69.56 Minimumpayment $10.00")
#   - Reference number on the next line below each txn
#   - Both bare ($28.21) charges AND -$ payments
#   - Year-rollover (Dec 2021 → Jan 2022)
#   - Multi-line FX info that should be ignored
FIXTURE_TEXT = """\
RBC Avion Visa Platinum
KEVINAENNS 451409******1847
STATEMENTFROMDEC21TOJAN08,2022
1OF2
PREVIOUSACCOUNTBALANCE $500.00 IMPORTANTINFORMATION
TRANSACTION POSTING ACTIVITYDESCRIPTION AMOUNT($)
DATE DATE
DEC22 DEC23 SAVEONFOODS#984LANGLEYBC $28.21 Minimumpayment $10.00
74064494099820114781897 CustomerService 1-800-769-2512
DEC28 DEC29 AIRCANADA0147804901179DULUTHGA $613.24 Creditlimit $5,000.00
24717052272872722170188 ForeignCurrency-USD434.83
JAN05 JAN06 PAYMENT-THANKYOU/PAIEMENT-MERCI -$500.00
74510104106097951387106
TOTALACCOUNTBALANCE $641.45
NEW BALANCE $641.45
"""


# --- summary parsing ------------------------------------------------------


def test_parse_summary_extracts_balances() -> None:
    s = parse_summary(FIXTURE_TEXT)
    assert s.previous_balance == Decimal("500.00")
    assert s.new_balance == Decimal("641.45")


def test_parse_summary_extracts_period_with_rollover() -> None:
    """STATEMENT FROM DEC 21 TO JAN 08, 2022 — start month (Dec) > end
    month (Jan), so start year rolls back to 2021."""
    s = parse_summary(FIXTURE_TEXT)
    assert s.period_end_date == date(2022, 1, 8)
    assert s.period_start_date == date(2021, 12, 21)


def test_parse_summary_same_year_period() -> None:
    text = "STATEMENTFROMAPR09TOMAY08,2024"
    s = parse_summary(text)
    assert s.period_start_date == date(2024, 4, 9)
    assert s.period_end_date == date(2024, 5, 8)


def test_parse_summary_handles_year_on_both_dates() -> None:
    """Jan 2023+ statements have year on BOTH dates:
    'STATEMENTFROMDEC09,2022TOJAN09,2023' (no inference needed)."""
    text = "STATEMENTFROMDEC09,2022TOJAN09,2023"
    s = parse_summary(text)
    assert s.period_start_year == 2022
    assert s.period_start_date == date(2022, 12, 9)
    assert s.period_end_date == date(2023, 1, 9)


def test_year_for_txn_uses_explicit_start_year_when_available() -> None:
    """When both period years are known, txn dating is unambiguous —
    don't fall back to the heuristic which guesses based on
    txn_month > period_end_month."""
    from apps.finance_agent.importers.rbc_avion_visa_pdf import _year_for_txn
    # Dec 2022 - Jan 2023 statement
    assert _year_for_txn(
        12,
        period_end_month=1, period_end_year=2023,
        period_start_month=12, period_start_year=2022,
    ) == 2022
    assert _year_for_txn(
        1,
        period_end_month=1, period_end_year=2023,
        period_start_month=12, period_start_year=2022,
    ) == 2023


def test_parse_summary_falls_back_to_total_account_balance() -> None:
    """If NEW BALANCE is missing for some reason, TOTALACCOUNTBALANCE
    is used as the closing-balance source (same value on real PDFs)."""
    text = "PREVIOUSACCOUNTBALANCE $0.00\nTOTALACCOUNTBALANCE $1,089.09"
    s = parse_summary(text)
    assert s.new_balance == Decimal("1089.09")


# --- txn parsing ----------------------------------------------------------


def test_parse_statement_text_extracts_3_txns() -> None:
    result = parse_statement_text(FIXTURE_TEXT)
    assert len(result.raw_txns) == 3
    # Dec txns roll back to 2021; Jan stays in 2022
    assert result.raw_txns[0].posting_date == date(2021, 12, 23)
    assert result.raw_txns[1].posting_date == date(2021, 12, 29)
    assert result.raw_txns[2].posting_date == date(2022, 1, 6)


def test_parse_statement_text_ignores_right_column_noise() -> None:
    """Each txn line has trailing right-column content ('Minimumpayment
    $10.00' / 'Creditlimit $5,000.00'). Parser must anchor on the LEFTMOST
    $X.XX and ignore the rest — otherwise the wrong amount gets captured."""
    result = parse_statement_text(FIXTURE_TEXT)
    assert result.raw_txns[0].pdf_amount == Decimal("28.21")    # not 10.00
    assert result.raw_txns[1].pdf_amount == Decimal("613.24")   # not 5000.00


def test_parse_statement_text_handles_negative_payment() -> None:
    """Payments show as '-$X.XX' (literal minus before $). Sign captured."""
    result = parse_statement_text(FIXTURE_TEXT)
    pmt = result.raw_txns[2]
    assert "PAYMENT" in pmt.description
    assert pmt.pdf_amount == Decimal("-500.00")


def test_parse_statement_text_skips_ref_lines() -> None:
    """Lines that are just a reference number (17 digits) must not be
    parsed as txns — they don't start with a month abbrev."""
    result = parse_statement_text(FIXTURE_TEXT)
    descs = [r.description for r in result.raw_txns]
    assert not any(d.startswith("74064494") for d in descs)


def test_parse_statement_text_raises_if_no_period_line() -> None:
    text = (
        "PREVIOUSACCOUNTBALANCE $0.00\n"
        "DEC22 DEC23 SOMETHING $10.00\n"
        "NEW BALANCE $10.00\n"
    )
    with pytest.raises(PreParserError, match="STATEMENT FROM"):
        parse_statement_text(text)


# --- resolve_signs --------------------------------------------------------


def test_resolve_signs_negates_pdf_amounts() -> None:
    """PDF sign convention: charge=positive, payment=negative.
    Liability convention: charge=negative delta, payment=positive delta.
    So liability_delta = -pdf_amount."""
    result = parse_statement_text(FIXTURE_TEXT)
    signed = resolve_signs(result, currency="CAD")
    assert signed[0].amount == Decimal("-28.21")    # charge → owe more
    assert signed[1].amount == Decimal("-613.24")   # charge → owe more
    assert signed[2].amount == Decimal("500.00")    # payment → owe less


def test_resolve_signs_self_validates() -> None:
    """Sum of liability deltas should equal closing - opening (both
    negative). Opening = -500, closing = -641.45, expected = -141.45.
    Sum: -28.21 + -613.24 + 500.00 = -141.45 ✓."""
    result = parse_statement_text(FIXTURE_TEXT)
    signed = resolve_signs(result, currency="CAD")
    delta = sum((t.amount for t in signed), Decimal("0"))
    assert delta == Decimal("-141.45")


def test_resolve_signs_raises_on_mismatch() -> None:
    tampered = FIXTURE_TEXT.replace("$641.45", "$999.99")
    result = parse_statement_text(tampered)
    with pytest.raises(PreParserError, match="reconciliation failed"):
        resolve_signs(result, currency="CAD")


# --- full importer pipeline ----------------------------------------------


def test_importer_renders_pad_balance_txns_closing() -> None:
    profile = PROFILES["rbc-avion-1847"]
    result = parse_statement_text(FIXTURE_TEXT)
    signed = resolve_signs(result, currency="CAD")
    s = result.summary

    from apps.finance_agent.importers.base import StatementExtract

    extract = StatementExtract(
        transactions=signed,
        opening_date=s.period_start_date,
        opening_balance=-s.previous_balance,
        closing_date=s.period_end_date,
        closing_balance=-s.new_balance,
    )
    importer = RbcAvionVisaImporter(profile=profile)
    entries = importer.render(extract)

    # 3 txns + 1 pad + 1 opening + 1 closing = 6 entries
    assert len(entries) == 6
    text = "".join(e.text for e in entries)
    assert "Liabilities:CA:RBC:CreditCard:AvionVisaPlatinum-Joint-1847" in text
    # Liability convention: balance assertions are negative
    assert "-500.00 CAD" in text
    assert "-641.45 CAD" in text


# --- PreParser surface ----------------------------------------------------


def test_preparser_account_verification_rejects_wrong_pdf(tmp_path, monkeypatch) -> None:
    pdf = tmp_path / "wrong.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        RbcAvionVisaPdfPreParser,
        "_extract_pdf_text",
        staticmethod(lambda _path: "451409******9999 SOMEHEADER\n"
                                   "PREVIOUSACCOUNTBALANCE $0.00\n"
                                   "STATEMENTFROMAPR09TOMAY08,2024\n"
                                   "NEW BALANCE $0.00\n"),
    )
    p = RbcAvionVisaPdfPreParser(profile=PROFILES["rbc-avion-1847"])
    with pytest.raises(PreParserError, match="last-4"):
        p.extract(str(pdf))


def test_preparser_full_pipeline_with_stubbed_pdf(tmp_path, monkeypatch) -> None:
    pdf = tmp_path / "rbc-2022-01.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        RbcAvionVisaPdfPreParser,
        "_extract_pdf_text",
        staticmethod(lambda _path: FIXTURE_TEXT),
    )
    p = RbcAvionVisaPdfPreParser(profile=PROFILES["rbc-avion-1847"])
    extract = p.extract(str(pdf))

    assert len(extract.transactions) == 3
    assert extract.opening_balance == Decimal("-500.00")
    assert extract.closing_balance == Decimal("-641.45")
    assert extract.opening_date == date(2021, 12, 21)
    assert extract.closing_date == date(2022, 1, 8)
    assert extract.statement_id == "rbc-2022-01"


# --- registry integration -------------------------------------------------


def test_rbc_avion_profile_registered_via_registry() -> None:
    from apps.finance_agent.importers import KNOWN_INSTITUTIONS, get_importer
    assert "rbc-avion-1847" in KNOWN_INSTITUTIONS
    pre_parser, importer = get_importer("rbc-avion-1847")
    assert pre_parser.institution == "rbc-avion-1847"
    assert importer.source_account == (
        "Liabilities:CA:RBC:CreditCard:AvionVisaPlatinum-Joint-1847"
    )
