"""Built-in sub-agent personas (Phase 0.10)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STANDARD_ROLES = frozenset({"researcher", "planner", "tool-runner", "verifier"})


@dataclass(frozen=True)
class SubagentPersona:
    """Static persona metadata for a sub-agent role."""

    role: str
    intent: str
    default_route: str
    description: str
    system_prompt: str


def _persona(
    role: str,
    intent: str,
    default_route: str,
    description: str,
    focus: str,
) -> SubagentPersona:
    system = (
        f"You are a {role} sub-agent for a parent homelab agent. {description} "
        f"{focus} "
        "Respond with a single JSON object with keys: "
        "summary (string, the distilled answer for the parent — no chain-of-thought), "
        "citations (array of {{handle, range}} objects referencing evidence handles), "
        "confidence (low|medium|high), "
        "open_questions (array of strings). "
        "Do not include prose outside the JSON."
    )
    return SubagentPersona(
        role=role,
        intent=intent,
        default_route=default_route,
        description=description,
        system_prompt=system,
    )


PERSONAS: dict[str, SubagentPersona] = {
    "researcher": _persona(
        "researcher",
        "summarize",
        "local",
        "Gather and synthesize evidence.",
        "Prefer cited facts from the provided context; flag gaps explicitly.",
    ),
    "planner": _persona(
        "planner",
        "plan",
        "local",
        "Produce a concise, actionable plan.",
        "Break work into ordered steps with clear acceptance checks; avoid tool execution.",
    ),
    "tool-runner": _persona(
        "tool-runner",
        "summarize",
        "local",
        "Execute or interpret tool output on behalf of the parent.",
        "Default for tool-heavy steps: summarize tool results without leaking raw dumps.",
    ),
    "verifier": _persona(
        "verifier",
        "classify",
        "local",
        "Check a claim against supplied evidence.",
        "Return pass/fail style guidance in summary; cite mismatches in citations.",
    ),
}


def get_persona(role: str) -> SubagentPersona:
    key = role.strip().lower().replace("_", "-")
    if key not in PERSONAS:
        raise KeyError(key)
    return PERSONAS[key]


def persona_as_dict(role: str) -> dict[str, Any]:
    p = get_persona(role)
    return {
        "role": p.role,
        "intent": p.intent,
        "default_route": p.default_route,
        "description": p.description,
    }
