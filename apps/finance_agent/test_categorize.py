"""Tests for F6 categorization loop."""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from apps.finance_agent.categorize.classifier import analyst_classify
from apps.finance_agent.categorize.ledger import apply_category, find_pending, rewrite_blocks
from apps.finance_agent.categorize.policy import load_policy
from apps.finance_agent.categorize.runner import categorize_pending
from apps.finance_agent.categorize.risk import risk_verify
from apps._shared.verifier import VerifierEscalation, run_verifier_loop
from apps.finance_agent.categorize.classifier import analyst_revise


SAMPLE_LEDGER = """\
include "accounts.beancount"
include "transactions.beancount"
"""

SAMPLE_TXNS = """\
2024-01-10 ! "SHELL 12345 FUEL"
  source_importer: "test"
  Assets:CA:BMO:Chequing:Joint-4969                         -74.23 CAD
  Expenses:Uncategorized                                    74.23 CAD

2024-01-11 ! "MYSTERY VENDOR XYZ"
  source_importer: "test"
  Assets:CA:BMO:Chequing:Joint-4969                         -12.00 CAD
  Expenses:Uncategorized                                    12.00 CAD
"""


class CategorizeLedgerTests(unittest.TestCase):
    def test_find_pending_and_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp)
            (ledger / "main.beancount").write_text(SAMPLE_LEDGER, encoding="utf-8")
            txns = ledger / "transactions.beancount"
            txns.write_text(SAMPLE_TXNS, encoding="utf-8")
            pending = find_pending(txns)
            self.assertEqual(len(pending), 2)
            policy = load_policy()
            txn = pending[0]
            claim = analyst_classify(txn, policy)
            self.assertEqual(claim["proposed_category"], "Expenses:Auto:Fuel")
            self.assertGreaterEqual(float(claim["confidence"]), 0.85)
            new_lines = apply_category(
                txn,
                category=claim["proposed_category"],
                confidence=float(claim["confidence"]),
                correlation_id="test-corr",
            )
            rewrite_blocks(txns, [(txn, new_lines)])
            text = txns.read_text(encoding="utf-8")
            self.assertIn("Expenses:Auto:Fuel", text)
            self.assertNotIn(f"! \"{txn.description}\"", text)
            self.assertIn(f"* \"{txn.description}\"", text)
            remaining = find_pending(txns)
            self.assertEqual(len(remaining), 1)
            self.assertIn("MYSTERY", remaining[0].description)


class CategorizeVerifierTests(unittest.TestCase):
    def test_fuel_accepted_low_confidence_deferred(self) -> None:
        policy = load_policy()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp)
            (ledger / "main.beancount").write_text(SAMPLE_LEDGER, encoding="utf-8")
            txns = ledger / "transactions.beancount"
            txns.write_text(SAMPLE_TXNS, encoding="utf-8")
            pending = find_pending(txns, limit=1)
            fuel = pending[0]
            mystery = find_pending(txns)[1]

            claim_fuel = analyst_classify(fuel, policy)
            accepted, _ = run_verifier_loop(
                claim=claim_fuel,
                evidence=claim_fuel["evidence"],
                verifier=lambda c, e: risk_verify(c, e, policy=policy),
                persona="finance-risk",
                builder_revise=lambda c, r: analyst_revise(c, hint=r.notes, policy=policy),
                max_rounds=2,
            )
            self.assertEqual(accepted["proposed_category"], "Expenses:Auto:Fuel")

            claim_mystery = analyst_classify(mystery, policy)
            with self.assertRaises(VerifierEscalation):
                run_verifier_loop(
                    claim=claim_mystery,
                    evidence=claim_mystery["evidence"],
                    verifier=lambda c, e: risk_verify(c, e, policy=policy),
                    persona="finance-risk",
                    builder_revise=lambda c, r: analyst_revise(c, hint=r.notes, policy=policy),
                    max_rounds=2,
                )


class CategorizeRunnerTests(unittest.TestCase):
    def test_categorize_batch_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp)
            audit = Path(tmp) / "audit.jsonl"
            (ledger / "main.beancount").write_text(SAMPLE_LEDGER, encoding="utf-8")
            (ledger / "transactions.beancount").write_text(SAMPLE_TXNS, encoding="utf-8")
            result = categorize_pending(
                ledger_dir=ledger,
                audit_path=audit,
                dry_run=True,
                limit=10,
            )
            self.assertEqual(result.scanned, 2)
            self.assertEqual(result.approved, 1)
            self.assertEqual(result.deferred, 1)
            self.assertIn("SHELL", result.outcomes[0].description)


if __name__ == "__main__":
    unittest.main()
