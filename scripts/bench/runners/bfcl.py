"""Tool-call correctness (BFCL-lite).

A small, self-contained battery of single-turn function-calling cases that
mirrors the BFCL v3 simple/parallel categories. Designed for fast,
repeatable A/B comparisons across models. Run multiple repeats and report
mean +/- stdev; this is more stable than full BFCL for daily tuning while
keeping the same metric idea (function selection + arg correctness).

Env:
    BENCH_BASE_URL, BENCH_API_KEY, BENCH_MODEL, BENCH_MODEL_KEY
    BENCH_BFCL_REPEATS  default 3
    OUT                 results dir
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .. import client, metrics


_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_gpu_memory",
            "description": "Read GPU memory usage on a host.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "gpu_index": {"type": "integer"},
                },
                "required": ["host"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_service",
            "description": "Restart a homelab systemd unit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "unit": {"type": "string"},
                },
                "required": ["host", "unit"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_logs",
            "description": "Summarize recent journalctl output for a unit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "unit": {"type": "string"},
                    "since_minutes": {"type": "integer"},
                },
                "required": ["host", "unit"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_inventory",
            "description": "Find homelab hosts matching a query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
]


_CASES: list[dict] = [
    {
        "id": "simple_gpu_memory",
        "messages": [{"role": "user", "content": "Check GPU memory on alienware GPU 1."}],
        "expected_tool": "get_gpu_memory",
        "expected_args": {"host": "alienware", "gpu_index": 1},
    },
    {
        "id": "simple_restart",
        "messages": [{"role": "user", "content": "Restart alienware-vllm-strong on the alienware host please."}],
        "expected_tool": "restart_service",
        "expected_args": {"host": "alienware", "unit": "alienware-vllm-strong"},
    },
    {
        "id": "logs_with_since",
        "messages": [{"role": "user", "content": "Summarize the last 30 minutes of logs for alienware-vllm-fast on alienware."}],
        "expected_tool": "summarize_logs",
        "expected_args": {"host": "alienware", "unit": "alienware-vllm-fast", "since_minutes": 30},
    },
    {
        "id": "search_default_limit",
        "messages": [{"role": "user", "content": "Find homelab hosts that run vLLM."}],
        "expected_tool": "search_inventory",
        "expected_args": {"query": "vllm"},
    },
    {
        "id": "no_tool_needed",
        "messages": [
            {"role": "user", "content": "Briefly explain what a Mixture-of-Experts model is in two sentences."}
        ],
        "expected_tool": None,
        "expected_args": {},
    },
]


def _arg_match(actual: dict, expected: dict) -> bool:
    if not isinstance(actual, dict):
        return False
    for k, v in expected.items():
        if k not in actual:
            return False
        a = actual[k]
        if isinstance(v, str) and isinstance(a, str):
            if v.lower() not in a.lower():
                return False
        else:
            if a != v:
                return False
    return True


def _eval(case: dict, res: client.ChatResult) -> dict:
    tools = res.tool_calls or []
    names = [(tc.get("function") or {}).get("name") for tc in tools]
    selection_correct = (
        (case["expected_tool"] is None and not tools)
        or (case["expected_tool"] in names)
    )
    args_correct = False
    if case["expected_tool"] and tools:
        for tc in tools:
            fn = tc.get("function") or {}
            if fn.get("name") != case["expected_tool"]:
                continue
            try:
                actual = json.loads(fn.get("arguments") or "{}")
            except Exception:
                actual = {}
            if _arg_match(actual, case["expected_args"]):
                args_correct = True
                break
    elif case["expected_tool"] is None:
        args_correct = not tools
    return {
        "selection_correct": selection_correct,
        "args_correct": args_correct,
        "names": names,
    }


def main() -> int:
    base_url = os.environ["BENCH_BASE_URL"].rstrip("/")
    api_key = os.environ["BENCH_API_KEY"]
    model = os.environ["BENCH_MODEL"]
    model_key = os.environ.get("BENCH_MODEL_KEY", model)
    repeats = int(os.environ.get("BENCH_BFCL_REPEATS", "3"))
    out_dir = Path(os.environ["OUT"])
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = client.ClientConfig(base_url=base_url, api_key=api_key, model=model)
    if not client.health_check(cfg):
        metrics.write_json(out_dir / "summary.json", {"error": "health check failed"})
        return 2

    results_path = out_dir / "results.jsonl"
    results_path.write_text("")
    by_case: dict[str, list[dict]] = {}
    for case in _CASES:
        by_case[case["id"]] = []
        for r in range(repeats):
            res = client.post_chat(
                cfg,
                case["messages"],
                max_tokens=320,
                temperature=0.0,
                tools=_TOOLS,
                tool_choice="auto",
            )
            verdict = _eval(case, res)
            rec = {
                "case_id": case["id"],
                "repeat": r,
                "ok": res.ok,
                "latency_ms": res.latency_ms,
                "expected_tool": case["expected_tool"],
                "tool_names": verdict["names"],
                "selection_correct": verdict["selection_correct"],
                "args_correct": verdict["args_correct"],
                "content_excerpt": (res.content or "")[:300],
            }
            with results_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
            by_case[case["id"]].append(rec)
            print(json.dumps({"bfcl": rec}), flush=True)

    per_case: dict[str, dict] = {}
    for case_id, recs in by_case.items():
        sel = [1.0 if r["selection_correct"] else 0.0 for r in recs]
        arg = [1.0 if r["args_correct"] else 0.0 for r in recs]
        per_case[case_id] = {
            "selection_rate": round(sum(sel) / len(sel), 4),
            "args_rate": round(sum(arg) / len(arg), 4),
            "latency_ms": metrics.summarize([r["latency_ms"] for r in recs if r["ok"]]),
        }

    overall_sel = [1.0 if r["selection_correct"] else 0.0 for recs in by_case.values() for r in recs]
    overall_arg = [1.0 if r["args_correct"] else 0.0 for recs in by_case.values() for r in recs]
    summary = {
        "model_key": model_key,
        "model": model,
        "repeats": repeats,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "per_case": per_case,
        "overall_selection_rate": round(sum(overall_sel) / max(len(overall_sel), 1), 4),
        "overall_args_rate": round(sum(overall_arg) / max(len(overall_arg), 1), 4),
    }
    metrics.write_json(out_dir / "summary.json", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
