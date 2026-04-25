import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import agent_event_dispatcher as dispatcher


class AgentEventDispatcherTests(unittest.TestCase):
    def test_extract_execution_from_agent_execution_fence(self) -> None:
        description = """Please do the thing.

```agent-execution
{"allowed_paths": ["docs"], "checks": ["git diff --check"], "operations": {"write_files": []}}
```
"""
        execution = dispatcher.extract_execution(description)
        self.assertEqual(["docs"], execution["allowed_paths"])

    def test_build_card_export_reads_metadata_from_agent_fence(self) -> None:
        description = """Please do the thing.

```agent-execution
{
  "summary": "Update the docs",
  "labels": ["docs-only"],
  "execution": {
    "allowed_paths": ["docs"],
    "operations": {"write_files": []}
  }
}
```
"""
        card = dispatcher.build_card_export(
            {
                "body": {
                    "cardId": "123",
                    "name": "Update docs",
                    "listName": "Approved To Execute",
                    "description": description,
                }
            }
        )
        self.assertEqual("Update the docs", card["summary"])
        self.assertEqual(["docs-only"], card["labels"])
        self.assertEqual(["docs"], card["execution"]["allowed_paths"])

    def test_dispatch_planka_event_writes_author_queue_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            payload = {
                "body": {
                    "cardId": "123",
                    "name": "Update docs",
                    "listName": "Approved To Execute",
                    "labels": ["docs-only"],
                    "execution": {
                        "allowed_paths": ["docs"],
                        "operations": {"write_files": [{"path": "docs/example.md", "content": "hello\n"}]},
                    },
                }
            }
            result = dispatcher.dispatch_planka_event(
                payload,
                author_queue=tmp / "author",
                review_queue=tmp / "review",
                artifact_dir=tmp / "artifacts",
            )
            self.assertEqual("author-agent-execute", result["action"])
            job = json.loads((tmp / "author" / "inbox" / "card-123-execute.json").read_text())
            self.assertTrue(job["lifecycle_callback_url"].endswith("/agent/lifecycle"))

    def test_merged_pr_defaults_card_to_done(self) -> None:
        with mock.patch.dict(os.environ, {"PLANKA_DONE_LIST_ID": "done-list"}, clear=False):
            with mock.patch("scripts.agent_event_dispatcher.set_card_state_labels", return_value={"labels_updated": True}):
                with mock.patch("scripts.agent_event_dispatcher.move_planka_card", return_value={"moved": True}) as move:
                    result = dispatcher.handle_forgejo_pr_event(
                        {
                            "pull_request": {
                                "merged": True,
                                "body": "Planka card: https://planka.dev-path.org/cards/abc123",
                                "head": {"ref": "agent/card-abc123-execute-demo"},
                                "labels": [],
                            }
                        }
                    )

        self.assertEqual("abc123", result["card_id"])
        self.assertEqual("Done", result["target_list"])
        move.assert_called_once_with("abc123", "done-list")

    def test_merged_plan_pr_can_send_card_to_approved(self) -> None:
        with mock.patch.dict(os.environ, {"PLANKA_APPROVED_LIST_ID": "approved-list"}, clear=False):
            with mock.patch("scripts.agent_event_dispatcher.set_card_state_labels", return_value={"labels_updated": True}):
                with mock.patch("scripts.agent_event_dispatcher.move_planka_card", return_value={"moved": True}) as move:
                    result = dispatcher.handle_forgejo_pr_event(
                        {
                            "pull_request": {
                                "merged": True,
                                "body": "Planka card: https://planka.dev-path.org/cards/abc123\nNext Planka list: Approved To Execute",
                                "head": {"ref": "agent/card-abc123-plan-demo"},
                                "labels": [],
                            }
                        }
                    )

        self.assertEqual("Approved To Execute", result["target_list"])
        move.assert_called_once_with("abc123", "approved-list")

    def test_author_lifecycle_moves_card_to_author_review_ready(self) -> None:
        with mock.patch.dict(os.environ, {"PLANKA_IN_PROGRESS_LIST_ID": "in-progress"}, clear=False):
            with mock.patch("scripts.agent_event_dispatcher.set_card_state_labels", return_value={"labels_updated": True}) as labels:
                with mock.patch("scripts.agent_event_dispatcher.move_planka_card", return_value={"moved": True}) as move:
                    result = dispatcher.handle_agent_lifecycle_event({"event": "author-pr-opened", "card_id": "abc123"})

        self.assertEqual("In Progress", result["target_list"])
        labels.assert_called_once_with("abc123", ["state:pr-open", "state:review-agent"])
        move.assert_called_once_with("abc123", "in-progress")

    def test_review_lifecycle_moves_human_review_decision(self) -> None:
        with mock.patch.dict(os.environ, {"PLANKA_NEEDS_HUMAN_LIST_ID": "human-review"}, clear=False):
            with mock.patch("scripts.agent_event_dispatcher.set_card_state_labels", return_value={"labels_updated": True}) as labels:
                with mock.patch("scripts.agent_event_dispatcher.move_planka_card", return_value={"moved": True}) as move:
                    result = dispatcher.handle_agent_lifecycle_event(
                        {"event": "review-completed", "card_id": "abc123", "decision": "needs_human_review"}
                    )

        self.assertEqual("Needs Human Review", result["target_list"])
        labels.assert_called_once_with("abc123", ["review:pr"])
        move.assert_called_once_with("abc123", "human-review")

    def test_review_lifecycle_moves_approved_decision_to_human_review(self) -> None:
        with mock.patch.dict(os.environ, {"PLANKA_NEEDS_HUMAN_LIST_ID": "human-review"}, clear=False):
            with mock.patch("scripts.agent_event_dispatcher.set_card_state_labels", return_value={"labels_updated": True}) as labels:
                with mock.patch("scripts.agent_event_dispatcher.move_planka_card", return_value={"moved": True}) as move:
                    result = dispatcher.handle_agent_lifecycle_event(
                        {"event": "review-completed", "card_id": "abc123", "decision": "approve_and_merge"}
                    )

        self.assertEqual("Needs Human Review", result["target_list"])
        labels.assert_called_once_with("abc123", ["review:pr", "state:ready-to-merge"])
        move.assert_called_once_with("abc123", "human-review")

    def test_merged_pr_clears_state_labels_and_moves_card_to_done(self) -> None:
        with mock.patch.dict(os.environ, {"PLANKA_DONE_LIST_ID": "done-list"}, clear=False):
            with mock.patch("scripts.agent_event_dispatcher.set_card_state_labels", return_value={"labels_updated": True}) as labels:
                with mock.patch("scripts.agent_event_dispatcher.move_planka_card", return_value={"moved": True}) as move:
                    result = dispatcher.handle_forgejo_pr_event(
                        {
                            "pull_request": {
                                "merged": True,
                                "body": "Planka card: https://planka.dev-path.org/cards/abc123",
                                "head": {"ref": "agent/card-abc123-execute-demo"},
                                "labels": [],
                            }
                        }
                    )

        self.assertEqual("abc123", result["card_id"])
        self.assertEqual("Done", result["target_list"])
        labels.assert_called_once_with("abc123", [])
        move.assert_called_once_with("abc123", "done-list")


if __name__ == "__main__":
    unittest.main()
