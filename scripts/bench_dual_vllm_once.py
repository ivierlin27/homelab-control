#!/usr/bin/env python3
"""One-shot vLLM benchmark for dual-3090 fast + strong routing experiments."""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import time
from pathlib import Path
from urllib import error, request


OUT = Path(os.environ["OUT"])
RESULTS = OUT / "results.jsonl"
SUMMARY = OUT / "summary.json"

MODELS = {
    os.environ.get("BENCH_FAST_KEY", "fast_qwen25_7b_gpu0"): {
        "base_url": os.environ.get("BENCH_FAST_BASE_URL", "http://127.0.0.1:8000/v1"),
        "model": os.environ.get("BENCH_FAST_MODEL", "homelab-fast-vllm"),
        "key": os.environ["VLLM_FAST_API_KEY"],
    },
    os.environ.get("BENCH_STRONG_KEY", "strong_qwen25_14b_awq_gpu1"): {
        "base_url": os.environ.get("BENCH_STRONG_BASE_URL", "http://127.0.0.1:8011/v1"),
        "model": os.environ.get("BENCH_STRONG_MODEL", "homelab-strong-gpu1"),
        "key": os.environ["VLLM_STRONG_API_KEY"],
    },
}


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def ledger_prompt(records: int, needle_every: int = 0) -> str:
    lines = []
    for i in range(records):
        component = ["forgejo", "planka", "litellm", "vllm", "backups", "agents"][i % 6]
        severity = ["info", "warn", "error"][i % 3]
        text = (
            f"2026-05-14T{i % 24:02d}:{i % 60:02d}:00Z {severity} "
            f"component={component} event={i} latency_ms={40 + (i % 700)}"
        )
        if i == 17:
            text += " NEEDLE_ALPHA gpu1 strong route must be isolated from fast route"
        if needle_every and i % needle_every == 0:
            text += " repeated_system_prompt=homelab-maintainer policy gateway routing"
        if i == records // 2:
            text += " NEEDLE_BRAVO model gateway returned 502 while local vLLM was healthy"
        if i == records - 11:
            text += " NEEDLE_CHARLIE final remediation is pin fast to GPU0 and strong to GPU1"
        lines.append(text)
    body = "\n".join(lines)
    return (
        "You are testing long-context recall for a homelab LLM route. "
        "Use only the ledger. Return JSON with keys alpha, bravo, charlie, and recommendation.\n\n"
        f"LEDGER:\n{body}"
    )


TESTS = [
    {
        "id": "short_ops_summary",
        "messages": [
            {"role": "system", "content": "You are a concise homelab operations assistant."},
            {
                "role": "user",
                "content": (
                    "A model gateway has fast and strong routes. Explain when to use each in "
                    "5 bullets, focused on Kevin local homelab agents."
                ),
            },
        ],
        "max_tokens": 220,
        "temperature": 0.2,
    },
    {
        "id": "code_config_review",
        "messages": [
            {"role": "system", "content": "You review infrastructure diffs and find operational bugs."},
            {
                "role": "user",
                "content": (
                    "Review this proposed vLLM service change. Find bugs and give a corrected "
                    "recommendation.\n\nOld: --device nvidia.com/gpu=all -p 8000:8000 "
                    "--model Qwen2.5-7B\nNew: start fast and strong services both with "
                    "--device nvidia.com/gpu=all, fast on 8000, strong on 8001, both "
                    "gpu_memory_utilization=0.92."
                ),
            },
        ],
        "max_tokens": 260,
        "temperature": 0.1,
    },
    {
        "id": "structured_json_contract",
        "messages": [
            {"role": "system", "content": "Return only a JSON object. No markdown."},
            {
                "role": "user",
                "content": (
                    "Classify this request for a router: summarize yesterday agent logs, then "
                    "escalate only if failures mention CUDA OOM or schema parse errors. "
                    "Return keys route, escalation_condition, confidence."
                ),
            },
        ],
        "max_tokens": 180,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    },
    {
        "id": "long_context_8k",
        "messages": [
            {"role": "system", "content": "You are a careful long-context summarizer. Cite exact NEEDLE labels."},
            {"role": "user", "content": ledger_prompt(850, 40)},
        ],
        "max_tokens": 240,
        "temperature": 0.1,
    },
    {
        "id": "long_context_24k",
        "messages": [
            {"role": "system", "content": "You are a careful long-context summarizer. Cite exact NEEDLE labels."},
            {"role": "user", "content": ledger_prompt(2450, 60)},
        ],
        "max_tokens": 260,
        "temperature": 0.1,
    },
]


