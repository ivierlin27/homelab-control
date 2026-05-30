#!/usr/bin/env python3
"""Aggregate bench + agent-task artifacts into a verdict markdown note."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
DATE = os.environ.get("QWOPUS36_DATE", "2026-05-26")
BENCH_DOCS = REPO / "docs/model-lab/bench-artifacts"
BENCH_DATA = Path("/mnt/data/bench-artifacts")
AGENT_DIR = REPO / f"docs/model-lab/{DATE}-qwopus36-agent-tasks"
VERDICT = REPO / f"docs/model-lab/{DATE}-qwopus36-verdict.md"

KEYS = [
    "qwen3-coder-30b-a3b-awq",
    "qwopus36-35b-a3b-v1",
    "qwen36-35b-a3b-awq",
    "qwopus36-27b-v2",
    "qwen36-27b-awq",
    "qwopus36-27b-v2-mtp",
]


def load_summary(key: str) -> dict[str, Any] | None:
    for base in (BENCH_DATA, BENCH_DOCS):
        p = base / f"{DATE}-{key}" / "summary.json"
        if not p.is_file():
            p = base / f"{DATE}-{key}-summary.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def micro_row(summary: dict[str, Any] | None, test: str) -> str:
    if not summary:
        return "| — | — | — |"
    tests = summary.get("tests") or summary.get("micro") or {}
    t = tests.get(test) or {}
    lat = t.get("latency_ms") or {}
    tok = t.get("decode_tok_s") or {}
    p50 = lat.get("p50") if isinstance(lat, dict) else "—"
    mean_tok = tok.get("mean") if isinstance(tok, dict) else "—"
    ct = t.get("completion_tokens_mean") or t.get("mean_completion_tokens") or "—"
    return f"| {p50} | {mean_tok} | {ct} |"


def agent_summary(key: str) -> str:
    p = AGENT_DIR / f"{key}-summary.json"
    if not p.is_file():
        return "_no agent run_"
    data = json.loads(p.read_text(encoding="utf-8"))
    lines = []
    for tid, row in (data.get("tasks") or {}).items():
        rp = row.get("rubric_pass", 0)
        rt = row.get("rubric_total", 0)
        lines.append(
            f"- **{tid}**: {rp}/{rt} rubric, "
            f"{row.get('completion_tokens', 0)} completion tok, "
            f"{row.get('wall_s', 0)}s"
        )
    return "\n".join(lines) if lines else "_empty_"


def render(aggregate_only: bool = False) -> None:
    lines = [
        f"# Qwopus3.6 benchmark verdict ({DATE})",
        "",
        "Automated roll-up from `scripts/model-lab/qwopus36/` harness and agent tasks.",
        "",
        "## Harness micro (p50 ms / decode tok/s / mean completion tokens)",
        "",
        "| Model | code_config_review | short_ops_summary | tool_call_contract |",
        "|-------|-------------------|-------------------|---------------------|",
    ]
    for key in KEYS:
        s = load_summary(key)
        lines.append(
            f"| `{key}` | {micro_row(s, 'code_config_review')} "
            f"| {micro_row(s, 'short_ops_summary')} "
            f"| {micro_row(s, 'tool_call_contract')} |"
        )

    lines.extend(
        [
            "",
            "## Agent task rubric (Phase 3/5)",
            "",
        ]
    )
    for key in KEYS:
        if key in ("qwopus36-27b-v2-mtp",):
            continue
        lines.append(f"### `{key}`")
        lines.append(agent_summary(key))
        lines.append("")

    lines.extend(
        [
            "## Fine-tune delta (qualitative)",
            "",
            "- **35B MoE**: compare `qwopus36-35b-a3b-v1` vs `qwen36-35b-a3b-awq`",
            "- **27B dense**: compare `qwopus36-27b-v2` vs `qwen36-27b-awq`",
            "- **vs daily**: compare both against `qwen3-coder-30b-a3b-awq`",
            "",
            "## Recommendation",
            "",
            "_Fill after human review: promote / keep baseline / another round._",
            "",
            "Promotion checklist (from STRONG_MODEL_STRATEGY):",
            "- [ ] Clean startup at target context",
            "- [ ] Gateway + local endpoint smoke",
            "- [ ] Matches or beats baseline on agent tasks",
            "- [ ] Tool-call reliability acceptable",
            "- [ ] PR + 24h production soak",
            "",
        ]
    )

    VERDICT.parent.mkdir(parents=True, exist_ok=True)
    VERDICT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {VERDICT}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aggregate-only", action="store_true")
    args = ap.parse_args()
    render(aggregate_only=args.aggregate_only)


if __name__ == "__main__":
    main()
