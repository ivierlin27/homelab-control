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
            self.assertTrue((tmp / "author" / "inbox" / "card-123-execute.json").exists())

    def test_merged_pr_defaults_card_to_done(self) -> None:
        with mock.patch.dict(os.environ, {"PLANKA_DONE_LIST_ID": "done-list"}, clear=False):
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


if __name__ == "__main__":
    unittest.main()
