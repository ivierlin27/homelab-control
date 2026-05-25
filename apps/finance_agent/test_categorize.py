"""Tests for F6 categorization loop."""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from apps._shared.verifier import VerifierEscalation, run_verifier_loop
from apps.finance_agent.categorize.classifier import analyst_classify, analyst_revise
from apps.finance_agent.categorize.ledger import apply_category, find_pending, rewrite_blocks
from apps.finance_agent.categorize.policy import load_policy
from apps.finance_agent.categorize.risk import risk_verify
from apps.finance_agent.categorize.runner import categorize_pending

JOINT = "Assets:CA:BMO:Chequing:Joint-4969"
MAKAELY = "Assets:CA:BMO:Chequing:MaKaely-Personal-3616"

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

TXN_WITH_BALANCE = f"""\
2024-03-01 ! "INTERACe-TransferSent"
  source_importer: "test"
  {JOINT}                                                 -100.00 CAD
  Expenses:Uncategorized                                  100.00 CAD

2024-03-02 balance {JOINT}  900.00 CAD

2024-03-03 ! "Deposit"
  source_importer: "test"
  {JOINT}                                                  50.00 CAD
  Expenses:Uncategorized                                  -50.00 CAD
"""

TRANSFER_TXNS = f"""\
2024-02-01 ! "INTERACe-TransferSent"
  source_importer: "test"
  {JOINT}                                                 -500.00 CAD
  Expenses:Uncategorized                                  500.00 CAD

2024-02-02 ! "INTERACe-TransferReceived"
  source_importer: "test"
  {JOINT}                                                  200.00 CAD
  Expenses:Uncategorized                                 -200.00 CAD

2024-02-03 ! "OnlineTransfer,TF0764#3954-969"
  source_importer: "test"
  {MAKAELY}                                                -75.00 CAD
  Expenses:Uncategorized                                   75.00 CAD

2024-02-04 ! "OnlineTransfer,TF0764#3954-969"
  source_importer: "test"
  {MAKAELY}                                               1500.00 CAD
  Expenses:Uncategorized                                -1500.00 CAD

2024-02-05 ! "DebitCardPurchase,RECURRINGPYMNT 24FEB,SHAWMOBILEON"
  source_importer: "test"
  {JOINT}                                                  -39.20 CAD
  Expenses:Uncategorized                                   39.20 CAD

2024-02-06 ! "Deposit"
  source_importer: "test"
  {MAKAELY}                                                100.00 CAD
  Expenses:Uncategorized                                 -100.00 CAD
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
            self.assertEqual(txn.source_amount, Decimal("-74.23"))
            claim = analyst_classify(txn, policy)
            self.assertEqual(claim["proposed_category"], "Expenses:Auto:Fuel")
            self.assertEqual(txn.source_amount, Decimal("-74.23"))
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
            remaining = find_pending(txns)
            self.assertEqual(len(remaining), 1)


class BalanceBoundaryTests(unittest.TestCase):
    def test_balance_directive_not_in_transaction_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            txns = Path(tmp) / "transactions.beancount"
            txns.write_text(TXN_WITH_BALANCE, encoding="utf-8")
            pending = find_pending(txns)
            self.assertEqual(len(pending), 2)
            self.assertNotIn("balance", pending[0].block_text.lower())


class TransferContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()

    def test_house_outflow_is_allowance(self) -> None:
        txn = find_pending_from_text(
            '2024-02-01 ! "INTERACe-TransferSent"\n'
            f"  {JOINT}  -500.00 CAD\n  Expenses:Uncategorized  500.00 CAD\n"
        )[0]
        claim = analyst_classify(txn, self.policy)
        self.assertEqual(claim["proposed_category"], "Expenses:Household:Allowance")
        self.assertGreaterEqual(float(claim["confidence"]), 0.85)

    def test_house_inflow_is_reimbursement(self) -> None:
        txn = find_pending_from_text(
            '2024-02-02 ! "INTERACe-TransferReceived"\n'
            f"  {JOINT}  200.00 CAD\n  Expenses:Uncategorized  -200.00 CAD\n"
        )[0]
        claim = analyst_classify(txn, self.policy)
        self.assertEqual(claim["proposed_category"], "Income:Household:FamilyReimbursement")

    def test_debitcard_suffix_matches_groceries(self) -> None:
        txn = find_pending_from_text(
            '2024-03-01 ! "DebitCardPurchase,WALNUTGROVESE"\n'
            f"  {MAKAELY}  -12.00 CAD\n  Expenses:Uncategorized  12.00 CAD\n"
        )[0]
        claim = analyst_classify(txn, self.policy)
        self.assertEqual(claim["proposed_category"], "Expenses:Food:Groceries")
        self.assertGreaterEqual(float(claim["confidence"]), 0.85)

    def test_person_outflow_interac_sent_is_external_transfer(self) -> None:
        txn = find_pending_from_text(
            '2024-02-10 ! "INTERACe-TransferSent"\n'
            f"  {MAKAELY}  -50.00 CAD\n  Expenses:Uncategorized  50.00 CAD\n"
        )[0]
        claim = analyst_classify(txn, self.policy)
        self.assertEqual(claim["proposed_category"], "Expenses:Transfers:External")
        self.assertGreaterEqual(float(claim["confidence"]), 0.85)

    def test_person_outflow_to_joint_is_cc_reimbursement(self) -> None:
        txn = find_pending_from_text(
            '2024-02-03 ! "OnlineTransfer,TF0764#3954-969"\n'
            f"  {MAKAELY}  -75.00 CAD\n  Expenses:Uncategorized  75.00 CAD\n"
        )[0]
        claim = analyst_classify(txn, self.policy)
        self.assertEqual(claim["proposed_category"], "Expenses:Household:CC-Reimbursement")

    def test_person_inflow_is_allowance(self) -> None:
        txn = find_pending_from_text(
            '2024-02-04 ! "OnlineTransfer,TF0764#3954-969"\n'
            f"  {MAKAELY}  1500.00 CAD\n  Expenses:Uncategorized  -1500.00 CAD\n"
        )[0]
        claim = analyst_classify(txn, self.policy)
        self.assertEqual(claim["proposed_category"], "Income:Household:Allowance")

    def test_recurring_and_deposit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp)
            (ledger / "main.beancount").write_text(SAMPLE_LEDGER, encoding="utf-8")
            txns = ledger / "transactions.beancount"
            txns.write_text(TRANSFER_TXNS, encoding="utf-8")
            by_desc = {t.description: t for t in find_pending(txns)}
            rec = analyst_classify(by_desc["DebitCardPurchase,RECURRINGPYMNT 24FEB,SHAWMOBILEON"], self.policy)
            self.assertEqual(rec["proposed_category"], "Expenses:Utilities:Telecom")
            dep = analyst_classify(by_desc["Deposit"], self.policy)
            self.assertEqual(dep["proposed_category"], "Income:Household:Allowance")


class CategorizeVerifierTests(unittest.TestCase):
    def test_low_confidence_deferred(self) -> None:
        policy = load_policy()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp)
            (ledger / "main.beancount").write_text(SAMPLE_LEDGER, encoding="utf-8")
            (ledger / "transactions.beancount").write_text(SAMPLE_TXNS, encoding="utf-8")
            mystery = find_pending(ledger / "transactions.beancount")[1]
            claim = analyst_classify(mystery, policy)
            with self.assertRaises(VerifierEscalation):
                run_verifier_loop(
                    claim=claim,
                    evidence=claim["evidence"],
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
            (ledger / "transactions.beancount").write_text(TRANSFER_TXNS, encoding="utf-8")
            result = categorize_pending(
                ledger_dir=ledger,
                audit_path=audit,
                dry_run=True,
                limit=10,
            )
            self.assertEqual(result.scanned, 6)
            self.assertGreaterEqual(result.approved, 5)
            self.assertEqual(result.deferred, 0)


def find_pending_from_text(fragment: str) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        txns = Path(tmp) / "transactions.beancount"
        txns.write_text(fragment, encoding="utf-8")
        return find_pending(txns)


if __name__ == "__main__":
    unittest.main()
