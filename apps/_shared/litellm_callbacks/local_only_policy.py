"""Pure decision logic for the gateway's local-only enforcement.

This module has **no LiteLLM dependency** and **no I/O** beyond optionally
reading a precomputed JSON snapshot of skill policy. It is imported by both:

  - the agent-side ``apps/_shared/rlm/subcall.py`` (defense-in-depth: agents
    refuse to dispatch a local-only-skill call to a non-local model BEFORE
    the HTTP request is built);
  - the gateway-side ``apps/_shared/litellm_callbacks/custom_callbacks.py``
    (the canonical security gate: the proxy refuses the request with 403 +
    an audit row regardless of agent behaviour).

Source of truth for ``local_only`` is each skill's SKILL.md front matter.
For the gateway container — which has no access to the repo — a JSON
snapshot is generated at gateway start (see
``generate_skill_policy.py``) and bind-mounted into the container.

This file is intentionally small and fully unit-testable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_LOCAL_PREFIXES = ("homelab-", "local-")
DEFAULT_POLICY_PATH = "/var/log/llm-calls/skill-policy.json"


class LocalOnlyViolation(Exception):
    """Raised when a local-only skill tries to call a non-local model.

    Carries enough structured info to drive an audit row and a clear
    rejection message to the caller (agent process for agent-side enforcement;
    HTTP 403 body for gateway-side enforcement).
    """

    def __init__(
        self,
        *,
        skill_id: str,
        model: str,
        reason: str,
    ) -> None:
        super().__init__(reason)
        self.skill_id = skill_id
        self.model = model
        self.reason = reason

    def as_audit_dict(self) -> dict[str, str]:
        return {
            "kind": "local_only_violation",
            "skill_id": self.skill_id,
            "model": self.model,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SkillPolicy:
    """One skill's gateway-facing policy snapshot."""

    skill_id: str
    local_only: bool
    version: int


@dataclass(frozen=True)
class PolicySnapshot:
    """Immutable in-memory view of all skill policies + the local-route rules."""

    skills: Mapping[str, SkillPolicy]
    local_prefixes: tuple[str, ...]

    def is_local_model(self, model: str | None) -> bool:
        if not isinstance(model, str) or not model:
            return False
        return any(model.startswith(prefix) for prefix in self.local_prefixes)

    def get_skill(self, skill_id: str) -> SkillPolicy | None:
        return self.skills.get(skill_id)


def parse_local_prefixes(env_value: str | None) -> tuple[str, ...]:
    """Parse ``LITELLM_LOCAL_MODEL_PREFIXES`` (comma-separated)."""
    if not env_value:
        return DEFAULT_LOCAL_PREFIXES
    prefixes = tuple(
        p.strip() for p in env_value.split(",") if p.strip()
    )
    return prefixes or DEFAULT_LOCAL_PREFIXES


def load_snapshot(
    path: str | os.PathLike[str] | None = None,
    *,
    local_prefixes: tuple[str, ...] | None = None,
) -> PolicySnapshot:
    """Read a JSON skill-policy snapshot from disk.

    Snapshot schema (the contract with ``generate_skill_policy.py``):

        {
          "schema": 1,
          "generated_at_epoch": 1700000000,
          "skills": {
            "intake-classify": {"local_only": true,  "version": 1},
            "execute-task":    {"local_only": false, "version": 1},
            ...
          }
        }

    A missing or unreadable snapshot is treated as **no policy known** —
    every skill_id lookup returns ``None``, which the caller interprets as
    "no constraint" (fail-open at this layer; the gateway config controls
    which models are even reachable). The trade-off is intentional: the
    snapshot is best-effort metadata, not a security gate by itself. Real
    enforcement comes from the per-call check that pairs the snapshot with
    a real skill_id from the ``x-skill`` header.
    """
    prefixes = local_prefixes or parse_local_prefixes(
        os.environ.get("LITELLM_LOCAL_MODEL_PREFIXES")
    )
    if path is None:
        path = os.environ.get("SKILL_POLICY_SNAPSHOT", DEFAULT_POLICY_PATH)
    p = Path(os.fspath(path))
    if not p.is_file():
        return PolicySnapshot(skills={}, local_prefixes=prefixes)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PolicySnapshot(skills={}, local_prefixes=prefixes)
    if not isinstance(raw, dict):
        return PolicySnapshot(skills={}, local_prefixes=prefixes)
    skills_raw = raw.get("skills") or {}
    skills: dict[str, SkillPolicy] = {}
    if isinstance(skills_raw, dict):
        for skill_id, entry in skills_raw.items():
            if not isinstance(skill_id, str) or not isinstance(entry, dict):
                continue
            skills[skill_id] = SkillPolicy(
                skill_id=skill_id,
                local_only=bool(entry.get("local_only", False)),
                version=int(entry.get("version", 1)),
            )
    return PolicySnapshot(skills=skills, local_prefixes=prefixes)


def check_call(
    *,
    skill_id: str | None,
    model: str | None,
    snapshot: PolicySnapshot,
) -> None:
    """Raise :class:`LocalOnlyViolation` if the call would violate policy.

    Semantics:
      - ``skill_id`` is None or empty: no skill claimed; we cannot enforce
        per-skill policy. **Allow.** Calls without an ``x-skill`` header
        are unattributed (legacy callers, the cost relay, etc.).
      - ``skill_id`` is unknown to the snapshot: we have no policy for this
        skill. **Allow.** This is the fail-open case described in
        :func:`load_snapshot`. Operators can tighten by adding a deny-by-
        default flag in a future iteration.
      - skill found, ``local_only=False``: **Allow** regardless of model.
      - skill found, ``local_only=True``, model is in a local prefix: **Allow.**
      - skill found, ``local_only=True``, model NOT in a local prefix:
        **Reject** with :class:`LocalOnlyViolation`.

    The "fail-open on unknown" behaviour is intentional. The alternative
    (deny unknown skills) couples this gate to perfect snapshot freshness,
    which we don't yet have. The companion mitigation is that ANY skill an
    agent actually executes is in the agent's manifest, which goes through
    boot-time validation (apps._shared.skills.skills_for_agent) — so unknown
    skill_ids reaching here would be a bug, not an attack vector.
    """
    if not skill_id:
        return
    skill = snapshot.get_skill(skill_id)
    if skill is None:
        return
    if not skill.local_only:
        return
    if snapshot.is_local_model(model):
        return
    model_str = model if isinstance(model, str) and model else "<missing>"
    raise LocalOnlyViolation(
        skill_id=skill_id,
        model=model_str,
        reason=(
            f"skill {skill_id!r} is local_only=true but the requested model "
            f"{model_str!r} is not in the local route prefixes "
            f"{list(snapshot.local_prefixes)!r}. Refusing to dispatch."
        ),
    )
