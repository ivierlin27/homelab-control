"""Route policy checks for sub-agent LLM calls."""

from __future__ import annotations

from dataclasses import dataclass, field

from apps._shared.gateway_routes import (
    CLOUD_ROUTE,
    LOCAL_ROUTE,
    gateway_model_for_route,
    normalize_logical_route,
)

from .errors import SubagentRouteError

# Accepted at API boundaries (legacy tier names normalize to ``local``).
LOCAL_ROUTE_ALIASES = frozenset({LOCAL_ROUTE, "local-fast", "local-strong"})
CLOUD_ROUTES = frozenset({CLOUD_ROUTE})


@dataclass
class RoutePolicy:
    """What routes a parent agent may delegate to sub-agents."""

    allowed_routes: frozenset[str] = field(
        default_factory=lambda: frozenset({LOCAL_ROUTE})
    )
    allow_cloud: bool = False

    @classmethod
    def from_manifest_routing(cls, routing: dict | None) -> RoutePolicy:
        """Build policy from a manifest ``routing`` block when present."""
        if not routing:
            return cls()
        allowed = routing.get("subagent_allowed_routes") or routing.get("allowed_routes")
        if allowed:
            routes = frozenset(normalize_logical_route(str(r)) for r in allowed)
        else:
            routes = frozenset({LOCAL_ROUTE})
        allow_cloud = bool(
            routing.get("allow_cloud_subagent", routing.get("allow_cloud", False))
        )
        return cls(allowed_routes=routes, allow_cloud=allow_cloud)


def normalize_route(route: str) -> str:
    return normalize_logical_route(route)


def _policy_allows_local(policy: RoutePolicy) -> bool:
    return any(normalize_logical_route(r) == LOCAL_ROUTE for r in policy.allowed_routes)


def assert_route_allowed(route: str, policy: RoutePolicy | None) -> str:
    """Return canonical logical route or raise :class:`SubagentRouteError`."""
    normalized = normalize_logical_route(route)
    pol = policy or RoutePolicy()

    if normalized == CLOUD_ROUTE:
        if not pol.allow_cloud and CLOUD_ROUTE not in {
            normalize_logical_route(r) for r in pol.allowed_routes
        }:
            raise SubagentRouteError(
                f"route {normalized!r} requires allow_cloud on the parent routing policy"
            )
    elif normalized == LOCAL_ROUTE:
        if not _policy_allows_local(pol):
            raise SubagentRouteError(
                f"route {normalized!r} not in parent allowed routes: "
                f"{sorted(pol.allowed_routes)}"
            )
    else:
        raise SubagentRouteError(f"unknown route {normalized!r}")

    try:
        gateway_model_for_route(normalized)
    except ValueError as exc:
        raise SubagentRouteError(str(exc)) from exc
    return normalized


def model_for_route(route: str) -> str:
    """LiteLLM model alias (``homelab-strong-long`` for all local routes today)."""
    return gateway_model_for_route(route)
