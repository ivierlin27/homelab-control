"""RLM orchestrator: probes, sub-calls, budgets, and audit.

The root model decides what probes to run; the harness executes them against
the sandbox and records every step. The eventual answer comes from a final
``finalize`` sub-call so the root never holds the prose.

For the spike the root is pluggable: a :class:`ScriptedRoot` produces a fixed
probe sequence (used by tests and deterministic benchmarks), and a
:class:`GatewayRoot` calls the LiteLLM gateway when a real planner is needed.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from .audit import AuditLog
from .sandbox import Handle, Sandbox
from .subcall import SubCallInvoker, SubCallResult, SubCallSchemaError


class RootProbeError(ValueError):
    """Raised when the root issues an unsupported or malformed probe."""


class BudgetExhausted(RuntimeError):
    """Raised when an orchestration runs out of root tokens, sub-calls, or wall time."""


@dataclass
class Budget:
    max_root_tokens: int = 4096
    max_subcalls: int = 12
    max_total_tokens: int = 200_000
    max_wall_seconds: int = 600
    subcall_quality_floor: str = "low"


@dataclass
class OrchestrationResult:
    orchestration_id: str
    final: SubCallResult | None
    audit_path: Path
    totals: dict[str, Any]
    aborted_reason: str | None = None
    aborted_step: int | None = None
    aborted_at: float | None = None
    notes: list[str] = field(default_factory=list)
    handle_metadata: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ProbeRequest:
    name: str
    args: dict[str, Any]
    intent: str = "summarize"


class RootStrategy(Protocol):
    def next_probe(
        self,
        *,
        orchestration_id: str,
        sandbox_metadata: list[dict[str, Any]],
        notes: list[str],
        last_result: dict[str, Any] | None,
        step: int,
    ) -> ProbeRequest: ...


class ScriptedRoot:
    """Deterministic root used by tests and reproducible benchmarks."""

    def __init__(self, probes: Iterable[dict[str, Any]]) -> None:
        self._probes = list(probes)
        self._index = 0

    def next_probe(
        self,
        *,
        orchestration_id: str,
        sandbox_metadata: list[dict[str, Any]],
        notes: list[str],
        last_result: dict[str, Any] | None,
        step: int,
    ) -> ProbeRequest:
        if self._index >= len(self._probes):
            return ProbeRequest(name="finalize", args={"prompt": "Synthesize the final answer."})
        probe = self._probes[self._index]
        self._index += 1
        return ProbeRequest(
            name=str(probe.get("name", "")).lower(),
            args=dict(probe.get("args", {})),
            intent=str(probe.get("intent", "summarize")),
        )


class GatewayRoot:
    """Calls the gateway to choose the next probe.

    The harness presents the sandbox metadata, recent notes, and the previous
    result to the model and asks for one of the supported probe directives in
    JSON. Used for live benchmark runs; tests should use :class:`ScriptedRoot`.
    """

    def __init__(self, *, invoker: SubCallInvoker, intent: str = "plan") -> None:
        self._invoker = invoker
        self._intent = intent

    def next_probe(
        self,
        *,
        orchestration_id: str,
        sandbox_metadata: list[dict[str, Any]],
        notes: list[str],
        last_result: dict[str, Any] | None,
        step: int,
    ) -> ProbeRequest:
        sub_prompt = (
            "You are the root planner of a recursive language model. Choose the next probe "
            "from this vocabulary: head, tail, slice, grep, count, index_by, summarize_via_subcall, "
            "aggregate_via_subcall, derive, note, finalize. Respond with a sub-call JSON object "
            "whose 'summary' is one of those probe names plus its arguments as JSON, citations is "
            "empty, confidence reflects your certainty, and open_questions is empty."
        )
        context = {
            "step": step,
            "sandbox": sandbox_metadata,
            "notes": notes[-10:],
            "last_result": last_result or {},
        }
        try:
            result = self._invoker.call(intent=self._intent, sub_prompt=sub_prompt, context=context)
        except SubCallSchemaError as exc:
            raise RootProbeError(f"root planner returned malformed probe: {exc}") from exc
        try:
            parsed = json.loads(result.summary)
        except json.JSONDecodeError as exc:
            raise RootProbeError(f"root planner summary is not JSON: {result.summary!r}") from exc
        return ProbeRequest(
            name=str(parsed.get("name", "")).lower(),
            args=dict(parsed.get("args", {})),
            intent=str(parsed.get("intent", "summarize")),
        )


READ_PROBES = {"head", "tail", "slice", "grep", "count", "index_by", "describe"}
SUBCALL_PROBES = {"summarize_via_subcall", "aggregate_via_subcall", "finalize"}
SANDBOX_PROBES = {"derive", "note"}
ALL_PROBES = READ_PROBES | SUBCALL_PROBES | SANDBOX_PROBES


def _approx_tokens(value: Any) -> int:
    if value is None:
        return 0
    return max(1, len(json.dumps(value, default=str)) // 4)


class Harness:
    def __init__(
        self,
        *,
        sandbox: Sandbox,
        invoker: SubCallInvoker,
        audit: AuditLog,
        budget: Budget | None = None,
        max_steps: int = 24,
    ) -> None:
        self.sandbox = sandbox
        self.invoker = invoker
        self.audit = audit
        self.budget = budget or Budget()
        self.max_steps = max_steps

    def run(self, *, root: RootStrategy, root_prompt: str) -> OrchestrationResult:
        orchestration_id = uuid.uuid4().hex
        notes: list[str] = []
        last_result: dict[str, Any] | None = None
        finalized: SubCallResult | None = None
        aborted_reason: str | None = None
        aborted_step: int | None = None
        started = time.monotonic()
        root_token_estimate = _approx_tokens({"prompt": root_prompt, "sandbox": self.sandbox.metadata_all()})

        self.audit.record(
            {
                "kind": "orchestration_start",
                "orchestration_id": orchestration_id,
                "root_prompt": root_prompt,
                "sandbox_metadata": self.sandbox.metadata_all(),
                "budget": self.budget.__dict__,
            }
        )

        for step_number in range(1, self.max_steps + 1):
            sandbox_metadata = self.sandbox.metadata_all()
            try:
                probe = root.next_probe(
                    orchestration_id=orchestration_id,
                    sandbox_metadata=sandbox_metadata,
                    notes=notes,
                    last_result=last_result,
                    step=step_number,
                )
            except RootProbeError as exc:
                self.audit.record(
                    {
                        "kind": "policy_violation",
                        "orchestration_id": orchestration_id,
                        "error": str(exc),
                    }
                )
                aborted_reason = f"root_probe_error: {exc}"
                aborted_step = step_number
                break

            if probe.name not in ALL_PROBES:
                self.audit.record(
                    {
                        "kind": "policy_violation",
                        "orchestration_id": orchestration_id,
                        "name": probe.name,
                        "args": probe.args,
                        "error": "unsupported probe",
                    }
                )
                aborted_reason = f"unsupported_probe: {probe.name}"
                aborted_step = step_number
                break

            try:
                if probe.name in READ_PROBES or probe.name in SANDBOX_PROBES:
                    result_payload = self._run_read_probe(orchestration_id, probe, step_number)
                    last_result = {"probe": probe.name, "result": result_payload}
                    root_token_estimate += _approx_tokens(result_payload)
                else:
                    sub_result = self._run_subcall_probe(orchestration_id, probe, step_number)
                    last_result = {"probe": probe.name, "result": sub_result.as_dict()}
                    root_token_estimate += _approx_tokens(sub_result.as_dict())
                    notes.append(f"step {step_number} {probe.name}: {sub_result.summary[:120]}")
                    if probe.name == "finalize":
                        finalized = sub_result
                        break
                self._enforce_budget(
                    orchestration_id=orchestration_id,
                    step=step_number,
                    started=started,
                    root_token_estimate=root_token_estimate,
                )
            except BudgetExhausted as exc:
                aborted_reason = str(exc)
                aborted_step = step_number
                break
            except RootProbeError as exc:
                self.audit.record(
                    {
                        "kind": "policy_violation",
                        "orchestration_id": orchestration_id,
                        "name": probe.name,
                        "args": probe.args,
                        "error": str(exc),
                    }
                )
                aborted_reason = f"root_probe_error: {exc}"
                aborted_step = step_number
                break
        else:
            aborted_reason = "max_steps_reached"
            aborted_step = self.max_steps

        totals = self.audit.totals()
        self.audit.record(
            {
                "kind": "orchestration_end",
                "orchestration_id": orchestration_id,
                "aborted_reason": aborted_reason,
                "aborted_step": aborted_step,
                "totals": totals,
            }
        )

        return OrchestrationResult(
            orchestration_id=orchestration_id,
            final=finalized,
            audit_path=self.audit.path,
            totals=totals,
            aborted_reason=aborted_reason,
            aborted_step=aborted_step,
            aborted_at=time.monotonic() - started,
            notes=notes,
            handle_metadata=self.sandbox.metadata_all(),
        )

    def _run_read_probe(self, orchestration_id: str, probe: ProbeRequest, step: int) -> Any:
        args = probe.args
        if probe.name == "head":
            result = self.sandbox.head(args["handle"], int(args.get("n", 5)))
        elif probe.name == "tail":
            result = self.sandbox.tail(args["handle"], int(args.get("n", 5)))
        elif probe.name == "slice":
            result = self.sandbox.slice(args["handle"], int(args.get("start", 0)), int(args.get("end", 0)))
        elif probe.name == "grep":
            result = self.sandbox.grep(args["handle"], str(args.get("pattern", "")))
        elif probe.name == "count":
            result = self.sandbox.count(args["handle"], str(args.get("pattern", "")))
        elif probe.name == "index_by":
            result = self.sandbox.index_by(args["handle"], str(args.get("key", "")))
        elif probe.name == "describe":
            result = self.sandbox.metadata(args["handle"])
        elif probe.name == "derive":
            handle = self.sandbox.derive(
                args["handle"],
                str(args.get("transform", "")),
                str(args.get("name", "")),
            )
            result = {"created": handle.id, "kind": handle.kind, "length": handle.length()}
        elif probe.name == "note":
            text = str(args.get("text", ""))
            result = {"note": text}
        else:
            raise RootProbeError(f"unhandled read probe: {probe.name}")

        self.audit.record(
            {
                "kind": "probe",
                "orchestration_id": orchestration_id,
                "name": probe.name,
                "args": args,
                "result_summary": _summarize_for_audit(result),
            }
        )
        return result

    def _run_subcall_probe(self, orchestration_id: str, probe: ProbeRequest, step: int) -> SubCallResult:
        args = probe.args
        if probe.name == "summarize_via_subcall":
            handle_id = args["handle"]
            sub_prompt = str(args.get("prompt", "Summarize this slice for the root."))
            handle_range = args.get("range") or args.get("slice") or [0, self.sandbox.get(handle_id).length()]
            slice_payload = self.sandbox.slice(handle_id, int(handle_range[0]), int(handle_range[1]))
            context = {
                "handle": handle_id,
                "schema": self.sandbox.metadata(handle_id)["schema"],
                "range": handle_range,
                "slice": slice_payload,
            }
            intent = probe.intent or "summarize"
        elif probe.name == "aggregate_via_subcall":
            handle_ids = list(args.get("handles", []))
            sub_prompt = str(args.get("prompt", "Aggregate the summaries below."))
            context = {
                "handles": [
                    {
                        "handle": handle_id,
                        "metadata": self.sandbox.metadata(handle_id),
                    }
                    for handle_id in handle_ids
                ],
            }
            intent = probe.intent or "summarize"
        elif probe.name == "finalize":
            sub_prompt = str(args.get("prompt", "Produce the final answer using the orchestration notes."))
            context = {
                "notes": args.get("notes") or [],
                "handles": self.sandbox.metadata_all(),
            }
            intent = probe.intent or "plan"
        else:
            raise RootProbeError(f"unhandled sub-call probe: {probe.name}")

        try:
            result = self.invoker.call(intent=intent, sub_prompt=sub_prompt, context=context)
        except SubCallSchemaError as exc:
            self.audit.record(
                {
                    "kind": "subcall_schema_error",
                    "orchestration_id": orchestration_id,
                    "name": probe.name,
                    "args": args,
                    "error": str(exc),
                }
            )
            raise RootProbeError(f"sub-call schema error: {exc}") from exc

        self.audit.record(
            {
                "kind": "subcall",
                "orchestration_id": orchestration_id,
                "name": probe.name,
                "args": args,
                "intent": intent,
                "route": result.route,
                "model": result.model,
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
                "latency_ms": result.latency_ms,
                "confidence": result.confidence,
                "result_summary": result.summary[:240],
            }
        )
        return result

    def _enforce_budget(
        self,
        *,
        orchestration_id: str,
        step: int,
        started: float,
        root_token_estimate: int,
    ) -> None:
        elapsed = time.monotonic() - started
        totals = self.audit.totals()
        if elapsed > self.budget.max_wall_seconds:
            self._record_budget_event(orchestration_id, "wall_clock_exceeded", step, elapsed, totals, root_token_estimate)
            raise BudgetExhausted("wall_clock_exceeded")
        if totals["subcalls"] > self.budget.max_subcalls:
            self._record_budget_event(orchestration_id, "subcall_limit_exceeded", step, elapsed, totals, root_token_estimate)
            raise BudgetExhausted("subcall_limit_exceeded")
        if totals["tokens_total"] > self.budget.max_total_tokens:
            self._record_budget_event(orchestration_id, "total_tokens_exceeded", step, elapsed, totals, root_token_estimate)
            raise BudgetExhausted("total_tokens_exceeded")
        if root_token_estimate > self.budget.max_root_tokens:
            self._record_budget_event(orchestration_id, "root_tokens_exceeded", step, elapsed, totals, root_token_estimate)
            raise BudgetExhausted("root_tokens_exceeded")

    def _record_budget_event(
        self,
        orchestration_id: str,
        reason: str,
        step: int,
        elapsed: float,
        totals: dict[str, Any],
        root_token_estimate: int,
    ) -> None:
        self.audit.record(
            {
                "kind": "budget_exhausted",
                "orchestration_id": orchestration_id,
                "reason": reason,
                "step": step,
                "elapsed_seconds": elapsed,
                "totals": totals,
                "root_token_estimate": root_token_estimate,
            }
        )


def _summarize_for_audit(value: Any) -> Any:
    if isinstance(value, str):
        return value[:240]
    if isinstance(value, list):
        return value[:5]
    if isinstance(value, dict):
        return {key: _summarize_for_audit(item) for key, item in list(value.items())[:8]}
    return value