def post_chat(model_key: str, test: dict) -> dict:
    spec = MODELS[model_key]
    payload = {
        "model": spec["model"],
        "messages": test["messages"],
        "max_tokens": test["max_tokens"],
        "temperature": test["temperature"],
    }
    if "response_format" in test:
        payload["response_format"] = test["response_format"]

    req = request.Request(
        f"{spec['base_url']}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {spec['key']}",
        },
    )
    start = time.monotonic()
    try:
        with request.urlopen(req, timeout=240) as resp:
            raw = resp.read().decode("utf-8")
        latency_ms = int((time.monotonic() - start) * 1000)
        parsed = json.loads(raw)
        choice = (parsed.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        usage = parsed.get("usage") or {}
        completion_tokens = int(usage.get("completion_tokens") or approx_tokens(content))
        prompt_text = json.dumps(test["messages"], ensure_ascii=False)
        return {
            "ok": True,
            "model_key": model_key,
            "test_id": test["id"],
            "latency_ms": latency_ms,
            "prompt_tokens": int(usage.get("prompt_tokens") or approx_tokens(prompt_text)),
            "completion_tokens": completion_tokens,
            "total_tokens": int(usage.get("total_tokens") or 0),
            "decode_tok_s": round(completion_tokens / max(latency_ms / 1000, 0.001), 2),
            "finish_reason": choice.get("finish_reason"),
            "content_excerpt": content[:700],
            "needle_hits": {
                label: label in content
                for label in ["NEEDLE_ALPHA", "NEEDLE_BRAVO", "NEEDLE_CHARLIE"]
            },
        }
    except Exception as exc:  # noqa: BLE001 - benchmark records endpoint failures as data.
        latency_ms = int((time.monotonic() - start) * 1000)
        body = ""
        if isinstance(exc, error.HTTPError):
            body = exc.read().decode("utf-8", errors="replace")[:1000]
        return {
            "ok": False,
            "model_key": model_key,
            "test_id": test["id"],
            "latency_ms": latency_ms,
            "error": repr(exc),
            "error_body": body,
        }


def gpu_snapshot() -> str:
    return subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader",
        ],
        text=True,
    ).strip()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text("")
    records = []
    meta = {
        "kind": "meta",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gpu_before": gpu_snapshot(),
        "models": {
            name: {key: value for key, value in spec.items() if key != "key"}
            for name, spec in MODELS.items()
        },
    }
    with RESULTS.open("a") as fh:
        fh.write(json.dumps(meta) + "\n")

    for model_key in MODELS:
        for test in TESTS:
            rec = post_chat(model_key, test)
            records.append(rec)
            print(json.dumps(rec, sort_keys=True))
            with RESULTS.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")

    concurrent_tests = [
        (os.environ.get("BENCH_FAST_KEY", "fast_qwen25_7b_gpu0"), TESTS[0]),
        (os.environ.get("BENCH_STRONG_KEY", "strong_qwen25_14b_awq_gpu1"), TESTS[1]),
    ]
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(post_chat, model_key, test) for model_key, test in concurrent_tests]
        concurrent_records = [future.result() for future in futs]
    wall_ms = int((time.monotonic() - start) * 1000)
    for rec in concurrent_records:
        rec["test_id"] = "concurrent_" + rec["test_id"]
        records.append(rec)
    concurrent_meta = {
        "kind": "concurrent_meta",
        "wall_ms": wall_ms,
        "gpu_after_concurrent": gpu_snapshot(),
    }
    with RESULTS.open("a") as fh:
        fh.write(json.dumps(concurrent_meta) + "\n")
        for rec in concurrent_records:
            fh.write(json.dumps(rec) + "\n")
    print(json.dumps(concurrent_meta, sort_keys=True))

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gpu_before": meta["gpu_before"],
        "gpu_after": gpu_snapshot(),
        "records": records,
        "concurrent_wall_ms": wall_ms,
    }
    SUMMARY.write_text(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
