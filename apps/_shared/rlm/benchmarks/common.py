"""Shared utilities for RLM benchmark workflows.

Each workflow runs three variants — direct, RAG, RLM — through these helpers so
the result records, scoring rubric, and audit hooks stay consistent. The
benchmarks intentionally accept either a real :class:`SubCallInvoker` (for live
runs against the Alienware gateway) or a deterministic transport (for the spike
tests and reproducible runs without GPU access).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ..audit import AuditLog
from ..harness import Budget, Harness, ScriptedRoot
from ..sandbox import Sandbox
from ..subcall import SubCallInvoker, SubCallResult, SubCallSchemaError


VARIANTS = ("direct", "rag", "rlm")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def approx_tokens(value: Any) -> int:
    if value is None:
        return 0
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return max(1, len(text) // 4)


@dataclass
class BenchmarkRun:
    workflow: str
    variant: str
    started_at: str
    finished_at: str
    latency_ms: int
    tokens_in: int
    tokens_out: int
    root_tokens: int
    subcalls: int
    final_summary: str
    final_payload: dict[str, Any]
    aborted_reason: str | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    audit_path: str | None = None
    rubric: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "variant": self.variant,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "latency_ms": self.latency_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_in + self.tokens_out,
            "root_tokens": self.root_tokens,
            "subcalls": self.subcalls,
            "final_summary": self.final_summary,
            "final_payload": self.final_payload,
            "aborted_reason": self.aborted_reason,
            "citations": self.citations,
            "audit_path": self.audit_path,
            "rubric": self.rubric,
        }


def append_run(path: Path, run: BenchmarkRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(run.as_dict(), sort_keys=True, default=str) + "\n")


def load_lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def naive_keyword_score(text: str, keywords: Iterable[str]) -> float:
    haystack = text.lower()
    matches = sum(1 for keyword in keywords if keyword.lower() in haystack)
    total = max(1, len(list(keywords)))
    return round(matches / total, 3)


def call_direct(
    invoker: SubCallInvoker,
    *,
    workflow: str,
    raw_text: str,
    instruction: str,
) -> SubCallResult:
    """Direct variant: pass everything to the model in one shot.

    The model is asked to produce the same JSON schema as a sub-call so
    scoring is uniform across variants. If the input would not fit in the
    sub-call context the call will fail and the run records an
    ``aborted_reason`` of ``direct_input_oversize``.
    """

    sub_prompt = (
        f"Workflow: {workflow}. {instruction} "
        "Respond with the standard JSON schema (summary, citations, confidence, open_questions)."
    )
    return invoker.call(intent="plan", sub_prompt=sub_prompt, context={"raw": raw_text})


def call_rag(
    invoker: SubCallInvoker,
    *,
    workflow: str,
    raw_text: str,
    instruction: str,
    queries: Iterable[str],
    top_k: int = 8,
) -> SubCallResult:
    """RAG variant: keyword-rank chunks, send only top-K to the model.

    No external vector store: this is a deliberately simple BM25-shaped scorer
    so the comparison isolates the question of whether RAG-style truncation
    beats RLM-style sandboxing for these workflows. Real RAG would replace
    this scorer; the harness contract does not change.
    """

    chunks = [chunk.strip() for chunk in re.split(r"\n{2,}", raw_text) if chunk.strip()]
    queries = list(queries)
    scored: list[tuple[float, int, str]] = []
    for index, chunk in enumerate(chunks):
        haystack = chunk.lower()
        score = sum(haystack.count(query.lower()) for query in queries)
        if score:
            scored.append((float(score), index, chunk))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = [item[2] for item in scored[:top_k]]
    sub_prompt = (
        f"Workflow: {workflow}. {instruction} You receive only the most relevant chunks; "
        "say so explicitly in 'open_questions' if you cannot answer."
    )
    return invoker.call(
        intent="plan",
        sub_prompt=sub_prompt,
        context={"chunks": selected, "queries": queries, "skipped": max(0, len(chunks) - len(selected))},
    )


def call_rlm(
    invoker: SubCallInvoker,
    *,
    workflow: str,
    sandbox: Sandbox,
    probes: list[dict[str, Any]],
    audit_path: Path,
    budget: Budget | None = None,
    root_prompt: str = "",
) -> tuple[SubCallResult | None, dict[str, Any], str | None]:
    """RLM variant: run a scripted-root harness over the sandbox."""

    audit = AuditLog(audit_path)
    harness = Harness(sandbox=sandbox, invoker=invoker, audit=audit, budget=budget or Budget())
    root = ScriptedRoot(probes)
    result = harness.run(root=root, root_prompt=root_prompt or workflow)
    return result.final, result.totals, result.aborted_reason


def score_keyword_rubric(*, payload: dict[str, Any], required_keywords: Iterable[str]) -> dict[str, Any]:
    summary = str(payload.get("summary", ""))
    citation_count = len(payload.get("citations", []) or [])
    confidence = str(payload.get("confidence", "low"))
    keyword_score = naive_keyword_score(summary, required_keywords)
    return {
        "keyword_coverage": keyword_score,
        "citation_count": citation_count,
        "confidence": confidence,
        "summary_length_chars": len(summary),
    }


def measured(call: Callable[[], Any]) -> tuple[Any, int]:
    start = time.monotonic()
    result = call()
    return result, int((time.monotonic() - start) * 1000)
