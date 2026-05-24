"""A2A-specific exceptions."""


class A2AError(Exception):
    """Base class for agent-to-agent bus errors."""


class A2ANotAllowedError(A2AError):
    """Caller is not permitted to invoke the callee per manifest ACL."""


class A2ARoutingError(A2AError):
    """Callee principal unknown or missing queue_dir in registry."""


class A2ATimeoutError(A2AError):
    """No reply received within the configured timeout."""
