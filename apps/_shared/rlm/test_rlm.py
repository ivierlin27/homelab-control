"""Tests for the RLM harness, sandbox, sub-call invoker, and audit log."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


from _shared.rlm.audit import AuditLog
from _shared.rlm.harness import Budget, BudgetExhausted, Harness, ScriptedRoot
from _shared.rlm.sandbox import Sandbox
from _shared.rlm.subcall import SubCallInvoker, SubCallSchemaError


def _gateway_response(payload: dict) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }


class SandboxTests(unittest.TestCase):
    def test_metadata_includes_constant_size_prefix(self) -> None:
        sandbox = Sandbox()
        sandbox.add_lines("log-1", [f"line {i}" for i in range(100)])
        meta = sandbox.metadata("log-1")
        self.assertEqual("log-1", meta["id"])
        self.assertEqual(100, meta["length"])
        self.assertIn("line 0", meta["prefix"])
        self.assertIn("head", meta["accessor_set"])

    def test_grep_returns_line_numbers_for_lines_handles(self) -> None:
        sandbox = Sandbox()
        sandbox.add_lines("log-1", ["info ok", "ERROR boom", "info ok", "ERROR again"])
        matches = sandbox.grep("log-1", "ERROR")
        self.assertEqual([1, 3], [match["line"] for match in matches])

    def test_index_by_groups_records_by_key(self) -> None:
        sandbox = Sandbox()
        sandbox.add_records(
            "events",
            [
                {"id": 1, "actor": "alice"},
                {"id": 2, "actor": "bob"},
                {"id": 3, "actor": "alice"},
            ],
        )
        index = sandbox.index_by("events", "actor")
        self.assertEqual({"alice": [0, 2], "bob": [1]}, index)

    def test_derive_filter_creates_new_handle(self) -> None:
        sandbox = Sandbox()
        sandbox.add_lines("log-1", ["info ok", "ERROR boom", "info ok"])
        sandbox.derive("log-1", "filter ERROR", "log-1-errors")
        self.assertTrue(sandbox.has("log-1-errors"))
        meta = sandbox.metadata("log-1-errors")
        self.assertEqual(1, meta["length"])


class SubCallInvokerTests(unittest.TestCase):
    def test_call_parses_schema_and_preserves_metadata(self) -> None:
        captured: list[tuple[str, str]] = []

        def transport(intent: str, model: str, payload: dict) -> dict:
            captured.append((intent, model))
            return _gateway_response(
                {
                    "summary": "All good.",
                    "citations": [{"handle": "log-1", "range": [0, 1]}],
                    "confidence": "medium",
                    "open_questions": ["next?"],
                }
            )

        invoker = SubCallInvoker(transport=transport, intent_to_model={"summarize": "homelab-fast"})
        result = invoker.call(intent="summarize", sub_prompt="Summarize.", context={"slice": []})
        self.assertEqual("All good.", result.summary)
        self.assertEqual("medium", result.confidence)
        self.assertEqual([("summarize", "homelab-fast")], captured)

    def test_call_raises_on_malformed_response(self) -> None:
        def transport(intent: str, model: str, payload: dict) -> dict:
            return {"choices": [{"message": {"content": "not json"}}]}

        invoker = SubCallInvoker(transport=transport)
        with self.assertRaises(SubCallSchemaError):
            invoker.call(intent="summarize", sub_prompt="x", context={})

    def test_http_post_sends_attribution_headers(self) -> None:
        """Phase 0.6 follow-up: x-agent-principal + x-task-intent must reach the gateway."""
        from unittest import mock

        captured_request = {}

        class _FakeResp:
            def read(self) -> bytes:
                return b'{"choices":[{"message":{"content":"{\\"summary\\":\\"ok\\",\\"citations\\":[],\\"confidence\\":\\"low\\",\\"open_questions\\":[]}"}}],"usage":{}}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=None):
            captured_request["headers"] = dict(req.headers)
            return _FakeResp()

        with mock.patch.dict(
            "os.environ", {"AGENT_PRINCIPAL": "agent:executive", "MODEL_GATEWAY_BASE_URL": "http://gw"}
        ):
            with mock.patch("apps._shared.rlm.subcall.request.urlopen", _fake_urlopen):
                invoker = SubCallInvoker()
                invoker.call(intent="classify", sub_prompt="x", context={})

        headers = captured_request["headers"]
        # urllib normalizes header names to Capitalized
        self.assertEqual("agent:executive", headers.get("X-agent-principal"))
        self.assertEqual("classify", headers.get("X-task-intent"))

    def test_http_post_sends_x_skill_header_when_provided(self) -> None:
        """Phase 1 P1: x-skill must reach the gateway so the LocalOnlyGuard
        can attribute the call to a specific skill manifest."""
        from unittest import mock

        captured_request: dict = {}

        class _FakeResp:
            def read(self) -> bytes:
                return b'{"choices":[{"message":{"content":"{\\"summary\\":\\"ok\\",\\"citations\\":[],\\"confidence\\":\\"low\\",\\"open_questions\\":[]}"}}],"usage":{}}'
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=None):
            captured_request["headers"] = dict(req.headers)
            return _FakeResp()

        with mock.patch.dict(
            "os.environ",
            {
                "AGENT_PRINCIPAL": "agent:finance",
                "MODEL_GATEWAY_BASE_URL": "http://gw",
                # Empty snapshot path = no enforcement; we're testing header
                # injection only here, not the caller-side gate.
                "SKILL_POLICY_SNAPSHOT": "/nonexistent/policy.json",
            },
        ):
            with mock.patch("apps._shared.rlm.subcall.request.urlopen", _fake_urlopen):
                invoker = SubCallInvoker()
                invoker.call(
                    intent="classify",
                    sub_prompt="x",
                    context={},
                    skill_id="finance-categorize",
                )

        headers = captured_request["headers"]
        self.assertEqual("finance-categorize", headers.get("X-skill"))

    def test_caller_side_local_only_blocks_cloud_model(self) -> None:
        """Phase 1 P1: when AGENT_SKILL points at a local_only skill, the
        invoker refuses to dispatch to a non-local model BEFORE the HTTP
        request is built. The gateway is the canonical gate, but failing
        fast here gives the agent a clean Python exception to handle."""
        import json as _json
        import tempfile
        from unittest import mock
        from apps._shared.litellm_callbacks.local_only_policy import (
            LocalOnlyViolation,
        )

        # Snapshot: one local_only skill.
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False
        ) as fh:
            _json.dump(
                {
                    "schema": 1,
                    "skills": {
                        "finance-categorize": {"local_only": True, "version": 1}
                    },
                },
                fh,
            )
            snap_path = fh.name

        def _fake_urlopen(*a, **kw):  # pragma: no cover - must not be reached
            raise AssertionError("HTTP must not be attempted on a local_only violation")

        with mock.patch.dict(
            "os.environ",
            {
                "AGENT_PRINCIPAL": "agent:finance",
                "AGENT_SKILL": "finance-categorize",
                "MODEL_GATEWAY_BASE_URL": "http://gw",
                "SKILL_POLICY_SNAPSHOT": snap_path,
            },
        ):
            with mock.patch("apps._shared.rlm.subcall.request.urlopen", _fake_urlopen):
                # Force a non-local model via intent_to_model mapping.
                invoker = SubCallInvoker(
                    intent_to_model={"classify": "openai/gpt-4o-mini"}
                )
                with self.assertRaises(LocalOnlyViolation) as cm:
                    invoker.call(intent="classify", sub_prompt="x", context={})
        self.assertEqual("finance-categorize", cm.exception.skill_id)
        self.assertEqual("openai/gpt-4o-mini", cm.exception.model)


class HarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.tmp.name) / "audit.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build(self, sandbox: Sandbox, transport) -> Harness:
        invoker = SubCallInvoker(transport=transport, intent_to_model={"summarize": "homelab-fast", "plan": "homelab-strong"})
        audit = AuditLog(self.audit_path)
        return Harness(sandbox=sandbox, invoker=invoker, audit=audit)

    def test_run_executes_probes_and_finalizes(self) -> None:
        sandbox = Sandbox()
        sandbox.add_lines("log-1", ["info ok", "ERROR boom", "ERROR again"])

        responses = [
            _gateway_response(
                {
                    "summary": "two errors observed",
                    "citations": [{"handle": "log-1", "range": [1, 3]}],
                    "confidence": "high",
                    "open_questions": [],
                }
            ),
            _gateway_response(
                {
                    "summary": "Final answer: investigate ERROR boom and ERROR again.",
                    "citations": [{"handle": "log-1", "range": [1, 3]}],
                    "confidence": "high",
                    "open_questions": [],
                }
            ),
        ]
        sequence = iter(responses)

        def transport(intent: str, model: str, payload: dict) -> dict:
            return next(sequence)

        harness = self._build(sandbox, transport)
        root = ScriptedRoot(
            [
                {"name": "grep", "args": {"handle": "log-1", "pattern": "ERROR"}},
                {"name": "summarize_via_subcall", "args": {"handle": "log-1", "range": [0, 3], "prompt": "Summarize the ERRORs."}},
                {"name": "finalize", "args": {"prompt": "Synthesize."}, "intent": "plan"},
            ]
        )
        result = harness.run(root=root, root_prompt="Investigate the log.")
        self.assertIsNotNone(result.final)
        self.assertEqual("high", result.final.confidence)
        self.assertEqual(2, result.totals["subcalls"])
        self.assertGreaterEqual(result.totals["probes"], 1)

    def test_run_aborts_on_budget(self) -> None:
        sandbox = Sandbox()
        sandbox.add_lines("log-1", ["info ok", "ERROR boom"])

        def transport(intent: str, model: str, payload: dict) -> dict:
            return _gateway_response(
                {
                    "summary": "summary text",
                    "citations": [],
                    "confidence": "low",
                    "open_questions": [],
                }
            )

        invoker = SubCallInvoker(transport=transport)
        audit = AuditLog(self.audit_path)
        harness = Harness(
            sandbox=sandbox,
            invoker=invoker,
            audit=audit,
            budget=Budget(max_subcalls=1, max_root_tokens=10_000_000),
        )
        root = ScriptedRoot(
            [
                {"name": "summarize_via_subcall", "args": {"handle": "log-1", "range": [0, 2], "prompt": "x"}},
                {"name": "summarize_via_subcall", "args": {"handle": "log-1", "range": [0, 2], "prompt": "y"}},
                {"name": "summarize_via_subcall", "args": {"handle": "log-1", "range": [0, 2], "prompt": "z"}},
            ]
        )
        result = harness.run(root=root, root_prompt="x")
        self.assertEqual("subcall_limit_exceeded", result.aborted_reason)
        self.assertIsNone(result.final)

    def test_unknown_probe_records_policy_violation(self) -> None:
        sandbox = Sandbox()
        sandbox.add_lines("log-1", ["a", "b"])

        def transport(intent: str, model: str, payload: dict) -> dict:
            return _gateway_response({"summary": "x", "citations": [], "confidence": "low", "open_questions": []})

        harness = self._build(sandbox, transport)
        root = ScriptedRoot([{"name": "execute_arbitrary_python", "args": {}}])
        result = harness.run(root=root, root_prompt="x")
        self.assertTrue(result.aborted_reason.startswith("unsupported_probe"))
        events = [json.loads(line) for line in self.audit_path.read_text().splitlines()]
        self.assertTrue(any(event.get("kind") == "policy_violation" for event in events))


if __name__ == "__main__":
    unittest.main()
