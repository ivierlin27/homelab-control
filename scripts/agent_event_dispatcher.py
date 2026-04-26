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
    os.environ.get("PLANKA_IN_PROGRESS_LIST_ID", ""): "In Progress",
    os.environ.get("PLANKA_NEEDS_HUMAN_LIST_ID", ""): "Needs Human Review",
    os.environ.get("PLANKA_DONE_LIST_ID", ""): "Done",
}

LABEL_COLORS = {
    "review:plan": "antique-blue",
    "review:pr": "lagoon-blue",
    "review:changes-requested": "pumpkin-orange",
    "state:author-working": "morning-sky",
    "state:pr-open": "summer-sky",
    "state:review-agent": "lilac-eyes",
    "state:ready-to-merge": "bright-moss",
    "type:docs": "fresh-salad",
    "type:deployment": "orange-peel",
    "type:research": "turquoise-sea",
}

STATE_LABELS = {
    "review:plan",
    "review:pr",
    "review:changes-requested",
    "state:author-working",
    "state:pr-open",
    "state:review-agent",
    "state:ready-to-merge",
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


def slugify(value: str, *, default: str = "task") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or default


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


def enrich_card_from_planka(card: dict[str, Any]) -> dict[str, Any]:
    card_id = card.get("id", "")
    if not card_id:
        return card
    try:
        payload = planka_request(f"cards/{card_id}")
    except Exception:
        return card
    item = payload.get("item", {})
    comments = [
        str(comment.get("text", ""))
        for comment in payload.get("included", {}).get("comments", [])
        if comment.get("text")
    ]
    enriched = {**card}
    enriched["description"] = item.get("description") or card.get("description", "")
    enriched["title"] = item.get("name") or card.get("title", "")
    enriched["comments"] = comments
    return enriched


def dispatch_planka_event(payload: dict[str, Any], *, author_queue: Path, review_queue: Path, artifact_dir: Path) -> dict[str, Any]:
    card = build_card_export(payload)
    if not card["list_name"]:
        return {"ok": True, "handled": "noop", "reason": "unmapped list", "card_id": card["id"]}
    if card["list_name"] == "Plan Ready":
        return handle_plan_ready_card(card)
    if card["list_name"] == "Approved To Execute" and not execution_is_actionable(card.get("execution", {})):
        return handle_missing_execution_details(card)
    if isinstance(card.get("execution"), dict):
        port = os.environ.get("AGENT_DISPATCH_PORT", "8765")
        card["execution"].setdefault("lifecycle_callback_url", f"http://127.0.0.1:{port}/agent/lifecycle")
        card["execution"].setdefault("lifecycle_callback_token", os.environ.get("AGENT_DISPATCH_TOKEN", ""))
        card["execution"].setdefault(
            "review_queue_dir",
            str(Path.home() / ".local/state/homelab-control/agent-review"),
        )
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


def update_planka_card_description(card_id: str, description: str) -> dict[str, Any]:
    if not card_id:
        return {"updated": False, "reason": "missing card id"}
    return {
        "updated": True,
        "card": planka_request(f"cards/{card_id}", method="PATCH", payload={"description": description}),
    }


def add_pr_link_to_card(card_id: str, pr_url: str) -> dict[str, Any]:
    if not card_id or not pr_url:
        return {"pr_link_updated": False, "reason": "missing card id or pr url"}
    payload = planka_request(f"cards/{card_id}")
    item = payload.get("item", {})
    description = item.get("description") or ""
    if pr_url in description:
        return {"pr_link_updated": False, "reason": "pr url already present"}
    marker = "## Pull Request"
    if marker in description:
        updated = re.sub(
            r"## Pull Request\n\n.*?(?=\n## |\Z)",
            f"## Pull Request\n\n- {pr_url}\n",
            description,
            flags=re.DOTALL,
        )
    else:
        updated = description.rstrip() + f"\n\n## Pull Request\n\n- {pr_url}\n"
    update_planka_card_description(card_id, updated)
    return {"pr_link_updated": True, "pr_url": pr_url}


def board_id() -> str:
    return os.environ.get("PLANKA_BOARD_ID", "")


def board_labels() -> dict[str, str]:
    board = board_id()
    if not board:
        return {}
    payload = planka_request(f"boards/{board}")
    labels = payload.get("included", {}).get("labels", [])
    return {label["name"]: label["id"] for label in labels if label.get("name")}


def ensure_label(label_name: str) -> str | None:
    labels = board_labels()
    if label_name in labels:
        return labels[label_name]
    board = board_id()
    if not board:
        return None
    payload = {
        "name": label_name,
        "color": LABEL_COLORS.get(label_name, "dark-granite"),
        "position": 65536 * (len(labels) + 1),
    }
    created = planka_request(f"boards/{board}/labels", method="POST", payload=payload)
    return created.get("item", {}).get("id")


def card_label_ids(card_id: str) -> set[str]:
    payload = planka_request(f"cards/{card_id}")
    return {str(item.get("labelId")) for item in payload.get("included", {}).get("cardLabels", []) if item.get("labelId")}


def add_card_label(card_id: str, label_name: str) -> None:
    label_id = ensure_label(label_name)
    if not label_id:
        return
    if label_id in card_label_ids(card_id):
        return
    try:
        planka_request(f"cards/{card_id}/card-labels", method="POST", payload={"labelId": label_id})
    except error.HTTPError as exc:
        if exc.code != 409:
            raise


def remove_card_label(card_id: str, label_name: str) -> None:
    label_id = board_labels().get(label_name)
    if not label_id:
        return
    if label_id not in card_label_ids(card_id):
        return
    planka_request(f"cards/{card_id}/card-labels/labelId:{label_id}", method="DELETE")


def set_card_state_labels(card_id: str, labels: list[str]) -> dict[str, Any]:
    if not card_id:
        return {"labels_updated": False, "reason": "missing card id"}
    for label in STATE_LABELS:
        remove_card_label(card_id, label)
    for label in labels:
        add_card_label(card_id, label)
    return {"labels_updated": True, "labels": labels}


def strip_generated_plan_sections(description: str) -> str:
    markers = ["## Agent Plan Draft", "## Execution Details Needed"]
    cut = len(description)
    for marker in markers:
        idx = description.find(marker)
        if idx >= 0:
            cut = min(cut, idx)
    return description[:cut].rstrip()


def fallback_execution_payload(card: dict[str, Any]) -> dict[str, Any]:
    title = card.get("title", "Untitled task")
    description = strip_generated_plan_sections(card.get("description", ""))
    comments = "\n".join(f"- {comment}" for comment in card.get("comments", [])) or "- No comments."
    slug = slugify(title)

    if "youtube" in title.lower():
        path = "docs/LINK_INTAKE_WORKFLOW.md"
        kind = "YouTube link intake"
    elif "searx" in title.lower():
        path = "docs/SEARXNG_AGENT_SEARCH.md"
        kind = "SearXNG agent search"
    else:
        path = f"docs/proposals/{slug}.md"
        kind = "agent proposal"

    content = "\n".join(
        [
            f"# {title}",
            "",
            "## Goal",
            "",
            description or "No card description was provided.",
            "",
            "## Human Feedback",
            "",
            comments,
            "",
            "## Executable First Slice",
            "",
            f"Create or update this {kind} document as the first reviewable slice.",
            "After this lands, the next card should implement the concrete workflow, service, or code changes described here.",
            "",
            "## Review Gate",
            "",
            "Do not write durable memory or deploy runtime changes until the proposal has been reviewed.",
            "",
        ]
    )
    return {
        "summary": f"Create a concrete {kind} proposal for review.",
        "labels": ["docs-only", "type:docs"],
        "execution": {
            "allowed_paths": ["docs"],
            "checks": ["git diff --check"],
            "summary_lines": [f"Create a concrete {kind} proposal for human review."],
            "next_planka_list": "Approved To Execute",
            "operations": {"write_files": [{"path": path, "content": content}]},
        },
    }


def call_planning_model(card: dict[str, Any]) -> dict[str, Any] | None:
    base_url = os.environ.get("MODEL_GATEWAY_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("MODEL_GATEWAY_API_KEY", "")
    model = os.environ.get("AGENT_PLANNING_MODEL", "homelab-strong")
    if not base_url or not api_key:
        return None

    description = strip_generated_plan_sections(card.get("description", ""))
    comments = "\n".join(f"- {comment}" for comment in card.get("comments", [])) or "- No comments."
    prompt = {
        "role": "user",
        "content": (
            "Draft a concrete, reviewable plan for a homelab-control Planka card. "
            "Return ONLY JSON with keys summary, labels, and execution. "
            "The execution object must be suitable for the author agent and include allowed_paths, checks, summary_lines, and operations. "
            "operations must contain at least one of replacements, append_text, write_files, delete_files. "
            "Prefer a small documentation/proposal first slice unless the card specifies exact code or workflow file edits. "
            "Use only repository-relative paths. Do not include secrets.\n\n"
            f"Title: {card.get('title', '')}\n\nDescription:\n{description}\n\nComments:\n{comments}\n"
        ),
    }
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "system", "content": "You are a careful senior engineer planning safe GitOps changes."}, prompt],
            "temperature": 0.2,
        }
    ).encode("utf-8")
    req = request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        payload = json.loads(content)
        if execution_is_actionable(payload.get("execution", {})):
            return payload
    except Exception:
        return None
    return None


