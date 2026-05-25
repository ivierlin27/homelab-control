"""Logical routes → LiteLLM model aliases for the trimmed Alienware fleet.

As of 2026-05, only ``alienware-vllm-strong-long`` (Qwen on both 3090s) runs.
``config/model-gateway/litellm.config.yaml`` registers ``homelab-strong-long`` as
the canonical alias; ``homelab-fast`` and ``homelab-strong`` are compatibility
aliases to the same upstream. Callers should use logical route ``local`` and
let this module resolve the gateway model name.
"""

from __future__ import annotations

import os

# Canonical LiteLLM model_name (see config/model-gateway/litellm.config.yaml).
DEFAULT_LOCAL_MODEL = (
    os.environ.get("HOMELAB_LOCAL_MODEL", "homelab-strong-long").strip()
    or "homelab-strong-long"
)

# Legacy logical / tier names still accepted at API boundaries.
_LEGACY_LOCAL_ROUTES = frozenset({"local-fast", "local-strong"})

CLOUD_ROUTE = "cloud-frontier"
LOCAL_ROUTE = "local"
CANONICAL_LOCAL_ROUTES = frozenset({LOCAL_ROUTE})


def normalize_logical_route(route: str) -> str:
    """Map legacy tier names to the single local route."""
    key = route.strip().lower().replace("_", "-")
    if key in _LEGACY_LOCAL_ROUTES or key == LOCAL_ROUTE:
        return LOCAL_ROUTE
    return key


def gateway_model_for_route(route: str) -> str:
    """Resolve a logical route to the LiteLLM ``model`` field for chat completions."""
    logical = normalize_logical_route(route)
    if logical == LOCAL_ROUTE:
        return DEFAULT_LOCAL_MODEL
    if logical == CLOUD_ROUTE:
        return CLOUD_ROUTE
    raise ValueError(f"unknown logical route {route!r}")


def is_local_route(route: str) -> bool:
    return normalize_logical_route(route) == LOCAL_ROUTE
