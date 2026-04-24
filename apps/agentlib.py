#!/usr/bin/env python3
"""Shared helpers for homelab queue workers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib import error, parse, request


def slugify(value: str, *, default: str = "task") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or default


def repo_name_from_path(path: Path) -> str:
    return path.resolve().name


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, indent=2) + "\n")


def parse_pr_url(pr_url: str) -> dict[str, Any]:
    parsed = parse.urlparse(pr_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[2] != "pulls":
        raise ValueError(f"unsupported PR URL: {pr_url}")
    owner, repo, _, number = parts[:4]
    return {
        "base_url": f"{parsed.scheme}://{parsed.netloc}",
        "owner": owner,
        "repo": repo,
        "number": int(number),
    }


def _api_url(base_url: str, api_path: str) -> str:
    return f"{base_url.rstrip('/')}/api/v1/{api_path.lstrip('/')}"


def forgejo_request(
    base_url: str,
    api_path: str,
    *,
    token: str = "",
    method: str = "GET",
    payload: dict[str, Any] | list[Any] | None = None,
    timeout: int = 30,
) -> Any:
    headers = {"Accept": "application/json"}
    body: bytes | None = None
    if token:
        headers["Authorization"] = f"token {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    req = request.Request(
        _api_url(base_url, api_path),
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            if not raw.strip():
                return {}
            return json.loads(raw)
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"forgejo api {method} {api_path} failed: http {exc.code}: {response_body}") from exc


def extract_links(text: str) -> list[str]:
    return re.findall(r"https?://\S+", text)
