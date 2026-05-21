"""Institution-slug → (PreParser, Importer) registry.

Slugs are the public surface: the `ingest --institution <slug>` CLI takes
them, the audit row records them, the Forgejo runbook lists them. Keep
them stable.

Pattern: `<bank>-<owner-or-role>-<account-type>[-<currency>]`, lowercase,
hyphenated. Examples:
  bmo-joint-chequing
  bmo-kevin-chequing
  bmo-joint-savings-usd      (deferred; profile not yet registered)
  rbc-avion-visa-joint       (deferred; different parser entirely)

Each importer module owns its own {slug: factory} maps; this module just
unions them. Add a new module → import its factory maps here.
"""

from __future__ import annotations

from typing import Callable

from .base import Importer, PreParser
from .bmo_chequing_pdf import (
    IMPORTER_FACTORIES as _BMO_CHEQUING_IMPORTER_FACTORIES,
    PRE_PARSER_FACTORIES as _BMO_CHEQUING_PRE_PARSER_FACTORIES,
)
from .bmo_cashback_mc_pdf import (
    IMPORTER_FACTORIES as _BMO_MC_IMPORTER_FACTORIES,
    PRE_PARSER_FACTORIES as _BMO_MC_PRE_PARSER_FACTORIES,
)

# Factories return fresh instances per ingest. Importers and PreParsers are
# stateless today, but keep the indirection so tests can pass in mocks.
_PRE_PARSER_FACTORIES: dict[str, Callable[[], PreParser]] = {
    **_BMO_CHEQUING_PRE_PARSER_FACTORIES,
    **_BMO_MC_PRE_PARSER_FACTORIES,
}

_IMPORTER_FACTORIES: dict[str, Callable[[], Importer]] = {
    **_BMO_CHEQUING_IMPORTER_FACTORIES,
    **_BMO_MC_IMPORTER_FACTORIES,
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
