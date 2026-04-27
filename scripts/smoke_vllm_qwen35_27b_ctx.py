#!/usr/bin/env python3
"""
Smoke tests for the optional Qwen3.5-27B-AWQ vLLM service (port 8002 by default).

Usage on Alienware (after editing vllm-qwen35-27b-ctx.env and starting the unit):

  export VLLM_Q35_CTX_API_KEY="$(grep ^VLLM_Q35_CTX_API_KEY= ~/.config/homelab-control/vllm-qwen35-27b-ctx.env | cut -d= -f2-)"
  python3 scripts/smoke_vllm_qwen35_27b_ctx.py

Optional:
  export VLLM_SMOKE_BASE_URL=http://127.0.0.1:8002/v1
  export VLLM_SMOKE_MODEL=homelab-qwen35-27b-ctx-vllm
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


DEFAULT_BASE = os.environ.get("VLLM_SMOKE_BASE_URL", "http://127.0.0.1:8002/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("VLLM_SMOKE_MODEL", "homelab-qwen35-27b-ctx-vllm")


@dataclass
class RunStats:
    name: str
    wall_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tool_calls: int = 0
    finish_reason: str = ""
    error: str = ""


@dataclass
class Summary:
    runs: list[RunStats] = field(default_factory=list)

    def add(self, r: RunStats) -> None:
        self.runs.append(r)


def post_chat(
    base: str,
    api_key: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    url = f"{base}/chat/completions"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    api_key = os.environ.get("VLLM_Q35_CTX_API_KEY", "")
    if not api_key:
        raise SystemExit("Set VLLM_Q35_CTX_API_KEY (same value as in vllm-qwen35-27b-ctx.env).")

    base = DEFAULT_BASE
    model = DEFAULT_MODEL
    summary = Summary()

    # 1) Short prompt
    t0 = time.perf_counter()
    try:
        out = post_chat(
            base,
            api_key,
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
                "max_tokens": 32,
                "temperature": 0,
            },
        )
    except urllib.error.HTTPError as e:
        summary.add(
            RunStats(
                name="short_ping",
                error=f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}",
            )
        )
        _print_summary(summary)
        raise SystemExit(1) from e

    u = out.get("usage") or {}
    ch0 = (out.get("choices") or [{}])[0]
    fr = (ch0.get("finish_reason") or "") or ""
    summary.add(
        RunStats(
            name="short_ping",
            wall_s=time.perf_counter() - t0,
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
            total_tokens=int(u.get("total_tokens") or 0),
            finish_reason=fr,
        )
    )

    # 2) Long-ish context (approximate token load; tokenizer differs)
    filler = ("The quick brown fox jumps over the lazy dog. " * 1800).strip()
    t1 = time.perf_counter()
    out2 = post_chat(
        base,
        api_key,
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": f"After the following text, reply with one word: OKAY.\n\n{filler}",
                }
            ],
            "max_tokens": 16,
            "temperature": 0,
        },
    )
    u2 = out2.get("usage") or {}
    ch1 = (out2.get("choices") or [{}])[0]
    summary.add(
        RunStats(
            name="long_prefill",
            wall_s=time.perf_counter() - t1,
            prompt_tokens=int(u2.get("prompt_tokens") or 0),
            completion_tokens=int(u2.get("completion_tokens") or 0),
            total_tokens=int(u2.get("total_tokens") or 0),
            finish_reason=(ch1.get("finish_reason") or "") or "",
        )
    )

    # 3) Tool call
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_magic_number",
                "description": "Return a fixed magic number.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }
    ]
    t2 = time.perf_counter()
    out3 = post_chat(
        base,
        api_key,
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "Call get_magic_number once and say nothing else.",
                }
            ],
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": 128,
            "temperature": 0,
        },
    )
    u3 = out3.get("usage") or {}
    ch2 = (out3.get("choices") or [{}])[0]
    msg = ch2.get("message") or {}
    tcalls = msg.get("tool_calls") or []
    summary.add(
        RunStats(
            name="tool_call",
            wall_s=time.perf_counter() - t2,
            prompt_tokens=int(u3.get("prompt_tokens") or 0),
            completion_tokens=int(u3.get("completion_tokens") or 0),
            total_tokens=int(u3.get("total_tokens") or 0),
            tool_calls=len(tcalls),
            finish_reason=(ch2.get("finish_reason") or "") or "",
        )
    )

    _print_summary(summary)


def _print_summary(summary: Summary) -> None:
    print(json.dumps({"base_url": DEFAULT_BASE, "model": DEFAULT_MODEL}, indent=2))
    for r in summary.runs:
        row = {
            "case": r.name,
            "wall_seconds": round(r.wall_s, 3),
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "total_tokens": r.total_tokens,
            "tool_calls": r.tool_calls,
            "finish_reason": r.finish_reason,
        }
        if r.error:
            row["error"] = r.error
        if r.wall_s > 0:
            if r.name == "short_ping" and r.completion_tokens > 0:
                row["output_tokens_per_s_approx"] = round(
                    r.completion_tokens / r.wall_s, 2
                )
            if r.name == "long_prefill" and r.prompt_tokens > 0:
                row["input_tokens_per_s_approx"] = round(
                    r.prompt_tokens / r.wall_s, 2
                )
        print(json.dumps(row, indent=2))


if __name__ == "__main__":
    main()
