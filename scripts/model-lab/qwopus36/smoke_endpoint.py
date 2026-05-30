#!/usr/bin/env python3
"""Smoke-test an OpenAI-compatible lab endpoint (vLLM or llama.cpp)."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CaseResult:
    name: str
    ok: bool = False
    wall_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    content_preview: str = ""
    error: str = ""


@dataclass
class SmokeReport:
    base_url: str
    model: str
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.cases)


def post_chat(base: str, api_key: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{base.rstrip('/')}/chat/completions"
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


def run_case(
    base: str,
    api_key: str,
    model: str,
    name: str,
    body: dict[str, Any],
    *,
    template_kwargs: dict[str, Any] | None = None,
    expect_tool: bool = False,
    expect_content: bool = True,
) -> CaseResult:
    t0 = time.perf_counter()
    try:
        payload = {**body, "model": model}
        if template_kwargs:
            payload["chat_template_kwargs"] = template_kwargs
        out = post_chat(base, api_key, payload)
    except urllib.error.HTTPError as e:
        return CaseResult(
            name=name,
            error=f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:800]}",
        )
    except Exception as e:  # noqa: BLE001
        return CaseResult(name=name, error=str(e))

    wall = time.perf_counter() - t0
    usage = out.get("usage") or {}
    ch0 = (out.get("choices") or [{}])[0]
    msg = ch0.get("message") or {}
    content = (msg.get("content") or "") or ""
    tcalls = msg.get("tool_calls") or []
    ok = True
    if expect_content and not content.strip() and not tcalls:
        ok = False
    if expect_tool and not tcalls:
        ok = False
    return CaseResult(
        name=name,
        ok=ok,
        wall_s=wall,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        tool_calls=len(tcalls),
        content_preview=content[:200].replace("\n", " "),
    )


def main() -> int:
    base = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8002/v1")
    api_key = os.environ.get("SMOKE_API_KEY", "")
    model = os.environ.get("SMOKE_MODEL", "")
    out_dir = Path(os.environ.get("SMOKE_OUT_DIR", "."))
    if not api_key or not model:
        print("Set SMOKE_API_KEY and SMOKE_MODEL", file=sys.stderr)
        return 2

    report = SmokeReport(base_url=base, model=model)
    template_kwargs: dict[str, Any] | None = None
    if os.environ.get("SMOKE_ENABLE_THINKING_FALSE", "1") == "1":
        template_kwargs = {"enable_thinking": False}

    report.cases.append(
        run_case(
            base,
            api_key,
            model,
            "short_ping",
            template_kwargs=template_kwargs,
            body={
                "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
                "max_tokens": 32,
                "temperature": 0,
            },
        )
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_gpu_memory",
                "description": "Read GPU memory on a host.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "gpu_index": {"type": "integer", "default": 0},
                    },
                    "required": ["host"],
                },
            },
        }
    ]
    report.cases.append(
        run_case(
            base,
            api_key,
            model,
            "tool_call",
            template_kwargs=template_kwargs,
            body={
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Call get_gpu_memory with host alienware and gpu_index 1. "
                            "Do not explain."
                        ),
                    }
                ],
                "tools": tools,
                "tool_choice": "auto",
                "max_tokens": 128,
                "temperature": 0,
            },
            expect_tool=True,
            expect_content=False,
        )
    )

    report.cases.append(
        run_case(
            base,
            api_key,
            model,
            "json_contract",
            template_kwargs=template_kwargs,
            body={
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            'Return JSON only: {"route":"strong","escalation_condition":"none",'
                            '"confidence":"high"}'
                        ),
                    }
                ],
                "max_tokens": 64,
                "temperature": 0,
            },
        )
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "smoke.json"
    payload = {
        "base_url": report.base_url,
        "model": report.model,
        "passed": report.passed,
        "cases": [asdict(c) for c in report.cases],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
