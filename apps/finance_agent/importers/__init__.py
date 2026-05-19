"""Per-institution statement importers.

Pipeline (per institution):

    PDF / OFX / CSV file
        ↓
    PreParser     — institution-specific. PDFs use pdfplumber + regex/heuristics
                    to produce a list[ExtractedTransaction]. OFX/CSV imports
                    can skip the pre-parser entirely.
        ↓
    Importer      — converts ExtractedTransaction → list[BeancountEntry].
                    Knows the source account (e.g. Assets:CA:BMO:Chequing:Joint-4969),
                    fills the offsetting leg with Expenses:Uncategorized (F6 will
                    layer smart_importer on top to predict real categories).
        ↓
    Ingest layer  — appends entries to ~/finance/ledger/transactions.beancount,
                    writes hash-chained audit row, runs bean-check.

F4a (this commit) ships interfaces + the registry + a BMO joint-chequing stub
that raises NotImplementedError on extract — the real regex lands in F4b once
we have a sanitized pdfplumber dump of a real BMO statement.
"""

from .base import (
    BeancountEntry,
    ExtractedTransaction,
    Importer,
    ImporterError,
    PreParser,
    PreParserError,
)
from .registry import (
    INSTITUTION_BMO_JOINT_CHEQUING,
    KNOWN_INSTITUTIONS,
    get_importer,
    list_institutions,
)

__all__ = [
    "BeancountEntry",
    "ExtractedTransaction",
    "Importer",
    "ImporterError",
    "PreParser",
    "PreParserError",
    "INSTITUTION_BMO_JOINT_CHEQUING",
    "KNOWN_INSTITUTIONS",
    "get_importer",
    "list_institutions",
]
