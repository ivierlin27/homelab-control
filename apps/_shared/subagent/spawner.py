"""Spawn a sub-agent LLM call with distilled return and full audit transcript."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apps._shared.audit import AuditLog
from apps._shared.rlm.subcall import SubCallInvoker, SubCallResult, SubCallSchemaError

from .errors import SubagentRoleError
from .personas import STANDARD_ROLES, get_persona
from .routing import RoutePolicy, assert_route_allowed, model_for_route, normalize_route


@dataclass
class SubagentResult:
    """Distilled sub-agent output returned to the parent (no full transcript)."""

    role: str
    subagent_correlation_id: str
    parent_correlation_id: str
    route: str
    model: str
    summary: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    confidence: str = "low"
    open_questions: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    structured: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "subagent_correlation_id": self.subagent_correlation_id,
            "parent_correlation_id": self.parent_correlation_id,
            "route": self.route,
            "model": self.model,
            "summary": self.summary,
            "citations": self.citations,
            "confidence": self.confidence,
            "open_questions": self.open_questions,
            "structured": self.structured,
        }


def _invoke_with_persona(
    invoker: SubCallInvoker,
    *,
    persona_system: str,
    intent: str,
    model: str,
    sub_prompt: str,
    context: dict[str, Any],
    skill_id: str | None,
) -> SubCallResult:
    """Run one gateway call with a persona-specific system prompt."""
    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": persona_system},
            {
                "role": "user",
                "content": json.dumps({"sub_prompt": sub_prompt, "context": context}, default=str),
            },
        ],
        "response_format": {"type": "json_object"},
    }
    start_ns = __import__("time").monotonic()
    if invoker.transport is not None:
        response = invoker.transport(intent, model, payload)
    else:
        response = invoker._http_post(payload, intent=intent, skill_id=skill_id)  # noqa: SLF001
    latency_ms = int((__import__("time").monotonic() - start_ns) * 1000)
    text = invoker._extract_text(response)  # noqa: SLF001
    from apps._shared.rlm.subcall import _parse_schema  # noqa: PLC0415

    parsed = _parse_schema(text)
    usage = response.get("usage", {}) if isinstance(response, dict) else {}
    return SubCallResult(
        **parsed,
        raw=response if isinstance(response, dict) else {},
        tokens_in=int(usage.get("prompt_tokens", 0)),
        tokens_out=int(usage.get("completion_tokens", 0)),
        latency_ms=latency_ms,
        route=intent,
        model=model,
    )


def spawn_subagent(
    role: str,
    prompt: str,
    tools: list[str] | None = None,
    *,
    route: str = "local",
    return_schema: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    parent_correlation_id: str,
    audit_path: Path | str,
    route_policy: RoutePolicy | None = None,
    skill_id: str | None = None,
    invoker: SubCallInvoker | None = None,
) -> SubagentResult:
    """Spawn a sub-agent; return distilled JSON only; log full transcript to audit.

    Parameters
    ----------
    role:
        One of ``researcher``, ``planner``, ``tool-runner``, ``verifier``.
    prompt:
        Task instruction for the sub-agent.
    tools:
        Tool names the parent authorizes for this sub-run (metadata only in 0.10).
    route:
        ``local`` (default, resolves to ``homelab-strong-long`` on the gateway),
        or ``cloud-frontier`` when the parent policy allows cloud.
        Legacy ``local-fast`` / ``local-strong`` are accepted and normalized to
        ``local``.
    return_schema:
        Optional JSON-schema-shaped hint merged into context for the sub-agent.
    context:
        Evidence handles and other structured input for the sub-agent.
    parent_correlation_id:
        Parent task / A2A correlation id — every audit row references this.
    audit_path:
        Hash-chained JSONL path (typically the parent agent trust ledger).
    route_policy:
        Parent routing policy; defaults to local only.
    skill_id:
        Optional skill id for local-only gateway enforcement.
    invoker:
        Injectable :class:`SubCallInvoker` (for tests).
    """
    role_key = role.strip().lower().replace("_", "-")
    if role_key not in STANDARD_ROLES:
        raise SubagentRoleError(
            f"unsupported sub-agent role {role!r}; expected one of {sorted(STANDARD_ROLES)}"
        )
    persona = get_persona(role_key)
    normalized_route = assert_route_allowed(route or persona.default_route, route_policy)
    model = model_for_route(normalized_route)
    sub_id = str(uuid.uuid4())
    parent_id = parent_correlation_id.strip()
    if not parent_id:
        raise ValueError("parent_correlation_id is required")

    merged_context: dict[str, Any] = dict(context or {})
    merged_context["tools"] = list(tools or [])
    if return_schema:
        merged_context["return_schema"] = return_schema

    audit = AuditLog(str(audit_path))
    audit.append(
        {
            "event": "subagent_spawn",
            "principal": os.environ.get("AGENT_PRINCIPAL", ""),
            "parent_correlation_id": parent_id,
            "subagent_correlation_id": sub_id,
            "role": role_key,
            "route": normalized_route,
            "model": model,
            "tools": merged_context["tools"],
            "prompt_preview": prompt[:500],
        }
    )

    call = invoker or SubCallInvoker()
    try:
        raw = _invoke_with_persona(
            call,
            persona_system=persona.system_prompt,
            intent=persona.intent,
            model=model,
            sub_prompt=prompt,
            context=merged_context,
            skill_id=skill_id,
        )
    except Exception as exc:
        audit.append(
            {
                "event": "subagent_failed",
                "parent_correlation_id": parent_id,
                "subagent_correlation_id": sub_id,
                "role": role_key,
                "route": normalized_route,
                "error": str(exc),
            }
        )
        raise

    transcript = {
        "gateway_response": raw.raw,
        "distilled": raw.as_dict(),
        "messages": {
            "system": persona.system_prompt,
            "user": {"sub_prompt": prompt, "context": merged_context},
        },
    }
    audit.append(
        {
            "event": "subagent_complete",
            "principal": os.environ.get("AGENT_PRINCIPAL", ""),
            "parent_correlation_id": parent_id,
            "subagent_correlation_id": sub_id,
            "role": role_key,
            "route": normalized_route,
            "model": model,
            "tokens_in": raw.tokens_in,
            "tokens_out": raw.tokens_out,
            "latency_ms": raw.latency_ms,
            "transcript": transcript,
            "distilled": raw.as_dict(),
        }
    )

    structured = raw.as_dict()
    if return_schema:
        structured["return_schema"] = return_schema

    return SubagentResult(
        role=role_key,
        subagent_correlation_id=sub_id,
        parent_correlation_id=parent_id,
        route=normalized_route,
        model=model,
        summary=raw.summary,
        citations=raw.citations,
        confidence=raw.confidence,
        open_questions=raw.open_questions,
        tokens_in=raw.tokens_in,
        tokens_out=raw.tokens_out,
        latency_ms=raw.latency_ms,
        structured=structured,
    )


def default_route_for_role(role: str) -> str:
    return normalize_route(get_persona(role).default_route)
