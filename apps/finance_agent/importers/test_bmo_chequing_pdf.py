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
from apps.finance_agent.importers.bmo_chequing_pdf import (
    PROFILES,
    BmoChequingImporter,
    BmoChequingPdfPreParser,
    _verify_account_match,
    find_period_end_date,
    infer_statement_year_from_filename,
    parse_statement_text,
    resolve_signs,
)

# Default profile used by these tests (the original joint-chequing 4969).
JOINT_PROFILE = PROFILES["bmo-joint-chequing"]


def _make_joint_pre_parser(**kwargs) -> BmoChequingPdfPreParser:
    return BmoChequingPdfPreParser(profile=JOINT_PROFILE, **kwargs)


def _make_joint_importer() -> BmoChequingImporter:
    return BmoChequingImporter(profile=JOINT_PROFILE)


FIXTURE_PDF_TEXT = """\
For the period ending April 1, 2024
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


def test_parse_statement_text_does_not_glue_statement_footer() -> None:
    text = FIXTURE_PDF_TEXT.replace(
        "Apr01 Closingtotals 1,850.25 12,500.00",
        "Mar30 InterestEarned 1.06 11,650.81\n"
        "Pleasereportanyerrors,omissionsorirregularitiesinwriting\n"
        "BankofMontreal,BankofMontrealMortgageCorporation\n"
        "Apr01 Closingtotals 1,850.25 12,500.00",
    )
    result = parse_statement_text(text, anchor_year=2024)
    interest = [t for t in result.raw_txns if "InterestEarned" in t.description]
    assert len(interest) == 1
    assert interest[0].description == "InterestEarned"
    signed, _, _ = resolve_signs(result, currency="CAD")
    interest_signed = [t for t in signed if t.description == "InterestEarned"]
    assert len(interest_signed) == 1


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
    """Heuristic fallback: with no period line in PDF, statement period
    Dec → Jan should bump year forward at the wrap based on month-jump."""
    rollover = (
        "Dec28 Openingbalance 100.00\n"
        "Dec29 SomethingDebit 10.00 90.00\n"
        "Jan02 SomethingCredit 50.00 140.00\n"
    )
    result = parse_statement_text(rollover, anchor_year=2023)
    assert result.opening_date == date(2023, 12, 28)
    assert result.raw_txns[0].posting_date == date(2023, 12, 29)
    assert result.raw_txns[1].posting_date == date(2024, 1, 2)


def test_parse_statement_text_handles_dec_to_jan_with_period_line() -> None:
    """Authoritative period-end mode: a real BMO statement ending in
    January 2022 has Dec transactions that must roll BACKWARD to 2021,
    not forward. Mirrors the real 2022-01-18 statement where the period
    line says "For the period ending January 18, 2022" but the first
    transaction is Dec 18, 2021.
    """
    real_layout = (
        "For the period ending January 18, 2022\n"
        "Dec18 Openingbalance 14827.39\n"
        "Dec24 DirectDeposit,SOMEPAYER 0.19 14827.58\n"
        "Dec31 DirectDeposit,EMPLOYER 5112.36 19939.94\n"
        "Jan04 ScheduledTransfer 200.00 19739.94\n"
        "Jan18 Closingtotals 200.00 5112.55\n"
    )
    # anchor_year=9999 to prove period-line overrides
    result = parse_statement_text(real_layout, anchor_year=9999)
    # Opening Dec 18 → 2021 (month > period_end_month=1)
    assert result.opening_date == date(2021, 12, 18)
    # Dec txns → 2021
    assert result.raw_txns[0].posting_date == date(2021, 12, 24)
    assert result.raw_txns[1].posting_date == date(2021, 12, 31)
    # Jan txn → 2022 (month <= period_end_month=1)
    assert result.raw_txns[2].posting_date == date(2022, 1, 4)
    # And the period-end date is correctly extracted
    assert result.period_end_date == date(2022, 1, 18)


def test_parse_statement_text_jan_to_jan_next_year() -> None:
    """Edge case: statement ending January 2023 (so Dec txns are 2022,
    Jan txns are 2023). Period-end-mode handles this symmetrically with
    the 2022-01 case above."""
    layout = (
        "For the period ending January 17, 2023\n"
        "Dec17 Openingbalance 500.00\n"
        "Dec20 SomethingDebit 100.00 400.00\n"
        "Jan05 SomethingCredit 50.00 450.00\n"
    )
    result = parse_statement_text(layout, anchor_year=2023)
    assert result.opening_date == date(2022, 12, 17)
    assert result.raw_txns[0].posting_date == date(2022, 12, 20)
    assert result.raw_txns[1].posting_date == date(2023, 1, 5)


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
    # Closing date prefers the period_end_date from "For the period ending Apr 1, 2024"
    # over the last txn's date (Mar 30, 2024).
    assert closing_date == date(2024, 4, 1)


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


# --- find_period_end_date -------------------------------------------------


def test_find_period_end_date_canonical_format() -> None:
    """The exact line Kevin reported from a real BMO statement."""
    assert find_period_end_date("For the period ending April 18, 2022") == date(
        2022, 4, 18
    )


def test_find_period_end_date_no_comma() -> None:
    assert find_period_end_date("For the period ending May 1 2023") == date(2023, 5, 1)


def test_find_period_end_date_case_insensitive() -> None:
    assert find_period_end_date("for THE period ending MARCH 31, 2024") == date(
        2024, 3, 31
    )


def test_find_period_end_date_glued_words() -> None:
    """pdfplumber may strip inter-word spaces in some PDFs."""
    assert find_period_end_date("Fortheperiodending April1,2024") == date(2024, 4, 1)


def test_find_period_end_date_returns_none_when_absent() -> None:
    assert find_period_end_date("just some other text\nMar20 Openingbalance 1.00") is None


def test_find_period_end_date_returns_first_match_when_multiple() -> None:
    text = "For the period ending April 1, 2024\nFor the period ending May 1, 2024"
    assert find_period_end_date(text) == date(2024, 4, 1)


def test_parse_statement_text_uses_period_year_over_anchor() -> None:
    """If the period line is present, its year overrides anchor_year."""
    text = (
        "For the period ending April 1, 2024\n"
        "Mar20 Openingbalance 100.00\n"
        "Mar21 Foo 10.00 90.00\n"
    )
    # Operator passes wrong year as anchor; period line wins.
    result = parse_statement_text(text, anchor_year=1999)
    assert result.period_end_date == date(2024, 4, 1)
    assert result.opening_date == date(2024, 3, 20)
    assert result.raw_txns[0].posting_date == date(2024, 3, 21)


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

    importer = _make_joint_importer()
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
    # Closing balance assertion is dated one day AFTER closing_date.
    # closing_date here is the period_end (Apr 1), so assertion is Apr 2.
    assert "2024-04-02 balance" in all_text


# --- PreParser surface ----------------------------------------------------


def test_preparser_can_handle_pdf_extension() -> None:
    p = _make_joint_pre_parser()
    assert p.can_handle("foo.pdf") is True
    assert p.can_handle("FOO.PDF") is True
    assert p.can_handle("foo.csv") is False


def test_preparser_rejects_bytes_input() -> None:
    p = _make_joint_pre_parser()
    with pytest.raises(PreParserError, match="path, not bytes"):
        p.extract(b"%PDF-1.4")


def test_preparser_raises_if_year_cannot_be_inferred(tmp_path, monkeypatch) -> None:
    """Year inference falls back through period-line → filename → flag.
    With no period line in the PDF AND no year in the filename, the
    pre-parser must refuse rather than silently guess.
    """
    pdf = tmp_path / "no-year-in-name.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        BmoChequingPdfPreParser,
        "_extract_pdf_text",
        staticmethod(lambda _path: "PrimaryChequingAccount#4-969\n"),
    )

    p = _make_joint_pre_parser()
    with pytest.raises(PreParserError, match="statement year"):
        p.extract(str(pdf))


def test_preparser_raises_if_pdf_not_found(tmp_path) -> None:
    p = _make_joint_pre_parser(statement_year=2024)
    with pytest.raises(PreParserError, match="not found"):
        p.extract(str(tmp_path / "does-not-exist.pdf"))


def test_preparser_account_suffix_mismatch_refuses_import(
    tmp_path, monkeypatch
) -> None:
    """If the PDF doesn't contain any digit pattern matching last-4, refuse."""
    pdf = tmp_path / "bmo-2024.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        BmoChequingPdfPreParser,
        "_extract_pdf_text",
        staticmethod(lambda _path: "PrimaryChequingAccount#9-999\nMar19 Openingbalance 1.00\n"),
    )

    p = _make_joint_pre_parser(statement_year=2024)
    with pytest.raises(PreParserError, match="last-4"):
        p.extract(str(pdf))


