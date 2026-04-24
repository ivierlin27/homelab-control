#!/usr/bin/env python3
"""HTTP bridge for Planka and Forgejo events into the agent queues."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from planka_dispatch import dispatch_payload  # noqa: E402


LISTS_BY_ID = {
    os.environ.get("PLANKA_PLAN_READY_LIST_ID", ""): "Plan Ready",
    os.environ.get("PLANKA_APPROVED_LIST_ID", ""): "Approved To Execute",
    os.environ.get("PLANKA_AUTHOR_REVIEW_LIST_ID", ""): "Author Review Ready",
    os.environ.get("PLANKA_REVIEW_LIST_ID", ""): "Review Agent",
    os.environ.get("PLANKA_NEEDS_HUMAN_LIST_ID", ""): "Needs Human Review",
    os.environ.get("PLANKA_DONE_LIST_ID", ""): "Done",
    os.environ.get("PLANKA_MERGED_LIST_ID", ""): "Merged / Applied",
}


def load_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw or "{}")


def require_dispatch_token(handler: BaseHTTPRequestHandler) -> None:
    expected = os.environ.get("AGENT_DISPATCH_TOKEN", "")
    if not expected:
        return
    provided = handler.headers.get("X-Agent-Dispatch-Token", "")
    if provided != expected:
        raise PermissionError("invalid dispatch token")


def label_names(labels: Any) -> list[str]:
    if not isinstance(labels, list):
        return []
    names: list[str] = []
    for item in labels:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("title")
            if name:
                names.append(str(name))
    return names


def extract_card_item(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("body", payload)
    return body.get("item") or body.get("card") or body.get("data", {}).get("item") or body


def extract_agent_payload(description: str) -> dict[str, Any]:
    """Extract automation metadata from a fenced JSON block in a Planka card."""
    patterns = [
        r"```agent-execution\s*(\{.*?\})\s*```",
        r"```json\s*(\{.*?\})\s*```",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, description, flags=re.DOTALL | re.IGNORECASE):
            return json.loads(match.group(1))
    return {}


def extract_execution(description: str) -> dict[str, Any]:
    """Extract the execution object from a fenced JSON block in a Planka card."""
    data = extract_agent_payload(description)
    if "execution" in data and isinstance(data["execution"], dict):
        return data["execution"]
    return data


def planka_card_url(card_id: str) -> str:
    base_url = os.environ.get("PLANKA_BASE_URL", "").rstrip("/")
    return f"{base_url}/cards/{card_id}" if base_url else card_id


def build_card_export(payload: dict[str, Any]) -> dict[str, Any]:
    item = extract_card_item(payload)
    body = payload.get("body", payload)
    card_id = str(item.get("id") or body.get("cardId") or "")
    dest_list_id = str(
        item.get("listId")
        or item.get("list", {}).get("id", "")
        or body.get("destListId")
        or body.get("listId")
        or body.get("toListId")
        or body.get("newListId")
        or ""
    )
    description = str(item.get("description") or body.get("description") or "")
    agent_payload = extract_agent_payload(description)
    execution = body.get("execution") if isinstance(body.get("execution"), dict) else extract_execution(description)
    labels = label_names(item.get("labels") or body.get("labels"))
    if not labels:
        labels = label_names(agent_payload.get("labels"))
    return {
        "id": card_id,
        "title": item.get("name") or body.get("name") or "Untitled task",
        "summary": body.get("summary") or agent_payload.get("summary", ""),
        "description": description,
        "list_name": body.get("listName") or LISTS_BY_ID.get(dest_list_id, ""),
        "labels": labels or ["safe-update"],
        "url": body.get("url") or planka_card_url(card_id),
        "planka_card": body.get("url") or planka_card_url(card_id),
        "execution": execution,
    }


def dispatch_planka_event(payload: dict[str, Any], *, author_queue: Path, review_queue: Path, artifact_dir: Path) -> dict[str, Any]:
    card = build_card_export(payload)
    if not card["list_name"]:
        return {"ok": True, "handled": "noop", "reason": "unmapped list", "card_id": card["id"]}
    card_path = artifact_dir / f"card-{card['id']}.json"
    card["source_path"] = str(card_path)
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(json.dumps(card, indent=2) + "\n")
    result = dispatch_payload(card, author_queue=author_queue, review_queue=review_queue, artifact_dir=artifact_dir)
    return {"ok": True, "handled": "dispatched", **result}


def planka_access_token() -> str:
    token = os.environ.get("PLANKA_API_TOKEN", "")
    if token:
        return token

    base_url = os.environ.get("PLANKA_BASE_URL", "").rstrip("/")
    username = os.environ.get("PLANKA_EMAIL_OR_USERNAME", "")
    password = os.environ.get("PLANKA_PASSWORD", "")
    if not base_url or not username or not password:
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
    return data["item"]


def planka_request(api_path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    base_url = os.environ.get("PLANKA_BASE_URL", "").rstrip("/")
    token = planka_access_token()
    if not base_url or not token:
        raise ValueError("PLANKA_BASE_URL and PLANKA_API_TOKEN or Planka credentials are required")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        f"{base_url}/api/{api_path.lstrip('/')}",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
        method=method,
    )
    with request.urlopen(req, timeout=20) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def move_planka_card(card_id: str, list_id: str) -> dict[str, Any]:
    if not card_id or not list_id:
        return {"moved": False, "reason": "missing card_id or list_id"}
    return {"moved": True, "card": planka_request(f"cards/{card_id}", method="PATCH", payload={"listId": list_id, "position": 65536})}


def parse_card_id_from_text(value: str) -> str:
    match = re.search(r"planka\.[^/\s]+/cards/([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)
    match = re.search(r"agent/card-(.+?)-(?:execute|plan|review)-", value)
    if match:
        return match.group(1)
    return ""


def next_list_for_merged_pr(pr: dict[str, Any]) -> tuple[str, str]:
    body = pr.get("body", "")
    marker = re.search(r"Next Planka list:\s*`?([^`\n]+)`?", body, flags=re.IGNORECASE)
    if marker:
        name = marker.group(1).strip()
    else:
        labels = {item.get("name", "") for item in pr.get("labels", []) if isinstance(item, dict)}
        name = "Approved To Execute" if "plan-ready" in labels else "Done"

    env_key = {
        "Approved To Execute": "PLANKA_APPROVED_LIST_ID",
        "Done": "PLANKA_DONE_LIST_ID",
        "Merged / Applied": "PLANKA_MERGED_LIST_ID",
    }.get(name, "PLANKA_DONE_LIST_ID")
    return name, os.environ.get(env_key, "")


def handle_forgejo_pr_event(payload: dict[str, Any]) -> dict[str, Any]:
    pr = payload.get("pull_request") or {}
    if not pr.get("merged"):
        return {"ok": True, "handled": "noop", "reason": "pull request is not merged"}
    card_id = parse_card_id_from_text(pr.get("body", "")) or parse_card_id_from_text(pr.get("head", {}).get("ref", ""))
    target_name, target_list_id = next_list_for_merged_pr(pr)
    move_result = move_planka_card(card_id, target_list_id) if card_id else {"moved": False, "reason": "missing card id"}
    return {"ok": True, "handled": "merged-pr", "card_id": card_id, "target_list": target_name, **move_result}


class DispatchHandler(BaseHTTPRequestHandler):
    author_queue: Path
    review_queue: Path
    artifact_dir: Path

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        rendered = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(rendered)))
        self.end_headers()
        self.wfile.write(rendered)

    def do_POST(self) -> None:  # noqa: N802
        try:
            require_dispatch_token(self)
            payload = load_json_body(self)
            if self.path in {"/planka/card-moved", "/planka-control-plane"}:
                result = dispatch_planka_event(
                    payload,
                    author_queue=self.author_queue,
                    review_queue=self.review_queue,
                    artifact_dir=self.artifact_dir,
                )
            elif self.path in {"/forgejo/pull-request", "/forgejo/pr-event"}:
                result = handle_forgejo_pr_event(payload)
            else:
                self._json_response(404, {"ok": False, "error": f"unknown path: {self.path}"})
                return
            self._json_response(200, result)
        except PermissionError as exc:
            self._json_response(401, {"ok": False, "error": str(exc)})
        except (ValueError, KeyError, json.JSONDecodeError, error.HTTPError) as exc:
            self._json_response(400, {"ok": False, "error": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("AGENT_DISPATCH_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENT_DISPATCH_PORT", "8765")))
    parser.add_argument("--author-queue", required=True)
    parser.add_argument("--review-queue", required=True)
    parser.add_argument("--artifact-dir", required=True)
    args = parser.parse_args()

    DispatchHandler.author_queue = Path(args.author_queue).expanduser()
    DispatchHandler.review_queue = Path(args.review_queue).expanduser()
    DispatchHandler.artifact_dir = Path(args.artifact_dir).expanduser()
    server = ThreadingHTTPServer((args.host, args.port), DispatchHandler)
    print(f"agent event dispatcher listening on {args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
