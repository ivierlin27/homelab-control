"""Unit tests for the institution registry."""

from __future__ import annotations

import pytest

from apps.finance_agent.importers import (
    KNOWN_INSTITUTIONS,
    get_importer,
    list_institutions,
)
from apps.finance_agent.importers.bmo_cashback_mc_pdf import (
    PROFILES as MC_PROFILES,
)
from apps.finance_agent.importers.bmo_chequing_pdf import PROFILES

# All BMO chequing slugs currently registered. Add as profiles land.
BMO_CHEQUING_SLUGS = (
    "bmo-joint-chequing",
    "bmo-kevin-chequing",
    "bmo-jennifer-chequing",
    "bmo-makaely-personal-chequing",
    "bmo-ellowyn-personal-chequing",
    "bmo-jennifer-books-7179",
    "bmo-makaely-education-4221",
    "bmo-ellowyn-education-4248",
    "bmo-joint-savings-cad-8327",
    "bmo-joint-savings-usd-6863",
)


def test_known_institutions_contains_all_bmo_chequing_profiles() -> None:
    for slug in BMO_CHEQUING_SLUGS:
        assert slug in KNOWN_INSTITUTIONS, f"missing slug: {slug}"
    # Sanity: each registered slug has a matching profile in the source module
    for slug in BMO_CHEQUING_SLUGS:
        assert slug in PROFILES


def test_list_institutions_is_sorted() -> None:
    slugs = list_institutions()
    assert slugs == sorted(slugs)
    for s in BMO_CHEQUING_SLUGS:
        assert s in slugs


@pytest.mark.parametrize("slug", BMO_CHEQUING_SLUGS)
def test_get_importer_returns_profile_driven_pair(slug: str) -> None:
    pre_parser, importer = get_importer(slug)
    profile = PROFILES[slug]
    assert pre_parser.institution == slug
    assert importer.institution == slug
    assert importer.source_account == profile.source_account
    assert importer.currency == profile.currency
    assert importer.counter_account == "Expenses:Uncategorized"


def test_get_importer_unknown_slug_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        get_importer("definitely-not-a-real-bank")


def test_each_profile_has_unique_source_account() -> None:
    """Catches copy-paste errors where two slugs accidentally point at the
    same Beancount account — that would silently merge two real-world
    accounts on ingest. Spans both chequing and MC profile spaces."""
    all_profiles = list(PROFILES.values()) + list(MC_PROFILES.values())
    accounts = [p.source_account for p in all_profiles]
    assert len(accounts) == len(set(accounts)), "duplicate source_account across PROFILES"


def test_mastercard_profile_registered() -> None:
    from apps.finance_agent.importers import KNOWN_INSTITUTIONS, get_importer
    assert "bmo-cashback-mc-0706" in KNOWN_INSTITUTIONS
    pre_parser, importer = get_importer("bmo-cashback-mc-0706")
    assert pre_parser.institution == "bmo-cashback-mc-0706"
    assert importer.source_account == "Liabilities:CA:BMO:CreditCard:CashbackMC-Joint-0706"


@pytest.mark.parametrize("slug", BMO_CHEQUING_SLUGS)
def test_pre_parser_can_handle_only_pdf_files(slug: str) -> None:
    pre_parser, _ = get_importer(slug)
    assert pre_parser.can_handle("statement.pdf") is True
    assert pre_parser.can_handle("statement.PDF") is True
    assert pre_parser.can_handle("statement.csv") is False
    assert pre_parser.can_handle(b"%PDF-1.4") is False
