"""Tests for categorize defer Planka + report helpers."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from apps.finance_agent.categorize.defer_report import write_defer_report
from apps.finance_agent.categorize.planka import create_defer_card, post_defer_cards
from apps.finance_agent.categorize.runner import EntryOutcome


class DeferPlankaTests(unittest.TestCase):
    def test_write_defer_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outcomes = [
                EntryOutcome(
                    date="2024-01-01",
                    description="MYSTERY",
                    status="deferred",
                    category="Expenses:Misc",
                    confidence=0.55,
                    reason="below threshold",
                )
            ]
            path = write_defer_report(outcomes, correlation_id="abc", output_dir=tmp)
            self.assertIsNotNone(path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("MYSTERY", text)
            self.assertIn("below threshold", text)

    @mock.patch.dict(
        "os.environ",
        {
            "PLANKA_BASE_URL": "https://planka.example",
            "PLANKA_API_KEY": "key",
            "PLANKA_FINANCE_DEFER_LIST_ID": "list-99",
        },
    )
    @mock.patch("apps._shared.planka_client.planka_request")
    def test_create_defer_card(self, planka_request: mock.MagicMock) -> None:
        planka_request.return_value = {"item": {"id": "card-1"}}
        outcome = EntryOutcome(
            date="2024-02-01",
            description="UNKNOWN MERCHANT",
            status="deferred",
            category="Expenses:Misc",
            confidence=0.6,
            reason="revise",
        )
        card = create_defer_card(outcome, correlation_id="run-1")
        self.assertEqual(card["card_id"], "card-1")
        self.assertIn("card-1", card["url"])
        planka_request.assert_called_once()

    @mock.patch("apps.finance_agent.categorize.planka.planka_defer_configured", return_value=True)
    @mock.patch("apps.finance_agent.categorize.planka.create_defer_card")
    def test_post_defer_cards_skips_approved(
        self, create_card: mock.MagicMock, _configured: mock.MagicMock
    ) -> None:
        create_card.return_value = {"card_id": "1", "url": "https://x/cards/1"}
        outcomes = [
            EntryOutcome("2024-01-01", "OK", "approved", "Expenses:Misc", 0.9),
            EntryOutcome("2024-01-02", "BAD", "deferred", "Expenses:Misc", 0.5, reason="low"),
        ]
        posted = post_defer_cards(outcomes, correlation_id="r")
        self.assertEqual(len(posted), 1)
        create_card.assert_called_once()


if __name__ == "__main__":
    unittest.main()
