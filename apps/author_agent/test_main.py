import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parent / "main.py"
SPEC = importlib.util.spec_from_file_location("author_agent_main", MODULE_PATH)
author_main = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(author_main)


class AuthorAgentTests(unittest.TestCase):
    def test_create_execution_job_from_card_uses_execution_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            card_path = tmp / "card.json"
            output_path = tmp / "job.json"
            card_path.write_text(
                """{
  "title": "Pin mutable images",
  "risk": "safe-update",
  "planka_card": "https://planka.dev-path.org/cards/123",
  "execution": {
    "allowed_paths": ["compose/model-gateway"],
    "operations": {
      "replacements": []
    }
  }
}
"""
            )

            payload = author_main.create_execution_job_from_card(card_path, output_path)

            self.assertEqual("execute-task", payload["action"])
            self.assertEqual(["safe-update"], payload["labels"])
            self.assertEqual(["compose/model-gateway"], payload["allowed_paths"])
            self.assertEqual(payload, author_main.load_json(output_path))

    def test_apply_operations_rejects_paths_outside_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worktree = Path(tmpdir)
            target = worktree / "README.md"
            target.write_text("old\n")

            with self.assertRaisesRegex(ValueError, "outside allowed scope"):
                author_main.apply_operations(
                    {
                        "replacements": [
                            {
                                "path": "README.md",
                                "old_string": "old",
                                "new_string": "new",
                            }
                        ]
                    },
                    worktree=worktree,
                    allowed_paths=["docs/"],
                )

    def test_build_review_context_carries_policy_fields(self) -> None:
        job = {
            "repo_name": "homelab-control",
            "labels": ["safe-update"],
            "plan_link": "https://example.test/plan",
            "planka_card": "https://planka.dev-path.org/cards/123",
        }
        checks = [{"command": "echo ok", "returncode": 0, "stdout": "", "stderr": ""}]

        context = author_main.build_review_context(
            job,
            pr_url="https://forgejo.dev-path.org/kevin/homelab-control/pulls/5",
            pr_number=5,
            branch_name="agent/job-005",
            changed_files=["docs/README.md"],
            checks=checks,
            commit_sha="abc123",
        )

        self.assertTrue(context["checks_passed"])
        self.assertTrue(context["has_plan_link"])
        self.assertTrue(context["has_planka_card"])
        self.assertEqual(["safe-update"], context["labels"])

    def test_build_pr_body_includes_next_planka_list_marker(self) -> None:
        body = author_main.build_pr_body(
            {
                "summary_lines": ["Create plan"],
                "labels": ["docs-only"],
                "plan_link": "https://planka.dev-path.org/cards/123",
                "planka_card": "https://planka.dev-path.org/cards/123",
                "next_planka_list": "Approved To Execute",
            },
            ["docs/example.md"],
            [{"command": "git diff --check", "returncode": 0}],
        )

        self.assertIn("Next Planka list: Approved To Execute", body)

    def test_create_worktree_suffixes_existing_agent_branch(self) -> None:
        job = {"title": "Demo", "branch_name": "agent/demo"}
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_dir = Path(tmpdir)
            job_path = queue_dir / "inbox" / "job.json"
            job_path.parent.mkdir()
            job_path.write_text("{}")
            with mock.patch.object(author_main, "repo_root_from_job", return_value=Path("/repo")):
                with mock.patch.object(author_main, "repo_name_from_path", return_value="homelab-control"):
                    with mock.patch.object(author_main, "git_lines", return_value=["agent/demo", "forgejo/main"]):
                        with mock.patch.object(author_main.subprocess, "run") as run:
                            run.return_value.returncode = 0
                            run.return_value.stdout = ""
                            run.return_value.stderr = ""

                            _, branch, _ = author_main.create_worktree(job, queue_dir, job_path)

        self.assertRegex(branch, r"agent/demo-\d{14}")


if __name__ == "__main__":
    unittest.main()