def build_plan_draft(card: dict[str, Any]) -> str:
    description = card.get("description", "").rstrip()
    original = strip_generated_plan_sections(description)
    plan_payload = call_planning_model(card) or fallback_execution_payload(card)

    title = card.get("title", "Untitled task")
    summary = plan_payload.get("summary") or card.get("summary") or "No separate summary was provided."
    labels = plan_payload.get("labels") or ["safe-update"]
    execution = plan_payload.get("execution", {})
    execution.setdefault("next_planka_list", "Approved To Execute")
    prefix = f"{original}\n\n" if original else ""
    return prefix + "\n".join(
        [
            "## Agent Plan Draft",
            "",
            "_Drafted automatically from the current card. Review and edit this before moving the card to `Approved To Execute`._",
            "",
            "### Goal",
            "",
            f"- {title}",
            f"- Summary: {summary}",
            "",
            "### Proposed Approach",
            "",
            *[f"- {line}" for line in execution.get("summary_lines", [])],
            "",
            "### Human Review Checklist",
            "",
            "- [ ] The goal is correct.",
            "- [ ] The risk level and labels are correct.",
            "- [ ] The proposed first slice is small enough.",
            "- [ ] The `agent-execution` block below matches what should happen next.",
            "",
            "### Proposed Labels",
            "",
            *[f"- `{label}`" for label in labels],
            "",
            "### Executable Proposal",
            "",
            "```agent-execution",
            json.dumps({"summary": summary, "labels": labels, "execution": execution}, indent=2),
            "```",
            "",
            "### Next Step",
            "",
            "If this looks right, move this card to `Approved To Execute`.",
        ]
    )


