"""Institution-slug → (PreParser, Importer) registry.

Slugs are the public surface: the `ingest --institution <slug>` CLI takes
them, the audit row records them, the Forgejo runbook lists them. Keep
them stable.

Pattern: `<bank>-<account-role>[-<currency>]`, lowercase, hyphenated.
Examples:
  bmo-joint-chequing
  bmo-joint-savings-usd
  rbc-avion-visa-joint
"""

from __future__ import annotations

from typing import Callable

from .base import Importer, PreParser
from .bmo_joint_chequing_pdf import build_bmo_joint_chequing_importer, build_bmo_joint_chequing_pre_parser

INSTITUTION_BMO_JOINT_CHEQUING = "bmo-joint-chequing"


# Factories return fresh instances per ingest. Importers and PreParsers are
# stateless today, but keep the indirection so tests can pass in mocks.
_PRE_PARSER_FACTORIES: dict[str, Callable[[], PreParser]] = {
    INSTITUTION_BMO_JOINT_CHEQUING: build_bmo_joint_chequing_pre_parser,
}

_IMPORTER_FACTORIES: dict[str, Callable[[], Importer]] = {
    INSTITUTION_BMO_JOINT_CHEQUING: build_bmo_joint_chequing_importer,
}


KNOWN_INSTITUTIONS = frozenset(_IMPORTER_FACTORIES.keys())


def list_institutions() -> list[str]:
    """Sorted list of institution slugs the CLI recognises."""
    return sorted(KNOWN_INSTITUTIONS)


def get_importer(slug: str) -> tuple[PreParser, Importer]:
    """Return a (PreParser, Importer) pair for an institution slug.

    Raises ``KeyError`` if the slug isn't registered — the CLI translates
    that into a friendly error listing valid slugs.
    """
    if slug not in _IMPORTER_FACTORIES:
        raise KeyError(slug)
    return _PRE_PARSER_FACTORIES[slug](), _IMPORTER_FACTORIES[slug]()
