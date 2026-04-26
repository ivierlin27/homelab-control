#!/usr/bin/env python3
"""Run a real Planka -> agent -> PR -> review -> merge smoke test."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib import request


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def http_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        url,
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})},
    )
    with request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def planka_token(base_url: str, username: str, password: str) -> str:
    data = http_json(
        f"{base_url.rstrip('/')}/api/access-tokens",
        method="POST",
        payload={"emailOrUsername": username, "password": password},
    )
    return data["item"]


def planka_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def card_lane(base_url: str, token: str, card_id: str) -> tuple[str, list[str]]:
    payload = http_json(f"{base_url.rstrip('/')}/api/cards/{card_id}", headers=planka_headers(token))
    card = payload["item"]
    card_labels = {item["labelId"] for item in payload.get("included", {}).get("cardLabels", [])}
    labels = [
        item["name"]
        for item in payload.get("included", {}).get("labels", [])
        if item.get("id") in card_labels and item.get("name")
    ]
    return card["listId"], sorted(labels)


def wait_for_lane(base_url: str, token: str, card_id: str, list_id: str, *, timeout: int = 120) -> list[str]:
    deadline = time.time() + timeout
    last_labels: list[str] = []
    while time.time() < deadline:
        current, labels = card_lane(base_url, token, card_id)
        last_labels = labels
        if current == list_id:
            return labels
        time.sleep(3)
    raise TimeoutError(f"card {card_id} did not reach list {list_id}; last labels={last_labels}")


def latest_pr_for_card(forgejo_base: str, token: str, owner: str, repo: str, card_id: str) -> dict[str, Any]:
    pulls = http_json(
        f"{forgejo_base.rstrip('/')}/api/v1/repos/{owner}/{repo}/pulls?state=open",
        headers={"Authorization": f"token {token}"},
    )
    for pr in pulls:
        if card_id in (pr.get("body") or "") or card_id in pr.get("head", {}).get("ref", ""):
            return pr
    raise LookupError(f"no open PR found for card {card_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="E2E Planka agent smoke test")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    planka_base = env("PLANKA_BASE_URL", "https://planka.dev-path.org")
    planka_user = env("PLANKA_EMAIL_OR_USERNAME", "admin")
    planka_password = env("PLANKA_PASSWORD")
    inbox = env("PLANKA_INBOX_LIST_ID")
    approved = env("PLANKA_APPROVED_LIST_ID")
    review = env("PLANKA_NEEDS_HUMAN_LIST_ID")
    done = env("PLANKA_DONE_LIST_ID")
    forgejo_base = env("FORGEJO_BASE_URL", "https://forgejo.dev-path.org")
    forgejo_token = env("FORGEJO_API_TOKEN")
    owner = env("FORGEJO_REPO_OWNER", "kevin")
    repo = env("FORGEJO_REPO_NAME", "homelab-control")

    required = {
        "PLANKA_PASSWORD": planka_password,
        "PLANKA_INBOX_LIST_ID": inbox,
        "PLANKA_APPROVED_LIST_ID": approved,
        "PLANKA_NEEDS_HUMAN_LIST_ID": review,
        "PLANKA_DONE_LIST_ID": done,
        "FORGEJO_API_TOKEN": forgejo_token,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise SystemExit(f"missing required env vars: {', '.join(missing)}")

    token = planka_token(planka_base, planka_user, planka_password)
    marker = str(int(time.time()))
    content = (
        "## E2E smoke marker\n\n"
        f"Verified Planka agent flow at marker `{marker}`.\n"
    )
    agent_payload = {
        "summary": "Record an E2E smoke marker in Planka automation docs.",
        "labels": ["docs-only", "type:docs"],
        "execution": {
            "allowed_paths": ["docs"],
            "checks": ["git diff --check"],
            "summary_lines": ["Record an E2E smoke marker in Planka automation docs."],
            "operations": {"append_text": [{"path": "docs/PLANKA_AUTOMATION.md", "text": "\n" + content}]},
            "review_queue_dir": str(Path.home() / ".local/state/homelab-control/agent-review"),
        },
    }
    description = "Automated end-to-end smoke test.\n\n```agent-execution\n" + json.dumps(agent_payload, indent=2) + "\n```\n"
    card = http_json(
        f"{planka_base.rstrip('/')}/api/lists/{inbox}/cards",
        method="POST",
        headers=planka_headers(token),
        payload={"type": "project", "name": args.title, "description": description, "position": 65536},
    )["item"]
    card_id = card["id"]
    print(f"created card {card_id}")

    http_json(
        f"{planka_base.rstrip('/')}/api/cards/{card_id}",
        method="PATCH",
        headers=planka_headers(token),
        payload={"listId": approved, "position": 65536},
    )
    labels = wait_for_lane(planka_base, token, card_id, review, timeout=args.timeout)
    if "state:ready-to-merge" not in labels:
        raise RuntimeError(f"card reached review without state:ready-to-merge: {labels}")
    print(f"card reached Needs Human Review with labels {labels}")

    pr = latest_pr_for_card(forgejo_base, forgejo_token, owner, repo, card_id)
    print(f"merging PR {pr['number']}: {pr['html_url']}")
    http_json(
        f"{forgejo_base.rstrip('/')}/api/v1/repos/{owner}/{repo}/pulls/{pr['number']}/merge",
        method="POST",
        headers={"Authorization": f"token {forgejo_token}"},
        payload={"Do": "merge", "merge_message_field": f"Merge E2E smoke test card {card_id}"},
    )
    wait_for_lane(planka_base, token, card_id, done, timeout=args.timeout)
    print(f"card {card_id} reached Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
