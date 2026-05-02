"""Workflow B: weekly executive review aggregation.

Inputs:

- one week of trust-ledger events (records handle)
- one week of planka card transitions (records handle)
- one week of memory-engine writes (records handle)

Goal: produce a weekly digest with per-domain activity, open risks, and a
short list of items the operator should review next week.

The aggregation profile is what makes this interesting for RLM: many small
records, redundant fields per record, and a desire for cross-record claims
("homelab domain produced N safe-update cards this week"). The direct
variant has to swallow the entire ledger; the RLM variant uses index_by and
short summaries per domain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import (
    BenchmarkRun,
    Budget,
    SubCallInvoker,
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


WORKFLOW = "weekly_review"
INSTRUCTION = (
    "Produce a weekly executive review digest. List per-domain activity counts, "
    "explicit decisions and blocked actions, route usage (local vs cloud), open "
    "risks, and three items the operator should review next week."
)
REQUIRED_KEYWORDS = (
    "homelab",
    "blocked",
    "route",
    "review",
    "memory",
)


@dataclass
class WeeklyReviewInputs:
    trust_events: list[dict[str, Any]]
    planka_transitions: list[dict[str, Any]]
    memory_writes: list[dict[str, Any]]

    def to_raw_text(self) -> str:
        sections = [
            "## Trust ledger (this week)",
            "\n".join(json.dumps(event, sort_keys=True, default=str) for event in self.trust_events),
            "",
            "## Planka transitions",
            "\n".join(json.dumps(transition, sort_keys=True, default=str) for transition in self.planka_transitions),
            "",
            "## Memory writes",
            "\n".join(json.dumps(write, sort_keys=True, default=str) for write in self.memory_writes),
        ]
        return "\n".join(sections)


def build_sandbox(inputs: WeeklyReviewInputs) -> Sandbox:
    sandbox = Sandbox()
    sandbox.add_records(
        "trust-events",
        inputs.trust_events,
        schema="records: principal, project_domain, task_class, route, decision, requires_human_review, occurred_at",
        provenance={"source": "agent-executive trust-ledger.jsonl"},
    )
    sandbox.add_records(
        "planka-transitions",
        inputs.planka_transitions,
        schema="records: card_id, from_list, to_list, actor, ts",
        provenance={"source": "planka webhook"},
    )
    sandbox.add_records(
        "memory-writes",
        inputs.memory_writes,
        schema="records: record_key, principal, source, classification, occurred_at",
        provenance={"source": "memory-engine"},
    )
    return sandbox


def rlm_probes() -> list[dict[str, Any]]:
    return [
        {"name": "index_by", "args": {"handle": "trust-events", "key": "project_domain"}},
        {"name": "index_by", "args": {"handle": "trust-events", "key": "decision"}},
        {"name": "index_by", "args": {"handle": "trust-events", "key": "route"}},
        {"name": "count", "args": {"handle": "trust-events", "pattern": "requires_human_review.*true"}},
        {
            "name": "summarize_via_subcall",
            "args": {
                "handle": "trust-events",
                "range": [0, 0],
                "prompt": "Summarize per-domain decisions, route usage, and any blocked actions or human-review escalations.",
            },
            "intent": "summarize",
        },
        {"name": "index_by", "args": {"handle": "planka-transitions", "key": "to_list"}},
        {
            "name": "summarize_via_subcall",
            "args": {
                "handle": "planka-transitions",
                "range": [0, 0],
                "prompt": "Summarize what moved into Plan-Ready, what got Done, and what stalled.",
            },
            "intent": "summarize",
        },
        {
            "name": "summarize_via_subcall",
            "args": {
                "handle": "memory-writes",
                "range": [0, 0],
                "prompt": "Summarize the most consequential memory writes and any unusual record_key prefixes.",
            },
            "intent": "summarize",
        },
        {
            "name": "finalize",
            "args": {
                "prompt": (
                    "Produce a weekly executive review with per-domain activity counts, "
                    "explicit decisions and blocked actions, route usage, open risks, and "
                    "three items for the operator next week. Use citations."
                ),
            },
            "intent": "plan",
        },
    ]


def run_variant(
    *,
    variant: str,
    inputs: WeeklyReviewInputs,
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
                    queries=["homelab", "blocked", "route", "decision", "review", "memory"],
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
        audit_path = audit_dir / f"workflow_b_rlm_{int(approx_tokens(raw_text))}.jsonl"
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
