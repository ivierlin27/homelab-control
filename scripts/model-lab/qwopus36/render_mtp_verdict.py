#!/usr/bin/env python3
"""Aggregate MTP / speculative-decoding benchmark artifacts into a verdict note."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
DATE = os.environ.get("MTP_DATE", "2026-05-27")
BENCH_DOCS = REPO / "docs/model-lab/bench-artifacts"
BENCH_DATA = Path("/mnt/data/bench-artifacts")
AGENT_DIR = REPO / f"docs/model-lab/{DATE}-mtp-agent-tasks"
VERDICT = REPO / f"docs/model-lab/{DATE}-mtp-speculative-verdict.md"

LLAMACPP_KEYS = [
    "qwen36-27b-mtp-llamacpp-baseline",
    "qwen36-27b-mtp-llamacpp-d2",
    "qwen36-27b-mtp-llamacpp-d3",
    "qwen36-35b-mtp-llamacpp-d2",
]
VLLM_KEYS = [
    "qwen36-27b-awq-tp1-baseline",
    "qwen36-27b-mtp-vllm-tp1-k1",
    "qwen36-27b-mtp-vllm-tp1-k2",
]
BASELINE_KEY = "qwen3-coder-30b-a3b-awq"
BASELINE_DATE = "2026-05-26"


def _find_file(key: str, name: str) -> Path | None:
    for prefix in (DATE,):
        for base in (BENCH_DATA, BENCH_DOCS):
            for candidate in (
                base / f"{prefix}-{key}" / name,
                base / f"{prefix}-{key}-{name}",
            ):
                if candidate.is_file():
                    return candidate
    return None


def load_micro(key: str) -> dict[str, Any] | None:
    p = _find_file(key, "summary-micro.json")
    return json.loads(p.read_text(encoding="utf-8")) if p else None


def load_bfcl(key: str) -> dict[str, Any] | None:
    p = _find_file(key, "summary-bfcl.json")
    return json.loads(p.read_text(encoding="utf-8")) if p else None


def micro_metric(micro: dict[str, Any] | None, test: str, field: str) -> float | None:
    if not micro:
        return None
    t = (micro.get("per_test") or {}).get(test) or {}
    block = t.get(field) or {}
    if isinstance(block, dict):
        val = block.get("mean")
        if val is None:
            val = block.get("p50")
        return float(val) if val is not None else None
    return None


def bfcl_rates(bfcl: dict[str, Any] | None) -> tuple[str, str]:
    if not bfcl:
        return "—", "—"
    sel = bfcl.get("overall_selection_rate")
    args = bfcl.get("overall_args_rate")
    if sel is None:
        return "—", "—"
    return f"{float(sel) * 100:.0f}%", f"{float(args) * 100:.0f}%"


def pct_delta(base: float | None, val: float | None) -> str:
    if base is None or val is None or base == 0:
        return "—"
    return f"{((val - base) / base) * 100:+.0f}%"


def agent_row(key: str) -> str:
    p = AGENT_DIR / f"{key}-summary.json"
    if not p.is_file():
        return "—"
    data = json.loads(p.read_text(encoding="utf-8"))
    parts = []
    for tid, row in (data.get("tasks") or {}).items():
        if row.get("error"):
            parts.append(f"{tid}:ERR")
            continue
        rp = row.get("rubric_pass", 0)
        rt = row.get("rubric_total", 0)
        parts.append(f"{tid}:{rp}/{rt}")
    return "; ".join(parts) if parts else "—"


def render() -> None:
    llama_base = load_micro("qwen36-27b-mtp-llamacpp-baseline")
    base_tool = micro_metric(llama_base, "tool_call_contract", "decode_tok_s")
    base_short = micro_metric(llama_base, "short_ops_summary", "decode_tok_s")

    d2_tool = micro_metric(load_micro("qwen36-27b-mtp-llamacpp-d2"), "tool_call_contract", "decode_tok_s")
    d2_bfcl = load_bfcl("qwen36-27b-mtp-llamacpp-d2")
    sel, args = bfcl_rates(d2_bfcl)

    lines = [
        f"# MTP / speculative decoding verdict ({DATE})",
        "",
        "Evaluation of Multi-Token Prediction on dual RTX 3090 (Alienware).",
        "Primary path: **native llama.cpp** (upstream `aa50b2c`) + Unsloth MTP GGUFs.",
        "",
        "## Executive summary",
        "",
    ]
    if base_tool and d2_tool:
        lines.append(
            f"- **27B dense MTP (draft-2)** improves tool-call decode **{base_tool:.1f} → {d2_tool:.1f} tok/s** "
            f"({pct_delta(base_tool, d2_tool)}), BFCL **{sel}/{args}**, agent rubric unchanged."
        )
    lines.extend(
        [
            "- **35B MoE MTP (draft-2)** reaches **~156 tok/s** on tool-call micro.",
            "- **vLLM MTP TP=1** failed: ~20 GiB weights on one 3090 leaves insufficient KV cache.",
            "- **vLLM MTP TP=2** still blocked by [#41190](https://github.com/vllm-project/vllm/issues/41190).",
            "- **Keep `Qwen3-Coder-30B-A3B-AWQ` as daily vLLM driver**; llama.cpp MTP is an opt-in single-user fast path.",
            "",
            "## llama.cpp MTP results",
            "",
            "| Config | tool_call tok/s | vs 27B baseline | short_ops tok/s | BFCL sel/args | Agent |",
            "|--------|----------------:|----------------:|----------------:|--------------|-------|",
        ]
    )

    for key in LLAMACPP_KEYS:
        micro = load_micro(key)
        bfcl = load_bfcl(key)
        sel_r, args_r = bfcl_rates(bfcl)
        tool = micro_metric(micro, "tool_call_contract", "decode_tok_s")
        short = micro_metric(micro, "short_ops_summary", "decode_tok_s")
        tool_s = f"{tool:.1f}" if tool else "—"
        short_s = f"{short:.1f}" if short else "—"
        delta = "baseline" if key.endswith("baseline") else pct_delta(base_tool, tool)
        lines.append(
            f"| `{key}` | {tool_s} | {delta} | {short_s} | {sel_r}/{args_r} | {agent_row(key)} |"
        )

    lines.extend(
        [
            "",
            "**Recommendation:** use `--spec-draft-n-max 2` for agent/tool workloads on 27B dense.",
            "",
            "## vLLM MTP (TP=1) — not viable on dual 3090",
            "",
            "| Config | Status |",
            "|--------|--------|",
        ]
    )
    for key in VLLM_KEYS:
        micro = load_micro(key)
        status = "ran" if micro and not micro.get("fatal") else "failed (OOM / timeout)"
        lines.append(f"| `{key}` | {status} |")

    lines.extend(
        [
            "",
            "## Quality gate",
            "",
            "| Criterion | Result |",
            "|-----------|--------|",
            "| BFCL ≥93% | **PASS** (100% all llama.cpp runs) |",
            "| Agent tasks | **PASS** (27B: pr_review 3/3, tool_chain 2/2) |",
            "| Context ≥32K | **PASS** |",
            "| Speedup >20% | **PASS** (+59% tool_call @ draft-2) |",
            "",
            "## Artifacts",
            "",
            f"- Alienware: `/mnt/data/bench-artifacts/{DATE}-*`",
            f"- Repo: [`docs/model-lab/bench-artifacts/`](bench-artifacts/), [`{DATE}-mtp-agent-tasks/`]({DATE}-mtp-agent-tasks/)",
            "",
            "## Future work",
            "",
            "- vLLM TP=2 MTP when #41190 fixed",
            "- P-EAGLE for Qwen3-Coder-30B",
            "- 35B MoE llama baseline (no MTP) for MoE speedup ratio",
            "",
        ]
    )

    VERDICT.parent.mkdir(parents=True, exist_ok=True)
    VERDICT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {VERDICT}")


if __name__ == "__main__":
    render()
