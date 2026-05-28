"""Planka cards for categorize deferrals (human review queue)."""

from __future__ import annotations

import os
from typing import Any

from .runner import EntryOutcome


def finance_defer_list_id() -> str:
    return (
        os.environ.get("PLANKA_FINANCE_DEFER_LIST_ID", "").strip()
        or os.environ.get("PLANKA_FINANCE_INBOX_LIST_ID", "").strip()
    )


def planka_defer_configured() -> bool:
    from apps._shared.planka_client import planka_auth_configured

    return planka_auth_configured() and bool(finance_defer_list_id())


def _card_url(card_id: str) -> str:
    base = os.environ.get("PLANKA_BASE_URL", "").rstrip("/")
    return f"{base}/cards/{card_id}" if base and card_id else ""


def _defer_description(outcome: EntryOutcome, *, correlation_id: str) -> str:
    lines = [
        f"**Date:** {outcome.date}",
        f"**Payee:** {outcome.description}",
        f"**Proposed:** `{outcome.category or '—'}`",
        f"**Confidence:** {outcome.confidence:.3f}",
        f"**Verifier rounds:** {outcome.verifier_rounds}",
        f"**Reason:** {outcome.reason or 'verifier escalation'}",
        f"**Run:** `{correlation_id}`",
        "",
        "Ledger row is still `!` on `Expenses:Uncategorized`. "
        "After choosing a category, edit the posting or re-run categorize.",
    ]
    return "\n".join(lines)


def create_defer_card(outcome: EntryOutcome, *, correlation_id: str) -> dict[str, Any]:
    """Create one Planka card for a deferred transaction."""
    from apps._shared.planka_client import planka_new_card_payload, planka_request

    list_id = finance_defer_list_id()
    if not list_id:
        raise ValueError("PLANKA_FINANCE_DEFER_LIST_ID or PLANKA_FINANCE_INBOX_LIST_ID is required")

    title = f"{outcome.date} — {outcome.description[:72]}"
    payload = planka_new_card_payload(
        name=title,
        description=_defer_description(outcome, correlation_id=correlation_id),
        card_type="project",
    )
    created = planka_request(f"lists/{list_id}/cards", method="POST", payload=payload)
    card = created.get("item", created)
    card_id = str(card.get("id", ""))
    return {"card_id": card_id, "url": _card_url(card_id), "title": title}


def post_defer_cards(
    outcomes: list[EntryOutcome],
    *,
    correlation_id: str,
) -> list[dict[str, Any]]:
    """Create Planka cards for all deferred outcomes. Skips if not configured."""
    if not planka_defer_configured():
        return []
    posted: list[dict[str, Any]] = []
    for outcome in outcomes:
        if outcome.status != "deferred":
            continue
        try:
            posted.append(create_defer_card(outcome, correlation_id=correlation_id))
        except Exception as exc:
            posted.append(
                {
                    "error": str(exc),
                    "date": outcome.date,
                    "description": outcome.description,
                }
            )
    return posted