def test_preparser_happy_path_with_stubbed_pdf_text(tmp_path, monkeypatch) -> None:
    """Full pre-parser pipeline with pdfplumber stubbed out (returns our fixture)."""
    pdf = tmp_path / "bmo-2024-03.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(
        BmoChequingPdfPreParser,
        "_extract_pdf_text",
        staticmethod(lambda _path: FIXTURE_PDF_TEXT),
    )

    p = _make_joint_pre_parser()
    extract = p.extract(str(pdf))

    assert extract.opening_balance == Decimal("1000.00")
    assert extract.opening_date == date(2024, 3, 19)
    assert extract.closing_balance == Decimal("11649.75")
    assert extract.closing_date == date(2024, 4, 1)  # period_end, not last txn
    assert len(extract.transactions) == 6
    assert extract.statement_id == "bmo-2024-03"


# --- Profile system: account-suffix verification + multi-account routing ----


def test_verify_account_match_accepts_dashed_format() -> None:
    """The canonical BMO format: '4-969' in the PDF for account 4969."""
    text = "PrimaryChequingAccount#4-969\nMar19 Openingbalance 0.00\n"
    assert _verify_account_match(text, "4969") is True


def test_verify_account_match_accepts_bare_digits() -> None:
    """Some BMO statement variants render the last-4 as bare digits."""
    text = "Statement for account ending 4256\n"
    assert _verify_account_match(text, "4256") is True


