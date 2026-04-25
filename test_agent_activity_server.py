import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.agent_activity_server import build_snapshot, cancel_queued_job, retry_failed_job, service_action


class AgentActivityServerTests(unittest.TestCase):
    def test_build_snapshot_reads_queue_and_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = Path(tmpdir)
            author = state / "agent-homelab"
            review = state / "agent-review"
            for root in (author, review):
                for folder in ("inbox", "processing", "done", "failed"):
                    (root / folder).mkdir(parents=True)
                (root / "heartbeat.json").write_text(json.dumps({"updated_at": "now", "current_job": None}))
            (author / "inbox" / "job.json").write_text(json.dumps({"action": "execute-task", "title": "Test"}))

            snapshot = build_snapshot(state)

            self.assertEqual("execute-task", snapshot["queues"]["author"]["inbox"][0]["action"])
            self.assertEqual("now", snapshot["queues"]["review"]["heartbeat"]["updated_at"])

    def test_retry_failed_job_moves_job_to_inbox_and_archives_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = Path(tmpdir)
            failed = state / "agent-homelab" / "failed"
            inbox = state / "agent-homelab" / "inbox"
            failed.mkdir(parents=True)
            inbox.mkdir(parents=True)
            (failed / "job.json").write_text("{}")
            (failed / "job.error.json").write_text("{}")

            result = retry_failed_job(state, "author", "job.json")

            self.assertTrue((inbox / "job.json").exists())
            self.assertIn("retried", result)

    def test_cancel_queued_job_moves_job_to_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = Path(tmpdir)
            inbox = state / "agent-review" / "inbox"
            failed = state / "agent-review" / "failed"
            inbox.mkdir(parents=True)
            failed.mkdir(parents=True)
            (inbox / "job.json").write_text("{}")

            result = cancel_queued_job(state, "review", "job.json")

            self.assertTrue((failed / "job.json").exists())
            self.assertTrue((failed / "job.error.json").exists())
            self.assertIn("cancelled", result)

    def test_service_action_uses_systemctl_user(self) -> None:
        with mock.patch("scripts.agent_activity_server.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""

            result = service_action("author", "restart")

        self.assertEqual("alienware-author-agent.service", result["unit"])
        run.assert_called_once()

    def test_dashboard_requires_token_when_configured(self) -> None:
        from scripts.agent_activity_server import ActivityHandler

        handler = mock.Mock()
        handler.token = "secret"
        handler.headers = {}
        handler.path = "/"

        self.assertFalse(ActivityHandler._authorized(handler))

        handler.path = "/?token=secret"
        self.assertTrue(ActivityHandler._authorized(handler))


if __name__ == "__main__":
    unittest.main()
