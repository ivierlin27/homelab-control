"""Tests for LLM-backed categorization analyst."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal

from apps._shared.rlm.subcall import SubCallInvoker
from apps.finance_agent.categorize.classifier import analyst_classify
from apps.finance_agent.categorize.ledger import PendingTransaction
from apps.finance_agent.categorize.llm_analyst import analyst_classify_llm, analyst_revise_llm
from apps.finance_agent.categorize.policy import load_policy
from apps.finance_agent.categorize.risk import risk_verify
from apps._shared.verifier import VerifierVerdict

JOINT = "Assets:CA:BMO:Chequing:Joint-4969"


def _txn(description: str, *, amount: str = "-12.00") -> PendingTransaction:
    return PendingTransaction(
        block_start=0,
        block_end=0,
        date="2024-06-01",
        description=description,
        source_account=JOINT,
        source_amount=Decimal(amount),
        amount=Decimal(amount.lstrip("-") or amount),
        currency="CAD",
        lines=(),
    )


class LlmAnalystTests(unittest.TestCase):
    def test_skips_llm_when_rules_meet_threshold(self) -> None:
        policy = load_policy()
        txn = _txn("SHELL 12345 FUEL")
        rules = analyst_classify(txn, policy)
        self.assertGreaterEqual(float(rules["confidence"]), policy.threshold)

        calls: list[str] = []

        def transport(intent: str, model: str, payload: dict) -> dict:
            calls.append(intent)
            return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

        inv = SubCallInvoker(transport=transport)
        out = analyst_classify_llm(txn, policy, invoker=inv)
        self.assertEqual(out["proposed_category"], rules["proposed_category"])
        self.assertEqual(calls, [])

    def test_llm_classify_used_for_low_confidence(self) -> None:
        policy = load_policy()
        txn = _txn("OBSCURE FOREIGN MERCHANT ZZZ")

        summary = json.dumps(
            {
                "proposed_category": "Expenses:Food:Dining",
                "confidence": 0.88,
                "alternatives": [
                    {"category": "Expenses:Misc", "confidence": 0.4},
                ],
                "reason": "restaurant payee in foreign city",
            }
        )

        def transport(intent: str, model: str, payload: dict) -> dict:
            return {
                "choices": [{"message": {"content": json.dumps({"summary": summary})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }

        inv = SubCallInvoker(transport=transport)
        out = analyst_classify_llm(txn, policy, invoker=inv)
        self.assertEqual(out["proposed_category"], "Expenses:Food:Dining")
        self.assertAlmostEqual(float(out["confidence"]), 0.88)
        verdict = risk_verify(out, out.get("evidence") or {}, policy=policy)
        self.assertEqual(verdict.verdict, VerifierVerdict.ACCEPT)

    def test_llm_revise_with_hint(self) -> None:
        policy = load_policy()
        txn = _txn("TST* SOME CAFE VANCOUVER")
        claim = analyst_classify(txn, policy)

        summary = json.dumps(
            {
                "proposed_category": "Expenses:Food:Dining",
                "confidence": 0.9,
                "alternatives": [],
                "reason": "TST prefix cafe",
            }
        )

        def transport(intent: str, model: str, payload: dict) -> dict:
            return {
                "choices": [{"message": {"content": json.dumps({"summary": summary})}}],
                "usage": {},
            }

        inv = SubCallInvoker(transport=transport)
        revised = analyst_revise_llm(
            claim, hint="confidence below threshold", policy=policy, invoker=inv
        )
        self.assertEqual(revised["proposed_category"], "Expenses:Food:Dining")
        self.assertGreaterEqual(float(revised["confidence"]), 0.85)


if __name__ == "__main__":
    unittest.main()
