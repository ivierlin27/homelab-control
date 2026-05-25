"""Errors raised by the sub-agent spawner."""


class SubagentError(Exception):
    """Base error for sub-agent operations."""


class SubagentRoleError(SubagentError):
    """Unknown or unsupported sub-agent role."""


class SubagentRouteError(SubagentError):
    """Route not permitted by the parent agent's routing policy."""
