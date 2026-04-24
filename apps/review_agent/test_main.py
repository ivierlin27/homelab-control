import importlib.util
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parent / "main.py"
SPEC = importlib.util.spec_from_file_location("review_agent_main", MODULE_PATH)
review_main = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(review_main)


class ReviewAgentTests(unittest.TestCase):
    def test_evaluate_requests_changes_when_checks_missing(self) -> None:
        policy = review_main.load_yaml(review_main.DEFAULT_POLICY)
        result = review_main.evaluate(
            policy,
            {
                "labels": ["safe-update"],
                "changed_files": ["docs/foo.md"],
                "checks_passed": False,
                "has_plan_link": True,
                "has_planka_card": True,
            },
        )
        self.assertEqual("request_changes", result["decision"])

    def test_evaluate_routes_compose_changes_to_human_review(self) -> None:
        policy = review_main.load_yaml(review_main.DEFAULT_POLICY)
        result = review_main.evaluate(
            policy,
            {
                "labels": ["safe-update"],
                "changed_files": ["compose/model-gateway/docker-compose.yml"],
                "checks_passed": True,
                "has_plan_link": True,
                "has_planka_card": True,
            },
        )
        self.assertEqual("needs_human_review", result["decision"])

    @mock.patch.object(review_main, "forgejo_request")
    def test_fetch_pull_request_merges_live_pr_data(self, forgejo_request: mock.Mock) -> None:
        forgejo_request.side_effect = [
            {
                "title": "Pin mutable images",
                "body": "Plan: https://example.test/plan\nPlanka: https://planka.dev-path.org/cards/123",
                "html_url": "https://forgejo.dev-path.org/kevin/homelab-control/pulls/7",
                "labels": [{"name": "safe-update"}],
                "head": {"sha": "abc123", "ref": "agent/job-007"},
                "base": {"ref": "main"},
            },
            [{"filename": "docs/INVENTORY_MEMORY_SYNC.md"}],
            {"state": "success", "statuses": [{"state": "success"}]},
        ]

        result = review_main.fetch_pull_request(
            {
                "pr_url": "https://forgejo.dev-path.org/kevin/homelab-control/pulls/7",
                "forgejo_base_url": "https://forgejo.dev-path.org",
                "forgejo_api_token": "secret",
            }
        )

        self.assertEqual(["safe-update"], result["labels"])
        self.assertEqual(["docs/INVENTORY_MEMORY_SYNC.md"], result["changed_files"])
        self.assertTrue(result["checks_passed"])


if __name__ == "__main__":
    unittest.main()
