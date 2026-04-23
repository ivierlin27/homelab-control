#!/usr/bin/env python3
"""Small utilities for author-agent plan rendering."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def render_from_card(card_path: Path, template_path: Path) -> str:
    card = json.loads(card_path.read_text())
    template = template_path.read_text()
    title = card.get("title") or card.get("name") or "Untitled task"
    repo = card.get("repo", "homelab-control")
    risk = card.get("risk", "safe-update")
    return (
        template.replace("<!-- one sentence outcome -->", title)
        .replace("- repo:", f"- repo: {repo}")
        .replace("- risk:", f"- risk: {risk}")
    )


def summarize_result(card_path: Path, pr_url: str) -> str:
    card = json.loads(card_path.read_text())
    return json.dumps(
        {
            "principal": "agent:homelab",
            "title": card.get("title") or card.get("name") or "Untitled task",
            "pr_url": pr_url,
            "status": "author_review_ready",
        },
        indent=2,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render-plan")
    render.add_argument("--card", required=True)
    render.add_argument("--template", default=str(ROOT / "config" / "planka" / "card-template.md"))

    summary = subparsers.add_parser("summarize-result")
    summary.add_argument("--card", required=True)
    summary.add_argument("--pr-url", required=True)

    args = parser.parse_args()

    if args.command == "render-plan":
        print(render_from_card(Path(args.card), Path(args.template)))
        return 0

    if args.command == "summarize-result":
        print(summarize_result(Path(args.card), args.pr_url))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
