#!/usr/bin/env python3
"""One-shot benchmark for a single OpenAI-compatible vLLM endpoint."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from urllib import error, request


OUT = Path(os.environ["OUT"])
RESULTS = OUT / "results.jsonl"
SUMMARY = OUT / "summary.json"

BASE_URL = os.environ["BENCH_BASE_URL"].rstrip("/")
MODEL = os.environ["BENCH_MODEL"]
API_KEY = os.environ["BENCH_API_KEY"]
MODEL_KEY = os.environ.get("BENCH_MODEL_KEY", MODEL)
CHAT_TEMPLATE_KWARGS: dict[str, object] = {}
if os.environ.get("BENCH_ENABLE_THINKING_FALSE") == "1":
    CHAT_TEMPLATE_KWARGS["enable_thinking"] = False


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def ledger_prompt(records: int, needle_every: int = 0) -> str:
    lines = []
    for i in range(records):
        component = ["forgejo", "planka", "litellm", "vllm", "backups", "agents"][i % 6]
        severity = ["info", "warn", "error"][i % 3]
        text = (
            f"2026-05-14T{i % 24:02d}:{i % 60:02d}:00Z {severity} "
            f"component={component} event={i} latency_ms={40 + (i % 700)} "
            f"trace=agent-{i:05d} route={'strong' if i % 7 == 0 else 'fast'}"
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
    return (
        "You are testing long-context recall for a homelab LLM route. "
        "Use only the ledger. Return compact JSON with keys alpha, bravo, charlie, recommendation. "
        "Include the exact NEEDLE labels in the values.\n\nLEDGER:\n"
        + "\n".join(lines)
    )


TESTS = [
    {
        "id": "short_ops_summary",
        "messages": [
            {"role": "system", "content": "You are a concise homelab operations assistant."},
            {
                "role": "user",
                "content": (
                    "A single large local model is serving both fast and strong use cases. "
                    "Give 5 bullets explaining when this is a good idea and when it is not."
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
                    "Review this vLLM plan. Find the operational risks and recommend a better setup.\n\n"
                    "Plan: run a 32B or 27B model with --tensor-parallel-size 2 across both RTX 3090s, "
                    "then also start Qwen2.5-7B on GPU0 for fast tasks. Both should expose OpenAI APIs."
                ),
            },
        ],
        "max_tokens": 300,
        "temperature": 0.1,
    },
    {
        "id": "structured_json_contract",
        "messages": [
            {"role": "system", "content": "Return only a JSON object. No markdown."},
            {
                "role": "user",
                "content": (
                    "Classify this request for a router: summarize yesterday agent logs, then escalate "
                    "only if failures mention CUDA OOM or schema parse errors. Return keys route, "
                    "escalation_condition, confidence."
                ),
            },
        ],
        "max_tokens": 180,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    },
    {
        "id": "tool_call_contract",
        "messages": [
            {"role": "system", "content": "Use tools when appropriate."},
            {"role": "user", "content": "Check GPU memory on alienware."},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_gpu_memory",
                    "description": "Read current GPU memory usage.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string", "description": "host to inspect"},
                        },
                        "required": ["host"],
                    },
                },
            },
        ],
        "tool_choice": "auto",
        "max_tokens": 260,
        "temperature": 0.1,
    },
    {
        "id": "long_context_31k",
        "messages": [
            {"role": "system", "content": "You are a careful long-context summarizer. Cite exact NEEDLE labels."},
            {"role": "user", "content": ledger_prompt(760, 40)},
        ],
        "max_tokens": 260,
        "temperature": 0.1,
    },
    {
        "id": "long_context_60k",
        "messages": [
            {"role": "system", "content": "You are a careful long-context summarizer. Cite exact NEEDLE labels."},
            {"role": "user", "content": ledger_prompt(1480, 60)},
        ],
        "max_tokens": 260,
        "temperature": 0.1,
    },
]


def post_chat(test: dict) -> dict:
    payload = {
        "model": MODEL,
        "messages": test["messages"],
        "max_tokens": test["max_tokens"],
        "temperature": test["temperature"],
    }
    if CHAT_TEMPLATE_KWARGS:
        payload["chat_template_kwargs"] = CHAT_TEMPLATE_KWARGS
    if "response_format" in test:
        payload["response_format"] = test["response_format"]
    if "tools" in test:
        payload["tools"] = test["tools"]
    if "tool_choice" in test:
        payload["tool_choice"] = test["tool_choice"]
    start = time.monotonic()
    try:
        req = request.Request(
            f"{BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
        )
        with request.urlopen(req, timeout=420) as resp:
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
            "model_key": MODEL_KEY,
            "test_id": test["id"],
            "latency_ms": latency_ms,
            "prompt_tokens": int(usage.get("prompt_tokens") or approx_tokens(prompt_text)),
            "completion_tokens": completion_tokens,
            "total_tokens": int(usage.get("total_tokens") or 0),
            "decode_tok_s": round(completion_tokens / max(latency_ms / 1000, 0.001), 2),
            "finish_reason": choice.get("finish_reason"),
            "content_excerpt": content[:900],
            "tool_call_count": len(msg.get("tool_calls") or []),
            "reasoning_excerpt": (msg.get("reasoning") or "")[:300],
            "needle_hits": {
                label: label in content
                for label in ["NEEDLE_ALPHA", "NEEDLE_BRAVO", "NEEDLE_CHARLIE"]
            },
        }
    except Exception as exc:  # noqa: BLE001 - benchmark records failures as data.
        latency_ms = int((time.monotonic() - start) * 1000)
        body = ""
        if isinstance(exc, error.HTTPError):
            body = exc.read().decode("utf-8", errors="replace")[:1200]
        return {
            "ok": False,
            "model_key": MODEL_KEY,
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
        "model_key": MODEL_KEY,
        "base_url": BASE_URL,
        "model": MODEL,
        "gpu_before": gpu_snapshot(),
    }
    with RESULTS.open("a") as fh:
        fh.write(json.dumps(meta) + "\n")

    for test in TESTS:
        rec = post_chat(test)
        records.append(rec)
        print(json.dumps(rec, sort_keys=True))
        with RESULTS.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model_key": MODEL_KEY,
        "model": MODEL,
        "gpu_before": meta["gpu_before"],
        "gpu_after": gpu_snapshot(),
        "records": records,
    }
    SUMMARY.write_text(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
