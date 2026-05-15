"""Shared prompt fixtures used by multiple runners.

The fixtures are intentionally deterministic and self-contained so multiple
runs in different environments produce identical inputs and let us compare
results across days/engines.
"""

from __future__ import annotations

from typing import Any


def ledger_prompt(records: int, needle_every: int = 0) -> str:
    """Synthetic homelab log ledger with embedded NEEDLE markers.

    Produces a prompt of approximately ``records * 40`` tokens. Three NEEDLE
    labels are placed at distinct depths to test single-needle recall as a
    fast regression guard alongside RULER.
    """
    lines: list[str] = []
    components = ["forgejo", "planka", "litellm", "vllm", "backups", "agents"]
    severities = ["info", "warn", "error"]
    for i in range(records):
        component = components[i % len(components)]
        severity = severities[i % len(severities)]
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


GPU_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_gpu_memory",
        "description": "Read current GPU memory usage on a homelab host.",
        "parameters": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "host to inspect"},
                "gpu_index": {"type": "integer", "description": "which GPU (0,1)", "default": 0},
            },
            "required": ["host"],
        },
    },
}


JSON_ROUTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["fast", "strong", "conditional_escalation"]},
        "escalation_condition": {"type": "string"},
        # Accept either a stringified or numeric confidence; production routers
        # see both shapes in practice. Schema-pass should reflect contract,
        # not punish models for picking one of two reasonable encodings.
        "confidence": {"oneOf": [{"type": "string"}, {"type": "number"}]},
    },
    "required": ["route", "escalation_condition", "confidence"],
    "additionalProperties": False,
}


def micro_tests() -> list[dict[str, Any]]:
    """The shared micro-benchmark battery.

    Each test is a self-describing dict consumed by ``runners.micro``.
    Keep it small and fast; long-context tests are expensive but provide
    the strongest signal for our use cases.
    """
    return [
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
            "schema": JSON_ROUTER_SCHEMA,
        },
        {
            "id": "tool_call_contract",
            "messages": [
                {"role": "system", "content": "Use tools when appropriate."},
                {"role": "user", "content": "Check GPU memory on alienware gpu 1."},
            ],
            "tools": [GPU_TOOL],
            "tool_choice": "auto",
            "max_tokens": 260,
            "temperature": 0.1,
            "tool_call_expected": "get_gpu_memory",
        },
        {
            "id": "long_context_31k",
            "messages": [
                {"role": "system", "content": "You are a careful long-context summarizer. Cite exact NEEDLE labels."},
                {"role": "user", "content": ledger_prompt(760, 40)},
            ],
            "max_tokens": 260,
            "temperature": 0.1,
            "needles": ["NEEDLE_ALPHA", "NEEDLE_BRAVO", "NEEDLE_CHARLIE"],
        },
        {
            "id": "long_context_60k",
            "messages": [
                {"role": "system", "content": "You are a careful long-context summarizer. Cite exact NEEDLE labels."},
                {"role": "user", "content": ledger_prompt(1480, 60)},
            ],
            "max_tokens": 260,
            "temperature": 0.1,
            "needles": ["NEEDLE_ALPHA", "NEEDLE_BRAVO", "NEEDLE_CHARLIE"],
        },
    ]
