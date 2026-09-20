"""HTTP client for the move command center API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def base_url() -> str:
    return os.environ.get("MOVE_COMMAND_CENTER_URL", "http://192.168.1.69:8780").rstrip("/")


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    url = f"{base_url()}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"move API {method} {path} failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"move API unreachable at {url}: {exc}") from exc


def get_status() -> dict[str, Any]:
    return _request("GET", "/api/v1/status")


def get_today() -> dict[str, Any]:
    return _request("GET", "/api/v1/today")


def create_task(
    *,
    title: str,
    workstream: str = "move",
    due_date: str | None = None,
    priority: str = "normal",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": title,
        "workstream": workstream,
        "priority": priority,
    }
    if due_date:
        payload["due_date"] = due_date
    return _request("POST", "/api/v1/tasks", payload)


def upsert_property(
    *,
    address: str,
    listing_url: str | None = None,
    sheet_url: str | None = None,
    forum_url: str | None = None,
    status: str = "discovered",
    price: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"address": address, "status": status}
    if listing_url:
        payload["listing_url"] = listing_url
    if sheet_url:
        payload["sheet_url"] = sheet_url
    if forum_url:
        payload["forum_url"] = forum_url
    if price is not None:
        payload["price"] = price
    return _request("POST", "/api/v1/properties", payload)


def get_house_status() -> dict[str, Any]:
    return _request("GET", "/api/v1/house/status")


def list_properties() -> list[dict[str, Any]]:
    return _request("GET", "/api/v1/properties")


def get_digest() -> dict[str, Any]:
    return _request("GET", "/api/v1/digest")


def summarize_property(property_id: str, *, thread_text: str, sheet_notes: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"thread_text": thread_text}
    if sheet_notes:
        body["sheet_notes"] = sheet_notes
    return _request("POST", f"/api/v1/properties/{property_id}/summarize", body)


def get_property(property_id: str) -> dict[str, Any]:
    return _request("GET", f"/api/v1/properties/{property_id}")


def patch_property(property_id: str, **fields: Any) -> dict[str, Any]:
    return _request("PATCH", f"/api/v1/properties/{property_id}", fields)


def intake_from_listing(
    *,
    listing_url: str,
    mls: str | None = None,
    notes: str | None = None,
    skip_sheet: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {"listing_url": listing_url, "skip_sheet": skip_sheet}
    if mls:
        body["mls"] = mls
    if notes:
        body["notes"] = notes
    return _request("POST", "/api/v1/house/intake", body)


def set_status_by_forum(*, forum_url: str, status: str) -> dict[str, Any]:
    return _request("POST", "/api/v1/house/status-by-forum", {"forum_url": forum_url, "status": status})


def compare_properties(
    *,
    property_ids: list[str] | None = None,
    addresses: list[str] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if property_ids:
        body["property_ids"] = property_ids
    if addresses:
        body["addresses"] = addresses
    return _request("POST", "/api/v1/house/compare", body)
