"""Capability Registry loader and validator.

The registry is the single source of truth for which agents exist on this
homelab and what they may do. See `config/agents/README.md` and
`config/agents/registry.schema.yaml` for the contract this module enforces.
"""

from .loader import (
    AGENT_PRINCIPAL_RE,
    DEFAULT_REGISTRY_PATH,
    AgentManifest,
    Registry,
    RegistryError,
    load_registry,
)

__all__ = [
    "AGENT_PRINCIPAL_RE",
    "DEFAULT_REGISTRY_PATH",
    "AgentManifest",
    "Registry",
    "RegistryError",
    "load_registry",
]
