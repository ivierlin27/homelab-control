"""Workflow A: multi-day incident post-mortem.

Inputs:

- a multi-day Proxmox / Forgejo / vLLM service log (lines handle)
- a Planka card thread for the related incident (records handle)
- a memory-engine extract of related observability notes (records handle)

Goal: produce a single JSON post-mortem object with timeline, suspected
contributing factors, and concrete next steps.

Each variant returns the same final JSON shape so the comparison runner can
score them uniformly. The RLM variant uses a scripted root with a fixed
probe sequence; the live planner variant lives in the runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .common import (
    BenchmarkRun,
    Budget,
    SubCallInvoker,
    SubCallResult,
    SubCallSchemaError,
    append_run,
    approx_tokens,
    call_direct,
    call_rag,
    call_rlm,
    measured,
    score_keyword_rubric,
    utc_now,
)
from ..sandbox import Sandbox


WORKFLOW = "incident_postmortem"
INSTRUCTION = (
    "You are writing an incident post-mortem covering the homelab's vLLM gateway. "
    "Reconstruct the timeline, name suspected contributing factors, and propose "
    "three concrete remediation steps."
)
REQUIRED_KEYWORDS = (
    "timeline",
    "vllm",
    "remediation",
    "memory",
    "gateway",
)


@dataclass
class PostmortemInputs:
    log_lines: list[str]
    planka_thread: list[dict[str, Any]]
    memory_notes: list[dict[str, Any]]

    def to_raw_text(self) -> str:
        sections = [
            "## Service log",
            "\n".join(self.log_lines),
            "",
            "## Planka thread",
            "\n".join(f"[{entry.get('actor', '')}] {entry.get('text', '')}" for entry in self.planka_thread),
            "",
            "## Memory notes",
            "\n".join(f"- {note.get('record_key', '')}: {note.get('text', '')}" for note in self.memory_notes),
        ]
        return "\n".join(sections)


def build_sandbox(inputs: PostmortemInputs) -> Sandbox:
    sandbox = Sandbox()
    sandbox.add_lines(
        "service-log",
        inputs.log_lines,
        schema="lines: ISO timestamp + level + service + message",
        provenance={"source": "fluentbit", "service": "vllm-gateway"},
    )
    sandbox.add_records(
        "planka-thread",
        inputs.planka_thread,
        schema="records: actor, text, ts, kind",
        provenance={"source": "planka", "card": "incident-vllm-2026-04"},
    )
    sandbox.add_records(
        "memory-notes",
        inputs.memory_notes,
        schema="records: record_key, text, principal",
        provenance={"source": "memory-engine"},
    )
    return sandbox


def rlm_probes() -> list[dict[str, Any]]:
    return [
        {"name": "grep", "args": {"handle": "service-log", "pattern": "ERROR"}},
        {"name": "count", "args": {"handle": "service-log", "pattern": "ERROR"}},
        {"name": "grep", "args": {"handle": "service-log", "pattern": "OOM|out of memory|cuda"}},
        {
            "name": "summarize_via_subcall",
            "args": {
                "handle": "service-log",
                "range": [0, 0],
                "prompt": "Summarize the highest-severity events and their timestamps.",
            },
            "intent": "summarize",
        },
        {
            "name": "summarize_via_subcall",
            "args": {
                "handle": "planka-thread",
                "range": [0, 0],
                "prompt": "Summarize the human discussion: what was tried, what was ruled out, what is open?",
            },
            "intent": "summarize",
        },
        {
            "name": "summarize_via_subcall",
            "args": {
                "handle": "memory-notes",
                "range": [0, 0],
                "prompt": "Pull related prior incidents or recurring failure modes.",
            },
            "intent": "summarize",
        },
        {
            "name": "finalize",
            "args": {
                "prompt": (
                    "Using the orchestration notes and handle metadata, produce a post-mortem with "
                    "timeline, contributing factors, and three concrete remediation steps. Use citations."
                ),
            },
            "intent": "plan",
        },
    ]


def run_variant(
    *,
    variant: str,
    inputs: PostmortemInputs,
    invoker: SubCallInvoker,
    audit_dir: Path,
    results_path: Path,
    budget: Budget | None = None,
) -> BenchmarkRun:
    started_at = utc_now()
    sandbox = build_sandbox(inputs)
    raw_text = inputs.to_raw_text()
    aborted_reason: str | None = None
    final_payload: dict[str, Any] = {}
    citations: list[dict[str, Any]] = []
    final_summary = ""
    tokens_in = 0
    tokens_out = 0
    subcalls = 0
    audit_path: Path | None = None

    if variant == "direct":
        try:
            (result, latency_ms) = measured(lambda: call_direct(invoker, workflow=WORKFLOW, raw_text=raw_text, instruction=INSTRUCTION))
            final_payload = result.as_dict()
            final_summary = result.summary
            citations = result.citations
            tokens_in = result.tokens_in
            tokens_out = result.tokens_out
            subcalls = 1
        except SubCallSchemaError as exc:
            aborted_reason = f"direct_schema_error: {exc}"
            latency_ms = 0
        root_tokens = approx_tokens(raw_text)
    elif variant == "rag":
        try:
            (result, latency_ms) = measured(
                lambda: call_rag(
                    invoker,
                    workflow=WORKFLOW,
                    raw_text=raw_text,
                    instruction=INSTRUCTION,
                    queries=["error", "oom", "cuda", "vllm", "timeline", "remediation"],
                )
            )
            final_payload = result.as_dict()
            final_summary = result.summary
            citations = result.citations
            tokens_in = result.tokens_in
            tokens_out = result.tokens_out
            subcalls = 1
        except SubCallSchemaError as exc:
            aborted_reason = f"rag_schema_error: {exc}"
            latency_ms = 0
        root_tokens = approx_tokens({"context": "rag-truncated"})
    elif variant == "rlm":
        audit_path = audit_dir / f"workflow_a_rlm_{int(approx_tokens(raw_text))}.jsonl"
        ((result, totals, aborted), latency_ms) = measured(
            lambda: call_rlm(
                invoker,
                workflow=WORKFLOW,
                sandbox=sandbox,
                probes=rlm_probes(),
                audit_path=audit_path,
                budget=budget,
                root_prompt=INSTRUCTION,
            )
        )
        if result is not None:
            final_payload = result.as_dict()
            final_summary = result.summary
            citations = result.citations
        aborted_reason = aborted
        tokens_in = int(totals.get("tokens_in", 0))
        tokens_out = int(totals.get("tokens_out", 0))
        subcalls = int(totals.get("subcalls", 0))
        root_tokens = approx_tokens(sandbox.metadata_all())
    else:
        raise ValueError(f"unknown variant: {variant}")

    rubric = score_keyword_rubric(payload=final_payload, required_keywords=REQUIRED_KEYWORDS)
    finished_at = utc_now()
    run = BenchmarkRun(
        workflow=WORKFLOW,
        variant=variant,
        started_at=started_at,
        finished_at=finished_at,
        latency_ms=int(latency_ms),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        root_tokens=root_tokens,
        subcalls=subcalls,
        final_summary=final_summary,
        final_payload=final_payload,
        aborted_reason=aborted_reason,
        citations=citations,
        audit_path=str(audit_path) if audit_path else None,
        rubric=rubric,
    )
    append_run(results_path, run)
    return run
