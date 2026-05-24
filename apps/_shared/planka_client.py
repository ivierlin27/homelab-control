"""Shared Planka HTTP auth and request helpers.

Supports (in order):

1. ``PLANKA_API_KEY`` — personal API key (``X-Api-Key`` header, Planka v2+)
2. ``PLANKA_API_TOKEN`` — legacy Bearer access token from ``/api/access-tokens``
3. ``PLANKA_EMAIL_OR_USERNAME`` + ``PLANKA_PASSWORD`` — mint a Bearer token at runtime
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import request


def _is_placeholder(value: str) -> bool:
    return not value or value.strip().lower() in {"replace-me", "changeme", "change-me"}


def planka_access_token() -> str:
    """Return a Bearer token if configured or minted from username/password."""
    token = os.environ.get("PLANKA_API_TOKEN", "").strip()
    if token and not _is_placeholder(token):
        return token

    base_url = os.environ.get("PLANKA_BASE_URL", "").rstrip("/")
    username = os.environ.get("PLANKA_EMAIL_OR_USERNAME", "").strip()
    password = os.environ.get("PLANKA_PASSWORD", "")
    if not base_url or not username or not password or _is_placeholder(password):
        return ""

    body = json.dumps({"emailOrUsername": username, "password": password}).encode("utf-8")
    req = request.Request(
        f"{base_url}/api/access-tokens",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data["item"])


def planka_auth_headers() -> dict[str, str]:
    """Headers for Planka REST calls (includes auth when configured)."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = os.environ.get("PLANKA_API_KEY", "").strip()
    if api_key and not _is_placeholder(api_key):
        headers["X-Api-Key"] = api_key
        return headers

    token = planka_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def planka_auth_configured() -> bool:
    """True when ``planka_request`` can authenticate."""
    if not os.environ.get("PLANKA_BASE_URL", "").strip():
        return False
    headers = planka_auth_headers()
    return "X-Api-Key" in headers or "Authorization" in headers


def planka_request(api_path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    """Call ``{PLANKA_BASE_URL}/api/{api_path}`` with shared auth headers."""
    base_url = os.environ.get("PLANKA_BASE_URL", "").rstrip("/")
    if not base_url:
        raise ValueError("PLANKA_BASE_URL is required")
    headers = planka_auth_headers()
    if "X-Api-Key" not in headers and "Authorization" not in headers:
        raise ValueError(
            "Planka credentials required: set PLANKA_API_KEY, PLANKA_API_TOKEN, "
            "or PLANKA_EMAIL_OR_USERNAME + PLANKA_PASSWORD"
        )
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        f"{base_url}/api/{api_path.lstrip('/')}",
        data=body,
        headers=headers,
        method=method,
    )
    with request.urlopen(req, timeout=20) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}
