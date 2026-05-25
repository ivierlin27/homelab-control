"""Route policy checks for sub-agent LLM calls."""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import SubagentRouteError

LOCAL_ROUTES = frozenset({"local-fast", "local-strong"})
CLOUD_ROUTES = frozenset({"cloud-frontier"})

# Map logical route names to LiteLLM model aliases (see config/model-gateway).
ROUTE_TO_MODEL: dict[str, str] = {
    "local-fast": "homelab-fast",
    "local-strong": "homelab-strong",
    "cloud-frontier": "cloud-frontier",
}


@dataclass
class RoutePolicy:
    """What routes a parent agent may delegate to sub-agents."""

    allowed_routes: frozenset[str] = field(
        default_factory=lambda: frozenset({"local-fast"})
    )
    allow_cloud: bool = False

    @classmethod
    def from_manifest_routing(cls, routing: dict | None) -> RoutePolicy:
        """Build policy from a manifest ``routing`` block when present."""
        if not routing:
            return cls()
        allowed = routing.get("subagent_allowed_routes") or routing.get("allowed_routes")
        if allowed:
            routes = frozenset(str(r).strip().lower().replace("_", "-") for r in allowed)
        else:
            routes = frozenset({"local-fast"})
        allow_cloud = bool(routing.get("allow_cloud_subagent", routing.get("allow_cloud", False)))
        return cls(allowed_routes=routes, allow_cloud=allow_cloud)


def normalize_route(route: str) -> str:
    return route.strip().lower().replace("_", "-")


def assert_route_allowed(route: str, policy: RoutePolicy | None) -> str:
    """Return normalized route or raise :class:`SubagentRouteError`."""
    normalized = normalize_route(route)
    pol = policy or RoutePolicy()
    if normalized in CLOUD_ROUTES:
        if not pol.allow_cloud and normalized not in pol.allowed_routes:
            raise SubagentRouteError(
                f"route {normalized!r} requires allow_cloud on the parent routing policy"
            )
    if normalized not in pol.allowed_routes and normalized not in (
        CLOUD_ROUTES if pol.allow_cloud else frozenset()
    ):
        raise SubagentRouteError(
            f"route {normalized!r} not in parent allowed routes: {sorted(pol.allowed_routes)}"
        )
    if normalized not in ROUTE_TO_MODEL:
        raise SubagentRouteError(f"unknown route {normalized!r}")
    return normalized


def model_for_route(route: str) -> str:
    return ROUTE_TO_MODEL[normalize_route(route)]
