import tempfile
import unittest
import importlib.util
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parent / "main.py"
SPEC = importlib.util.spec_from_file_location("homelab_maintainer_main", MODULE_PATH)
assert SPEC and SPEC.loader
maintainer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintainer)


class HomelabMaintainerTests(unittest.TestCase):
    def test_triage_intake_dry_run_records_trust_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_dir = Path(tmpdir) / "agent-homelab-maintainer"
            policy = maintainer.load_yaml(maintainer.DEFAULT_POLICY)
            job = {
                "action": "triage-intake",
                "intake_id": "intake-1",
                "title": "Gateway cleanup",
                "content": "Document the current LiteLLM routes.",
                "source_kind": "text",
                "source_ref": "notes.txt",
                "task_class": "architecture_synthesis",
                "symbolic_intent": "plan",
                "routing": {"route": "cloud-frontier", "model_tier": "cloud-frontier"},
                "dry_run": True,
                "write_memory": False,
            }

            result = maintainer.triage_intake(job, queue_dir=queue_dir, policy=policy)

            self.assertFalse(result["card"]["created"])
            ledger = queue_dir / "trust-ledger.jsonl"
            self.assertTrue(ledger.exists())
            self.assertIn('"event": "triage-intake"', ledger.read_text())

    def test_delegate_author_job_enqueues_into_author_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_dir = Path(tmpdir) / "author"
            policy = maintainer.load_yaml(maintainer.DEFAULT_POLICY)
            author_job = {
                "job_name": "author-job.json",
                "action": "execute-task",
                "allowed_paths": ["docs"],
            }
            with mock.patch.dict("os.environ", {"AUTHOR_QUEUE_DIR": str(queue_dir)}):
                result = maintainer.delegate_author_job(author_job, policy=policy)

            self.assertTrue(Path(result["job_path"]).exists())
            self.assertEqual(str(queue_dir), result["queue_dir"])


if __name__ == "__main__":
    unittest.main()
