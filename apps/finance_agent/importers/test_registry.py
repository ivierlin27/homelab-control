"""Unit tests for the institution registry (F4a)."""

from __future__ import annotations

import pytest

from apps.finance_agent.importers import (
    INSTITUTION_BMO_JOINT_CHEQUING,
    KNOWN_INSTITUTIONS,
    get_importer,
    list_institutions,
)
from apps.finance_agent.importers.bmo_joint_chequing_pdf import (
    SOURCE_ACCOUNT as BMO_JOINT_SOURCE,
)


def test_known_institutions_contains_bmo_joint_chequing() -> None:
    assert INSTITUTION_BMO_JOINT_CHEQUING in KNOWN_INSTITUTIONS
    assert INSTITUTION_BMO_JOINT_CHEQUING == "bmo-joint-chequing"


def test_list_institutions_is_sorted() -> None:
    slugs = list_institutions()
    assert slugs == sorted(slugs)
    assert INSTITUTION_BMO_JOINT_CHEQUING in slugs


def test_get_importer_returns_paired_pre_parser_and_importer() -> None:
    pre_parser, importer = get_importer(INSTITUTION_BMO_JOINT_CHEQUING)
    assert pre_parser.institution == INSTITUTION_BMO_JOINT_CHEQUING
    assert importer.institution == INSTITUTION_BMO_JOINT_CHEQUING
    assert importer.source_account == BMO_JOINT_SOURCE
    assert importer.currency == "CAD"
    assert importer.counter_account == "Expenses:Uncategorized"


def test_get_importer_unknown_slug_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        get_importer("definitely-not-a-real-bank")


def test_pre_parser_can_handle_only_pdf_files() -> None:
    pre_parser, _ = get_importer(INSTITUTION_BMO_JOINT_CHEQUING)
    assert pre_parser.can_handle("statement.pdf") is True
    assert pre_parser.can_handle("statement.PDF") is True
    assert pre_parser.can_handle("statement.csv") is False
    assert pre_parser.can_handle(b"%PDF-1.4") is False  # bytes not supported
