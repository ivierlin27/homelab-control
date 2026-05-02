"""Run RLM benchmarks and emit a side-by-side comparison report.

Usage (deterministic Mac/CI run, no GPU):

    python3 -m _shared.rlm.benchmarks.runner --mode synthetic \
        --output-dir ~/.local/state/homelab-control/rlm-spike

Usage (live Alienware run against the gateway):

    python3 -m _shared.rlm.benchmarks.runner --mode live \
        --output-dir ~/.local/state/homelab-control/rlm-spike

Outputs:

- results.jsonl       one record per workflow×variant
- comparison.md       human-readable side-by-side rendering
- summary.json        machine-readable summary used by the decision memo
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from _shared.rlm.benchmarks.common import BenchmarkRun, Budget, VARIANTS  # noqa: E402
from _shared.rlm.benchmarks.fixtures import (  # noqa: E402
    ScriptedTransport,
    synthetic_postmortem_inputs,
    synthetic_weekly_review_inputs,
)
from _shared.rlm.benchmarks import workflow_a_postmortem, workflow_b_weekly_review  # noqa: E402
from _shared.rlm.subcall import SubCallInvoker  # noqa: E402


WORKFLOWS = {
    workflow_a_postmortem.WORKFLOW: workflow_a_postmortem,
    workflow_b_weekly_review.WORKFLOW: workflow_b_weekly_review,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunnerArgs:
    mode: str
    output_dir: Path
    scale_postmortem: int
    scale_weekly: int


def _build_invoker(mode: str) -> SubCallInvoker:
    if mode == "synthetic":
        return SubCallInvoker(transport=ScriptedTransport(), intent_to_model={
            "summarize": "homelab-fast",
            "classify": "homelab-fast",
            "code": "homelab-strong",
            "plan": "homelab-strong",
        })
    if mode == "live":
        return SubCallInvoker()
    raise ValueError(f"unknown mode: {mode}")


def run_all(args: RunnerArgs) -> dict[str, Any]:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results_path = args.output_dir / "results.jsonl"
    if results_path.exists():
        results_path.unlink()
    audit_dir = args.output_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    invoker = _build_invoker(args.mode)
    runs: list[BenchmarkRun] = []

    inputs_a = synthetic_postmortem_inputs(scale=args.scale_postmortem)
    inputs_b = synthetic_weekly_review_inputs(scale=args.scale_weekly)

    budget = Budget(
        max_root_tokens=int(os.environ.get("RLM_BUDGET_ROOT_TOKENS", "8192")),
        max_subcalls=int(os.environ.get("RLM_BUDGET_SUBCALLS", "16")),
        max_total_tokens=int(os.environ.get("RLM_BUDGET_TOTAL_TOKENS", "400000")),
        max_wall_seconds=int(os.environ.get("RLM_BUDGET_WALL_SECONDS", "1200")),
    )

    for variant in VARIANTS:
        run = workflow_a_postmortem.run_variant(
            variant=variant,
            inputs=inputs_a,
            invoker=invoker,
            audit_dir=audit_dir,
            results_path=results_path,
            budget=budget,
        )
        runs.append(run)
    for variant in VARIANTS:
        run = workflow_b_weekly_review.run_variant(
            variant=variant,
            inputs=inputs_b,
            invoker=invoker,
            audit_dir=audit_dir,
            results_path=results_path,
            budget=budget,
        )
        runs.append(run)

    summary = build_summary(runs)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (args.output_dir / "comparison.md").write_text(render_comparison(runs))
    return summary


def build_summary(runs: list[BenchmarkRun]) -> dict[str, Any]:
    by_workflow: dict[str, dict[str, dict[str, Any]]] = {}
    for run in runs:
        record = run.as_dict()
        by_workflow.setdefault(run.workflow, {})[run.variant] = {
            "latency_ms": record["latency_ms"],
            "tokens_in": record["tokens_in"],
            "tokens_out": record["tokens_out"],
            "tokens_total": record["tokens_total"],
            "root_tokens": record["root_tokens"],
            "subcalls": record["subcalls"],
            "rubric": record["rubric"],
            "aborted_reason": record["aborted_reason"],
            "summary_excerpt": (record["final_summary"] or "")[:200],
        }
    return {
        "generated_at": utc_now(),
        "by_workflow": by_workflow,
    }


def render_comparison(runs: list[BenchmarkRun]) -> str:
    lines = ["# RLM Spike: side-by-side comparison", "", f"_Generated: {utc_now()}_", ""]
    workflows: dict[str, dict[str, BenchmarkRun]] = {}
    for run in runs:
        workflows.setdefault(run.workflow, {})[run.variant] = run

    for workflow_name, by_variant in workflows.items():
        lines.append(f"## {workflow_name}")
        lines.append("")
        lines.append("| Variant | Latency (ms) | Tokens in | Tokens out | Tokens total | Root tokens | Sub-calls | Keyword cov. | Citations | Confidence | Aborted |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |")
        for variant in VARIANTS:
            run = by_variant.get(variant)
            if not run:
                continue
            rubric = run.rubric or {}
            lines.append(
                "| {variant} | {latency} | {tin} | {tout} | {ttot} | {root} | {sub} | {kw} | {cit} | {conf} | {ab} |".format(
                    variant=variant,
                    latency=run.latency_ms,
                    tin=run.tokens_in,
                    tout=run.tokens_out,
                    ttot=run.tokens_in + run.tokens_out,
                    root=run.root_tokens,
                    sub=run.subcalls,
                    kw=rubric.get("keyword_coverage", 0),
                    cit=rubric.get("citation_count", 0),
                    conf=rubric.get("confidence", ""),
                    ab=run.aborted_reason or "",
                )
            )
        lines.append("")
        for variant in VARIANTS:
            run = by_variant.get(variant)
            if not run:
                continue
            excerpt = (run.final_summary or "(no final summary)").strip()
            lines.append(f"### {workflow_name} / {variant}")
            lines.append("")
            lines.append(f"> {excerpt}")
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("synthetic", "live"), default="synthetic")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--scale-postmortem", type=int, default=200)
    parser.add_argument("--scale-weekly", type=int, default=120)
    parsed = parser.parse_args(argv)
    summary = run_all(
        RunnerArgs(
            mode=parsed.mode,
            output_dir=Path(parsed.output_dir).expanduser(),
            scale_postmortem=parsed.scale_postmortem,
            scale_weekly=parsed.scale_weekly,
        )
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
