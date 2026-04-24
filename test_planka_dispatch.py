import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class PlankaDispatchTests(unittest.TestCase):
    def test_plan_ready_dispatches_create_execution_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            card = tmp / "card.json"
            card.write_text(
                json.dumps(
                    {
                        "id": "42",
                        "title": "Pin mutable images",
                        "list_name": "Plan Ready",
                        "source_path": str(card),
                        "execution": {"allowed_paths": ["compose/model-gateway"]},
                    }
                )
            )

            result = subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts" / "planka_dispatch.py"),
                    "--card",
                    str(card),
                    "--author-queue",
                    str(tmp / "author"),
                    "--review-queue",
                    str(tmp / "review"),
                    "--artifact-dir",
                    str(tmp / "artifacts"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            job = json.loads((tmp / "author" / "inbox" / "card-42-plan.json").read_text())

            self.assertEqual("author-agent-plan", payload["action"])
            self.assertEqual("create-execution-job", job["action"])

    def test_author_review_ready_dispatches_review_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            context = tmp / "review-context.json"
            context.write_text("{}")
            card = tmp / "card.json"
            card.write_text(
                json.dumps(
                    {
                        "id": "84",
                        "title": "Review compose hardening",
                        "list_name": "Author Review Ready",
                        "review_context_path": str(context),
                    }
                )
            )

            subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts" / "planka_dispatch.py"),
                    "--card",
                    str(card),
                    "--author-queue",
                    str(tmp / "author"),
                    "--review-queue",
                    str(tmp / "review"),
                    "--artifact-dir",
                    str(tmp / "artifacts"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            job = json.loads((tmp / "review" / "inbox" / "card-84-review.json").read_text())
            self.assertEqual("review-pr", job["action"])


if __name__ == "__main__":
    unittest.main()
