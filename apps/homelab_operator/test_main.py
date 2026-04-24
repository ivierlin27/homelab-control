import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parent / "main.py"
SPEC = importlib.util.spec_from_file_location("homelab_operator_main", MODULE_PATH)
homelab_main = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(homelab_main)


class InventoryMemorySyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.services_path = self.root / "services.yaml"
        self.observability_path = self.root / "observability.yaml"
        self.state_path = self.root / "inventory-sync-state.json"

        self.services_path.write_text(
            """
services:
  - id: alpha
    host: node-a
    type: docker
    role: app
    repo: homelab-control
    observability_profile: api
    endpoints:
      - name: dashboard
        url: https://alpha.example.test
  - id: beta
    host: node-b
    type: lxc
    role: worker
    repo: homelab-control
    observability_profile: background
""".strip()
            + "\n"
        )
        self.observability_path.write_text(
            """
profiles:
  api:
    required:
      - health_endpoint
      - logs
      - runbook
  background:
    required:
      - logs
checks:
  - service: alpha
    has_health_endpoint: true
    has_logs: true
    has_runbook: false
  - service: beta
    has_logs: true
""".strip()
            + "\n"
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_build_service_inventory_records_merges_observability(self) -> None:
        records = homelab_main.build_service_inventory_records(self.services_path, self.observability_path)

        self.assertEqual(["alpha", "beta"], [record["service_id"] for record in records])
        self.assertEqual(["runbook"], records[0]["observability"]["missing"])
        self.assertEqual([], records[1]["observability"]["missing"])
        self.assertEqual("https://alpha.example.test", records[0]["endpoints"][0]["url"])
        self.assertTrue(records[0]["fingerprint"])

    def test_sync_inventory_memory_skips_unchanged_records_after_first_run(self) -> None:
        with mock.patch.object(homelab_main, "post_json", return_value={"ok": True}) as post_json:
            first = homelab_main.sync_inventory_memory(
                services_path=self.services_path,
                observability_path=self.observability_path,
                ingest_url="https://n8n.example.test/webhook/ingest",
                principal="agent:homelab",
                source="operator",
                command_or_api="homelab_operator:inventory-memory-sync",
                git_ref="homelab-control@test",
                artifact_url="",
                state_path=self.state_path,
                timeout=5,
                dry_run=False,
            )

            self.assertEqual(2, len(first["changed"]))
            self.assertEqual(2, len(first["results"]))
            self.assertTrue(self.state_path.exists())

            second = homelab_main.sync_inventory_memory(
                services_path=self.services_path,
                observability_path=self.observability_path,
                ingest_url="https://n8n.example.test/webhook/ingest",
                principal="agent:homelab",
                source="operator",
                command_or_api="homelab_operator:inventory-memory-sync",
                git_ref="homelab-control@test",
                artifact_url="",
                state_path=self.state_path,
                timeout=5,
                dry_run=False,
            )

            self.assertEqual([], second["changed"])
            self.assertEqual(["alpha", "beta"], second["skipped"])
            self.assertEqual(2, post_json.call_count)


if __name__ == "__main__":
    unittest.main()
