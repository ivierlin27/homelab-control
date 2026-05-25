"""Production wiring helpers for :func:`spawn_subagent`."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

from apps._shared.gateway_routes import normalize_logical_route

from .routing import RoutePolicy
from .spawner import SubagentResult, spawn_subagent

log = logging.getLogger(__name__)


def subagent_enabled() -> bool:
    """True when gateway is configured and sub-agents are not explicitly disabled."""
    if os.environ.get("HOMELAB_SUBAGENT_DISABLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    if os.environ.get("HOMELAB_SUBAGENT_ENABLE", "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False
    return bool(os.environ.get("MODEL_GATEWAY_BASE_URL", "").strip())


def route_policy_from_manifest_policy(policy: dict[str, Any]) -> RoutePolicy:
    """Build a conservative sub-agent route policy from agent policy YAML."""
    routing = policy.get("routing_policy") or {}
    allow_cloud = bool(routing.get("subagent_allow_cloud", False))
    raw_routes = routing.get("subagent_allowed_routes")
    if raw_routes:
        allowed = frozenset(normalize_logical_route(str(r)) for r in raw_routes)
    else:
        allowed = frozenset({"local"})
    return RoutePolicy(allowed_routes=allowed, allow_cloud=allow_cloud)


def try_spawn_subagent(
    role: str,
    prompt: str,
    *,
    parent_correlation_id: str,
    audit_path: Path | str,
    policy: dict[str, Any] | None = None,
    route: str = "local",
    context: dict[str, Any] | None = None,
    tools: list[str] | None = None,
    dry_run: bool = False,
    skill_id: str | None = None,
) -> SubagentResult | None:
    """Spawn a sub-agent when enabled; return ``None`` on skip or soft failure."""
    if dry_run or not subagent_enabled():
        return None
    corr = parent_correlation_id.strip() or str(uuid.uuid4())
    route_policy = (
        route_policy_from_manifest_policy(policy) if policy else RoutePolicy()
    )
    try:
        return spawn_subagent(
            role,
            prompt,
            tools or [],
            route=route,
            context=context,
            parent_correlation_id=corr,
            audit_path=audit_path,
            route_policy=route_policy,
            skill_id=skill_id,
        )
    except Exception as exc:  # noqa: BLE001 — parent continues without enrichment
        log.warning("subagent %s failed for %s: %s", role, corr, exc)
        return None


def append_subagent_section(base: str, sub: SubagentResult | None, *, heading: str) -> str:
    if sub is None or not sub.summary.strip():
        return base
    return "\n".join(
        [
            base.rstrip(),
            "",
            f"## {heading}",
            "",
            sub.summary.strip(),
        ]
    )


def try_researcher_summary(
    prompt: str,
    *,
    parent_correlation_id: str,
    audit_path: Path | str,
    policy: dict[str, Any] | None = None,
    route: str = "local",
    context: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> SubagentResult | None:
    return try_spawn_subagent(
        "researcher",
        prompt,
        parent_correlation_id=parent_correlation_id,
        audit_path=audit_path,
        policy=policy,
        route=route,
        context=context,
        dry_run=dry_run,
    )