def execution_is_actionable(execution: Any) -> bool:
    if not isinstance(execution, dict):
        return False
    operations = execution.get("operations")
    if not isinstance(operations, dict):
        return False
    for key in ("replacements", "append_text", "write_files", "delete_files"):
        if operations.get(key):
            return True
    return False


def handle_plan_ready_card(card: dict[str, Any]) -> dict[str, Any]:
    card = enrich_card_from_planka(card)
    card_id = card.get("id", "")
    description = build_plan_draft(card)
    update_result = update_planka_card_description(card_id, description)
    label_result = set_card_state_labels(card_id, ["review:plan"])
    move_result = move_planka_card(card_id, list_id_for_name("Needs Human Review"))
    return {
        "ok": True,
        "handled": "plan-ready",
        "card_id": card_id,
        "target_list": "Needs Human Review",
        **update_result,
        **label_result,
        **move_result,
    }


def handle_missing_execution_details(card: dict[str, Any]) -> dict[str, Any]:
    card_id = card.get("id", "")
    description = card.get("description", "").rstrip()
    note = (
        "\n\n## Execution Details Needed\n\n"
        "This card was moved to `Approved To Execute`, but it does not yet contain an actionable "
        "`agent-execution` block with file operations. Add the concrete operations to perform, "
        "or move the card back to `Plan Ready` for another planning pass.\n"
    )
    if "## Execution Details Needed" not in description:
        update_result = update_planka_card_description(card_id, description + note)
    else:
        update_result = {"updated": False, "reason": "execution details note already present"}
    label_result = set_card_state_labels(card_id, ["review:changes-requested"])
    move_result = move_planka_card(card_id, list_id_for_name("Needs Human Review"))
    return {
        "ok": True,
        "handled": "missing-execution-details",
        "card_id": card_id,
        "target_list": "Needs Human Review",
        **update_result,
        **label_result,
        **move_result,
    }


