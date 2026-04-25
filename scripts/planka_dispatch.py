#!/usr/bin/env python3
"""Dispatch Planka card exports into author/review queue jobs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def card_id(card: dict[str, Any]) -> str:
    raw = str(card.get("id", "")).strip()
    return raw or "unknown"


def card_title(card: dict[str, Any]) -> str:
    return card.get("title") or card.get("name") or "Untitled task"


def list_name(card: dict[str, Any]) -> str:
    value = card.get("list_name") or card.get("list") or card.get("column") or ""
    return str(value).strip()


def dispatch_payload(
    card: dict[str, Any],
    *,
    author_queue: Path,
    review_queue: Path,
    artifact_dir: Path,
) -> dict[str, Any]:
    current_list = list_name(card)
    cid = card_id(card)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    if current_list == "Plan Ready":
        output_path = artifact_dir / f"card-{cid}.execution.json"
        job_path = author_queue / "inbox" / f"card-{cid}-plan.json"
        payload = {
            "action": "create-execution-job",
            "card": str(Path(card["source_path"]).resolve()) if card.get("source_path") else card.get("source_path", ""),
            "output_path": str(output_path),
        }
        write_json(job_path, payload)
        return {
            "card_id": cid,
            "title": card_title(card),
            "list_name": current_list,
            "queue_job": str(job_path),
            "artifact_path": str(output_path),
            "action": "author-agent-plan",
        }

    if current_list == "Approved To Execute":
        if not isinstance(card.get("execution"), dict):
            raise ValueError("card is missing execution details for Approved To Execute")
        job_path = author_queue / "inbox" / f"card-{cid}-execute.json"
        execution = {
            "action": "execute-task",
            "title": card_title(card),
            "summary": card.get("summary", ""),
            "labels": card.get("labels", [card.get("risk", "safe-update")]),
            "plan_link": card.get("plan_link") or card.get("url", ""),
            "planka_card": card.get("planka_card") or card.get("url", ""),
            "card_id": cid,
            **card["execution"],
        }
        write_json(job_path, execution)
        return {
            "card_id": cid,
            "title": card_title(card),
            "list_name": current_list,
            "queue_job": str(job_path),
            "action": "author-agent-execute",
        }

    if current_list == "Needs Human Review":
        review_context = card.get("review_context_path")
        if not review_context and card.get("pr_url"):
            review_context = artifact_dir / f"card-{cid}.review-context.json"
            write_json(
                Path(review_context),
                {
                    "pr_url": card["pr_url"],
                    "plan_link": card.get("plan_link", ""),
                    "planka_card": card.get("planka_card") or card.get("url", ""),
                    "labels": card.get("labels", []),
                },
            )
        if not review_context:
            raise ValueError("card is missing review_context_path or pr_url for review dispatch")
        job_path = review_queue / "inbox" / f"card-{cid}-review.json"
        write_json(job_path, {"action": "review-pr", "input": str(review_context)})
        return {
            "card_id": cid,
            "title": card_title(card),
            "list_name": current_list,
            "queue_job": str(job_path),
            "review_context_path": str(review_context),
            "action": "review-agent-start",
        }

    raise ValueError(f"unsupported Planka list for dispatch: {current_list}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--card", required=True, help="Path to a card JSON export")
    parser.add_argument("--author-queue", required=True)
    parser.add_argument("--review-queue", required=True)
    parser.add_argument("--artifact-dir", required=True)
    args = parser.parse_args()

    card_path = Path(args.card)
    card = load_json(card_path)
    card.setdefault("source_path", str(card_path))
    result = dispatch_payload(
        card,
        author_queue=Path(args.author_queue),
        review_queue=Path(args.review_queue),
        artifact_dir=Path(args.artifact_dir),
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
