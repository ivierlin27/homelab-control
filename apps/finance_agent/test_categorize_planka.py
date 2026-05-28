"""Tests for categorize defer Planka + report helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apps.finance_agent.categorize.defer_report import write_defer_report
from apps.finance_agent.categorize.planka import (
    create_defer_card,
    finance_defer_list_id,
    planka_defer_configured,
    post_defer_cards,
)
from apps.finance_agent.categorize.planka_smoke import (
    SMOKE_PAYEE,
    live_smoke_finance_planka,
)
from apps.finance_agent.categorize.runner import EntryOutcome, categorize_pending
from apps.finance_agent.test_categorize import SAMPLE_LEDGER, SAMPLE_TXNS


class DeferPlankaConfigTests(unittest.TestCase):
    def test_finance_defer_list_id_prefers_defer_env(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PLANKA_FINANCE_DEFER_LIST_ID": "defer-list",
                "PLANKA_FINANCE_INBOX_LIST_ID": "inbox-list",
            },
            clear=False,
        ):
            self.assertEqual(finance_defer_list_id(), "defer-list")

    def test_finance_defer_list_id_inbox_fallback(self) -> None:
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("PLANKA_FINANCE_DEFER_LIST_ID", "PLANKA_FINANCE_INBOX_LIST_ID")
        }
        env["PLANKA_FINANCE_INBOX_LIST_ID"] = "inbox-only"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(finance_defer_list_id(), "inbox-only")

    @mock.patch.dict(
        os.environ,
        {
            "PLANKA_BASE_URL": "https://planka.example",
            "PLANKA_API_KEY": "replace-me",
            "PLANKA_FINANCE_DEFER_LIST_ID": "list-99",
        },
        clear=False,
    )
    def test_planka_defer_configured_rejects_placeholder_key(self) -> None:
        self.assertFalse(planka_defer_configured())

    @mock.patch.dict(
        os.environ,
        {
            "PLANKA_BASE_URL": "https://planka.example",
            "PLANKA_API_KEY": "real-key",
            "PLANKA_FINANCE_DEFER_LIST_ID": "list-99",
        },
        clear=False,
    )
    def test_planka_defer_configured_when_auth_and_list(self) -> None:
        self.assertTrue(planka_defer_configured())


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
            path = write_defer_report(outcomes, correlation_id="abc", output_dir=Path(tmp))
            self.assertIsNotNone(path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("MYSTERY", text)
            self.assertIn("below threshold", text)

    def test_write_defer_report_none_when_no_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outcomes = [
                EntryOutcome("2024-01-01", "OK", "approved", "Expenses:Misc", 0.9),
            ]
            path = write_defer_report(outcomes, correlation_id="abc", output_dir=Path(tmp))
            self.assertIsNone(path)

    @mock.patch.dict(
        os.environ,
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
            verifier_rounds=2,
        )
        card = create_defer_card(outcome, correlation_id="run-1")
        self.assertEqual(card["card_id"], "card-1")
        self.assertEqual(card["url"], "https://planka.example/cards/card-1")
        planka_request.assert_called_once()
        call_path = planka_request.call_args[0][0]
        self.assertEqual(call_path, "lists/list-99/cards")
        payload = planka_request.call_args[1]["payload"]
        self.assertEqual(payload["type"], "project")
        self.assertIn("2024-02-01", payload["name"])
        self.assertIn("UNKNOWN MERCHANT", payload["name"])
        self.assertIn("run-1", payload["description"])
        self.assertIn("revise", payload["description"])

    def test_post_defer_cards_not_configured_returns_empty(self) -> None:
        with mock.patch(
            "apps.finance_agent.categorize.planka.planka_defer_configured",
            return_value=False,
        ):
            posted = post_defer_cards(
                [EntryOutcome("2024-01-01", "X", "deferred", "Expenses:Misc", 0.5)],
                correlation_id="r",
            )
        self.assertEqual(posted, [])

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

    @mock.patch("apps.finance_agent.categorize.planka.planka_defer_configured", return_value=True)
    @mock.patch("apps.finance_agent.categorize.planka.create_defer_card")
    def test_post_defer_cards_records_api_errors(
        self, create_card: mock.MagicMock, _configured: mock.MagicMock
    ) -> None:
        create_card.side_effect = RuntimeError("403 forbidden")
        posted = post_defer_cards(
            [EntryOutcome("2024-01-02", "BAD", "deferred", "Expenses:Misc", 0.5)],
            correlation_id="r",
        )
        self.assertEqual(len(posted), 1)
        self.assertIn("403", posted[0]["error"])


class CategorizeRunnerPlankaTests(unittest.TestCase):
    @mock.patch("apps.finance_agent.categorize.planka.post_defer_cards")
    @mock.patch(
        "apps.finance_agent.categorize.planka.planka_defer_configured",
        return_value=True,
    )
    def test_runner_posts_planka_on_defer(
        self, _configured: mock.MagicMock, post_cards: mock.MagicMock
    ) -> None:
        post_cards.return_value = [{"card_id": "c1", "url": "https://planka.example/cards/c1"}]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger"
            report_dir = Path(tmp) / "reports"
            audit = Path(tmp) / "audit.jsonl"
            ledger.mkdir()
            (ledger / "main.beancount").write_text(SAMPLE_LEDGER, encoding="utf-8")
            (ledger / "accounts.beancount").write_text("", encoding="utf-8")
            (ledger / "transactions.beancount").write_text(SAMPLE_TXNS, encoding="utf-8")
            result = categorize_pending(
                ledger_dir=ledger,
                audit_path=audit,
                limit=2,
                run_bean_check=False,
                post_planka=True,
                defer_report_dir=report_dir,
                correlation_id="test-corr",
            )
            self.assertEqual(result.deferred, 1)
            self.assertTrue(result.defer_report_path)
            self.assertEqual(len(result.planka_cards), 1)
            post_cards.assert_called_once()
            self.assertEqual(post_cards.call_args.kwargs["correlation_id"], "test-corr")

    @mock.patch("apps.finance_agent.categorize.planka.post_defer_cards")
    @mock.patch(
        "apps.finance_agent.categorize.planka.planka_defer_configured",
        return_value=True,
    )
    def test_runner_respects_post_planka_false(
        self, _configured: mock.MagicMock, post_cards: mock.MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger"
            report_dir = Path(tmp) / "reports"
            audit = Path(tmp) / "audit.jsonl"
            ledger.mkdir()
            (ledger / "main.beancount").write_text(SAMPLE_LEDGER, encoding="utf-8")
            (ledger / "accounts.beancount").write_text("", encoding="utf-8")
            (ledger / "transactions.beancount").write_text(SAMPLE_TXNS, encoding="utf-8")
            result = categorize_pending(
                ledger_dir=ledger,
                audit_path=audit,
                limit=2,
                run_bean_check=False,
                post_planka=False,
                defer_report_dir=report_dir,
            )
            self.assertEqual(result.deferred, 1)
            post_cards.assert_not_called()

    @mock.patch("apps.finance_agent.categorize.planka.post_defer_cards")
    def test_dry_run_skips_defer_report_and_planka(self, post_cards: mock.MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger"
            audit = Path(tmp) / "audit.jsonl"
            ledger.mkdir()
            (ledger / "main.beancount").write_text(SAMPLE_LEDGER, encoding="utf-8")
            (ledger / "accounts.beancount").write_text("", encoding="utf-8")
            (ledger / "transactions.beancount").write_text(SAMPLE_TXNS, encoding="utf-8")
            result = categorize_pending(
                ledger_dir=ledger,
                audit_path=audit,
                limit=2,
                dry_run=True,
            )
            self.assertGreaterEqual(result.deferred, 0)
            post_cards.assert_not_called()
            self.assertEqual(result.defer_report_path, "")
            self.assertEqual(result.planka_cards, [])


class PlankaSmokeHelperTests(unittest.TestCase):
    @mock.patch("apps._shared.planka_client.planka_request")
    @mock.patch("apps.finance_agent.categorize.planka_smoke.create_defer_card")
    @mock.patch("apps.finance_agent.categorize.planka_smoke.planka_defer_configured", return_value=True)
    @mock.patch("apps._shared.planka_client.planka_auth_configured", return_value=True)
    def test_live_smoke_creates_and_deletes(
        self,
        _auth: mock.MagicMock,
        _defer: mock.MagicMock,
        create_card: mock.MagicMock,
        planka_request: mock.MagicMock,
    ) -> None:
        create_card.return_value = {"card_id": "card-smoke", "url": "https://x/cards/card-smoke"}
        result = live_smoke_finance_planka(correlation_id="live.smoke.test")
        self.assertEqual(result["card_id"], "card-smoke")
        self.assertTrue(result["deleted"])
        create_card.assert_called_once()
        outcome = create_card.call_args[0][0]
        self.assertEqual(outcome.description, SMOKE_PAYEE)
        planka_request.assert_called_once_with("cards/card-smoke", method="DELETE")

    @mock.patch("apps._shared.planka_client.planka_request")
    @mock.patch("apps.finance_agent.categorize.planka_smoke.create_defer_card")
    @mock.patch("apps.finance_agent.categorize.planka_smoke.planka_defer_configured", return_value=True)
    @mock.patch("apps._shared.planka_client.planka_auth_configured", return_value=True)
    def test_live_smoke_no_cleanup_skips_delete(
        self,
        _auth: mock.MagicMock,
        _defer: mock.MagicMock,
        create_card: mock.MagicMock,
        planka_request: mock.MagicMock,
    ) -> None:
        create_card.return_value = {"card_id": "card-smoke", "url": "https://x/cards/card-smoke"}
        result = live_smoke_finance_planka(correlation_id="live.smoke.test", cleanup=False)
        self.assertIsNone(result["deleted"])
        planka_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
