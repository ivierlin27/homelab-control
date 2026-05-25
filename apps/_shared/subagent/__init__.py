"""Sub-agent spawner (Phase 0.10).

Parents call :func:`spawn_subagent` for a distilled structured result. Full
LLM transcripts are written to the hash-chained audit log under the parent's
``correlation_id``.
"""

from .errors import SubagentError, SubagentRoleError, SubagentRouteError
from .personas import STANDARD_ROLES, get_persona, persona_as_dict
from .routing import RoutePolicy, assert_route_allowed, model_for_route
from .spawner import SubagentResult, default_route_for_role, spawn_subagent
from .wiring import (
    append_subagent_section,
    route_policy_from_manifest_policy,
    subagent_enabled,
    try_researcher_summary,
    try_spawn_subagent,
)

__all__ = [
    "RoutePolicy",
    "STANDARD_ROLES",
    "SubagentError",
    "SubagentResult",
    "SubagentRoleError",
    "SubagentRouteError",
    "assert_route_allowed",
    "default_route_for_role",
    "get_persona",
    "model_for_route",
    "persona_as_dict",
    "spawn_subagent",
    "append_subagent_section",
    "route_policy_from_manifest_policy",
    "subagent_enabled",
    "try_researcher_summary",
    "try_spawn_subagent",
]