def test_verify_account_match_rejects_unrelated_digits() -> None:
    """Random digits matching neither dashed nor bare format must fail."""
    text = "PrimaryChequingAccount#9-999\nMar19 Openingbalance 0.00\n"
    assert _verify_account_match(text, "4256") is False


def test_verify_account_match_skips_when_no_last4() -> None:
    """Empty last4 means "skip verification" (escape hatch for new profiles)."""
    assert _verify_account_match("any text at all", "") is True


def test_other_profile_routes_through_same_parser(tmp_path, monkeypatch) -> None:
    """Smoke: a non-joint profile (Kevin 4256) parses the same fixture
    structure correctly, producing entries against its own source account.
    Real ingest will use the operator's actual PDFs; this just proves the
    profile system doesn't accidentally hard-code the joint account."""
    pdf = tmp_path / "bmo-kevin-2024.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    # Reuse the fixture but swap the account marker so it matches Kevin's last-4
    swapped_text = FIXTURE_PDF_TEXT.replace("4-969", "4-256")
    monkeypatch.setattr(
        BmoChequingPdfPreParser,
        "_extract_pdf_text",
        staticmethod(lambda _path: swapped_text),
    )

    kevin_profile = PROFILES["bmo-kevin-chequing"]
    p = BmoChequingPdfPreParser(profile=kevin_profile)
    extract = p.extract(str(pdf))

    assert extract.opening_balance == Decimal("1000.00")
    assert len(extract.transactions) == 6

    importer = BmoChequingImporter(profile=kevin_profile)
    entries = importer.render(extract)
    all_text = "".join(e.text for e in entries)

    # Entries are rendered against Kevin's account, NOT the joint account
    assert "Assets:CA:BMO:Chequing:Kevin-4256" in all_text
    assert "Assets:CA:BMO:Chequing:Joint-4969" not in all_text