def list_id_for_name(name: str) -> str:
    env_key = {
        "Approved To Execute": "PLANKA_APPROVED_LIST_ID",
        "In Progress": "PLANKA_IN_PROGRESS_LIST_ID",
        "Needs Human Review": "PLANKA_NEEDS_HUMAN_LIST_ID",
        "Done": "PLANKA_DONE_LIST_ID",
    }.get(name, "")
    return os.environ.get(env_key, "") if env_key else ""


def handle_agent_lifecycle_event(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event", "")
    card_id = str(payload.get("card_id", "")).strip()
    if event == "author-pr-opened":
        target = "In Progress"
        labels = ["state:pr-open", "state:review-agent"]
        pr_link_result = add_pr_link_to_card(card_id, str(payload.get("pr_url", "")))
    elif event == "review-completed":
        decision = payload.get("decision", "")
        target = "Needs Human Review"
        pr_link_result = {"pr_link_updated": False, "reason": "not an author event"}
        if decision == "needs_human_review":
            labels = ["review:pr"]
        elif decision == "request_changes":
            labels = ["review:changes-requested"]
        elif decision == "approve_and_merge":
            labels = ["review:pr", "state:ready-to-merge"]
        elif payload.get("merged"):
            target = "Done"
            labels = []
        else:
            target = ""
            labels = []
    else:
        target = ""
        labels = []
        pr_link_result = {"pr_link_updated": False, "reason": "unhandled lifecycle event"}

    if not target:
        return {"ok": True, "handled": "noop", "reason": f"unhandled lifecycle event: {event}"}
    target_list_id = list_id_for_name(target)
    label_result = set_card_state_labels(card_id, labels) if card_id else {"labels_updated": False, "reason": "missing card id"}
    move_result = move_planka_card(card_id, target_list_id) if card_id else {"moved": False, "reason": "missing card id"}
    return {
        "ok": True,
        "handled": "agent-lifecycle",
        "event": event,
        "card_id": card_id,
        "target_list": target,
        **pr_link_result,
        **label_result,
        **move_result,
    }


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
    label_result = set_card_state_labels(card_id, []) if card_id else {"labels_updated": False, "reason": "missing card id"}
    move_result = move_planka_card(card_id, target_list_id) if card_id else {"moved": False, "reason": "missing card id"}
    return {"ok": True, "handled": "merged-pr", "card_id": card_id, "target_list": target_name, **label_result, **move_result}


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
            elif self.path in {"/agent/lifecycle", "/agent/status"}:
                result = handle_agent_lifecycle_event(payload)
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
