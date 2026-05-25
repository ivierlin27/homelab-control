"""Tests for scripts/normalize_bmo_payees.py."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.normalize_bmo_payees import normalize_transactions_text


def test_truncates_footer_in_txn_header() -> None:
    text = (
        '2022-03-31 * "InterestEarned Pleasereportanyerrors,omissions"\n'
        "  Assets:CA:BMO:Savings:Joint-USD-6863  1.06 USD\n"
        "  Income:Interest:Bank  -1.06 USD\n"
    )
    new_text, scanned, changed = normalize_transactions_text(text)
    assert scanned == 1
    assert changed == 1
    assert '2022-03-31 * "InterestEarned"\n' in new_text
    assert "Pleasereportanyerrors" not in new_text.split("\n")[0]


def test_leaves_clean_headers_unchanged() -> None:
    text = '2024-02-01 * "InterestEarned"\n  Assets:CA:BMO:Savings:Joint-CAD-8327  1.00 CAD\n'
    new_text, scanned, changed = normalize_transactions_text(text)
    assert scanned == 0
    assert changed == 0
    assert new_text == text


def test_preserves_pending_flag_and_metadata_tail() -> None:
    text = (
        '2022-04-12 ! "DebitCardPurchase,SUBWAY Pleasereportanyerrors,x"\n'
        "  source_importer: \"bmo-ellowyn-personal-chequing\"\n"
    )
    new_text, _, changed = normalize_transactions_text(text)
    assert changed == 1
    assert '2022-04-12 ! "DebitCardPurchase,SUBWAY"\n' in new_text
    assert 'source_importer: "bmo-ellowyn-personal-chequing"' in new_text
