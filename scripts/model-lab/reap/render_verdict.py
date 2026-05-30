#!/usr/bin/env python3
"""Aggregate REAP bench artifacts into a verdict markdown note."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
DATE = os.environ.get("REAP_DATE", "2026-05-29")
BENCH_DOCS = REPO / "docs/model-lab/bench-artifacts"
BENCH_DATA = Path("/mnt/data/bench-artifacts")
SMOKE_DOC = REPO / f"docs/model-lab/{DATE}-reap-smoke"
VERDICT = REPO / f"docs/model-lab/{DATE}-reap-verdict.md"

KEYS = [
    "baseline",
    "test-a-llamacpp",
    "test-a-vllm",
    "test-b-llamacpp",
    "test-b-vllm",
    "test-c",
    "test-e",
    "dual-fast",
    "dual-strong-glm47",
]

PROMOTION = {
    "test-a-llamacpp": (
        "Matches/beats baseline on BFCL-lite, code micro, RULER 65K; "
        "no empty content on plain chat (GLM thinking mode)."
    ),
    "test-b-llamacpp": "Matches baseline within noise; frees >=2 GB/GPU vs 30B AWQ.",
    "test-c": "≥30 tok/s single-stream, RULER 32K + BFCL pass → strong-deep opt-in.",
}


def load_summary(key: str) -> dict[str, Any] | None:
    for base in (BENCH_DATA, BENCH_DOCS):
        for suffix in ("summary-micro.json", "summary.json"):
            p = base / f"{DATE}-{key}" / suffix
            if p.is_file():
                return json.loads(p.read_text(encoding="utf-8"))
            p = base / f"{DATE}-{key}-{suffix}"
            if p.is_file():
                return json.loads(p.read_text(encoding="utf-8"))
    return None


def load_smoke(key: str) -> dict[str, Any] | None:
    p = SMOKE_DOC / key / "smoke.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def micro_row(summary: dict[str, Any] | None, test: str) -> str:
    if not summary:
        return "— / — / —"
    tests = summary.get("tests") or summary.get("micro") or {}
    t = tests.get(test) or {}
    lat = t.get("latency_ms") or {}
    tok = t.get("decode_tok_s") or {}
    p50 = lat.get("p50") if isinstance(lat, dict) else "—"
    mean_tok = tok.get("mean") if isinstance(tok, dict) else "—"
    extra = ""
    if test == "tool_call_contract":
        extra = f" match={t.get('tool_match_rate', '—')}"
    return f"{p50}ms / {mean_tok} tok/s{extra}"


def bfcl_row(summary: dict[str, Any] | None) -> str:
    if not summary:
        return "—"
    bfcl = summary.get("bfcl") or summary.get("tests", {}).get("bfcl") or {}
    if isinstance(bfcl, dict) and "selection_mean" in bfcl:
        return f"sel {bfcl.get('selection_mean')} / args {bfcl.get('args_mean')}"
    return "—"


def render(partial: bool = False) -> None:
    lines = [
        f"# REAP benchmark verdict ({DATE})",
        "",
        "Roll-up from `scripts/model-lab/reap/` harness vs production",
        "`QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ` (strong-long).",
        "",
        "Reference: [REAP paper](https://arxiv.org/abs/2510.13999),",
        "[Cerebras collection](https://huggingface.co/collections/cerebras/cerebras-reap).",
        "",
    ]
    if partial:
        lines.append("_Partial render (harness may still be running)._")
        lines.append("")

    lines.extend(
        [
            "## Smoke (phase 2)",
            "",
            "| Key | passed | notes |",
            "|-----|--------|-------|",
        ]
    )
    smoke_keys = {
        "baseline": "baseline",
        "test-a-llamacpp": "test-a-llamacpp",
        "test-a-vllm": "test-a-llamacpp",
        "test-b-llamacpp": "test-b-llamacpp",
        "test-b-vllm": "test-b-vllm",
        "test-c": "test-c",
        "test-e": "test-e",
    }
    for key, smoke_name in smoke_keys.items():
        sm = load_smoke(smoke_name)
        if sm is None and key.startswith("test-b"):
            sm = load_smoke("test-b-llamacpp") or load_smoke("test-b-vllm")
        if sm:
            failed = [c["name"] for c in sm.get("cases", []) if not c.get("ok")]
            lines.append(
                f"| `{key}` | {sm.get('passed')} | "
                f"{', '.join(failed) if failed else 'all cases ok'} |"
            )
    for key in KEYS:
        if key in smoke_keys:
            continue
        lines.append(f"| `{key}` | — | _no smoke phase_ |")

    lines.extend(
        [
            "",
            "## Harness micro (p50 / decode tok/s)",
            "",
            "| Model | code_config_review | short_ops_summary | tool_call_contract |",
            "|-------|-------------------|-------------------|---------------------|",
        ]
    )
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
            "## BFCL-lite",
            "",
            "| Model | selection / args |",
            "|-------|------------------|",
        ]
    )
    for key in KEYS:
        lines.append(f"| `{key}` | {bfcl_row(load_summary(key))} |")

    lines.extend(
        [
            "",
            "## Promotion bars (from plan)",
            "",
        ]
    )
    for key, bar in PROMOTION.items():
        lines.append(f"- **{key}**: {bar}")

    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            "_Fill after reviewing artifacts on Alienware:_",
            "",
            "1. **Daily strong-long**: keep Qwen3-Coder-30B-A3B-AWQ / promote GLM-4.7-Flash-REAP-23B",
            "2. **strong-deep opt-in**: GLM-4.5-Air-REAP-82B if Test C clears bar",
            "3. **Dual fast+strong**: enable only if Test D micro passes on both ports",
            "4. **Test E**: document reasoning-leakage vs Phase-2 Qwen3.6 baseline",
            "",
            "Promotion checklist ([STRONG_MODEL_STRATEGY.md](../STRONG_MODEL_STRATEGY.md)):",
            "- [ ] Clean startup at target context",
            "- [ ] Gateway + local endpoint smoke",
            "- [ ] Matches or beats baseline on harness",
            "- [ ] Tool-call reliability acceptable",
            "- [ ] PR + 24h production soak",
            "",
        ]
    )

    VERDICT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {VERDICT}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partial", action="store_true")
    args = ap.parse_args()
    render(partial=args.partial)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
