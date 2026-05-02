"""Tests for the RLM harness, sandbox, sub-call invoker, and audit log."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


def _load_rlm_module(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_test_rlm_{name}",
        Path(__file__).resolve().parent / f"{name}.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Importing as a sibling package keeps the relative imports inside the modules
# valid; these helpers are only used to make the test runnable without a
# package install.
sandbox_module = importlib.import_module("_shared.rlm.sandbox") if False else None
del sandbox_module

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
