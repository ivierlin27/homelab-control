"""BMO joint chequing (account 4969) — PDF importer.

STATUS (F4a): SKELETON. The PreParser raises NotImplementedError on
``extract`` because writing brittle regex without a real BMO statement
layout in front of us is guessing.

F4b unblocks this by:
  1. Kevin runs pdfplumber on a real BMO statement on his Mac and
     pastes a sanitized text excerpt (transaction grid only — strip
     account numbers, addresses, balances, names).
  2. We implement ``BmoJointChequingPdfPreParser.extract`` against
     that real layout + commit a synthetic-but-realistic fixture for
     the unit tests.
  3. F4b runs `ingest --institution bmo-joint-chequing --file <real.pdf>`
     end-to-end on Alienware and verifies bean-check still passes on
     the updated ledger.

The Importer half (``BmoJointChequingImporter``) is complete: it just
wraps ``render_simple_entry`` with the source account hard-coded. No
BMO-specific quirks expected there.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import (
    BeancountEntry,
    ExtractedTransaction,
    Importer,
    PreParser,
    PreParserError,
    render_simple_entry,
)

# Hard-coded constants — F4 targets this one account first.
INSTITUTION = "bmo-joint-chequing"
SOURCE_ACCOUNT = "Assets:CA:BMO:Chequing:Joint-4969"
CURRENCY = "CAD"
COUNTER_ACCOUNT = "Expenses:Uncategorized"


@dataclass
class BmoJointChequingPdfPreParser:
    """Stub PreParser for BMO joint chequing 4969.

    F4a placeholder. ``extract`` raises NotImplementedError; ``can_handle``
    is deliberately conservative (returns False) so the CLI doesn't claim
    to handle files it can't yet parse.
    """

    institution: str = INSTITUTION

    def can_handle(self, path_or_bytes: bytes | str) -> bool:
        return False

    def extract(self, path_or_bytes: bytes | str) -> list[ExtractedTransaction]:
        raise NotImplementedError(
            "BMO joint chequing PDF extraction is F4b. To unblock: run "
            "pdfplumber on a real BMO statement and paste a sanitized "
            "excerpt of the transaction grid so we can write the regex."
        )


@dataclass
class BmoJointChequingImporter:
    """Render extracted txns into Beancount entries pointed at Joint-4969."""

    institution: str = INSTITUTION
    source_account: str = SOURCE_ACCOUNT
    currency: str = CURRENCY
    counter_account: str = COUNTER_ACCOUNT

    def render(self, txns: list[ExtractedTransaction]) -> list[BeancountEntry]:
        entries: list[BeancountEntry] = []
        for txn in txns:
            if txn.currency != self.currency:
                raise PreParserError(
                    f"txn currency {txn.currency} != source account currency {self.currency} "
                    f"for {self.institution}"
                )
            entries.append(
                render_simple_entry(
                    txn,
                    source_account=self.source_account,
                    counter_account=self.counter_account,
                    importer_slug=self.institution,
                )
            )
        return entries


# Registry factories.

def build_bmo_joint_chequing_pre_parser() -> PreParser:
    return BmoJointChequingPdfPreParser()


def build_bmo_joint_chequing_importer() -> Importer:
    return BmoJointChequingImporter()
