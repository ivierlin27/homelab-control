import tempfile
import unittest
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat_core import handle_chat_turn, source_allowed
from conversation_store import ConversationStore
import main as executive


class ChatInterfaceTests(unittest.TestCase):
    def test_conversation_store_persists_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ConversationStore(Path(tmpdir) / "conversations.sqlite3")
            conversation = store.upsert_conversation(
                conversation_id="local-web:test",
                title="Test",
                source="local-web",
                source_ref="test",
                domain="homelab",
                task_type="research",
            )
            store.add_turn(conversation["id"], role="user", content="hello")
            turns = store.list_turns(conversation["id"])

            self.assertEqual("local-web:test", conversation["id"])
            self.assertEqual(1, len(turns))
            self.assertEqual("hello", turns[0]["content"])

    def test_source_policy_blocks_disabled_discord(self) -> None:
        policy = executive.load_yaml(executive.DEFAULT_POLICY)
        allowed, reason = source_allowed(
            policy,
            source="discord-dm",
            metadata={"user_id": "123"},
        )

        self.assertFalse(allowed)
        self.assertIn("not enabled", reason)

    def test_local_chat_turn_records_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            store = ConversationStore(state_dir / "conversations.sqlite3")
            conversation = store.upsert_conversation(
                conversation_id="local-web:test",
                title="Test",
                source="local-web",
                source_ref="test",
                domain="homelab",
                task_type="research",
                plan_ready=True,
            )

            with mock.patch.object(executive, "create_planka_card") as create:
                create.return_value = {"card": {"id": "card-1"}, "list_id": "plan-ready"}
                result = handle_chat_turn(
                    store=store,
                    conversation=conversation,
                    message="Research a better calendar.",
                    source="local-web",
                    source_ref="local-web:test",
                    source_user="kevin",
                    metadata={"user_id": "kevin"},
                    state_dir=state_dir,
                    policy_path=executive.DEFAULT_POLICY,
                    dry_run=True,
                )

            turns = store.list_turns(conversation["id"])
            ledger = (state_dir / "trust-ledger.jsonl").read_text()

            self.assertIn("Decision: plan_ready", result["reply"])
            self.assertEqual(["user", "assistant"], [turn["role"] for turn in turns])
            self.assertIn('"source": "local-web"', ledger)
            self.assertIn('"conversation_id": "local-web:test"', ledger)


if __name__ == "__main__":
    unittest.main()
