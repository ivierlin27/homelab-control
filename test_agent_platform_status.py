import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from scripts.agent_platform_status import build_status


class AgentPlatformStatusTests(unittest.TestCase):
    def test_build_status_reports_backlog_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            author = tmp / "author"
            review = tmp / "review"
            for queue in (author, review):
                for name in ("inbox", "processing", "done", "failed"):
                    (queue / name).mkdir(parents=True, exist_ok=True)
            (author / "failed" / "job.error.json").write_text("{}")
            (author / "heartbeat.json").write_text(
                json.dumps({"updated_at": "2099-01-01T00:00:00+00:00", "processed_jobs": 3, "counts": {"inbox": 0}})
            )
            (review / "heartbeat.json").write_text(
                json.dumps({"updated_at": "2099-01-01T00:00:00+00:00", "processed_jobs": 4, "counts": {"inbox": 1}})
            )

            args = Namespace(
                author_queue=str(author),
                review_queue=str(review),
                author_heartbeat=str(author / "heartbeat.json"),
                review_heartbeat=str(review / "heartbeat.json"),
                forgejo_base_url="https://forgejo.example",
                repo_owner="kevin",
                repo_name="homelab-control",
                forgejo_api_token="token",
                stale_after_seconds=600,
            )
            with mock.patch("scripts.agent_platform_status.review_backlog", return_value=[{"pr_number": 1}]):
                status = build_status(args)

            self.assertEqual(["job.error.json"], status["queues"]["author"]["failed_jobs"])
            self.assertEqual([{"pr_number": 1}], status["review_backlog"])
            self.assertFalse(status["healthy"])


if __name__ == "__main__":
    unittest.main()
