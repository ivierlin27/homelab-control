"""Live Planka smoke for finance categorize defer cards (optional ops check)."""

from __future__ import annotations

import time
from typing import Any

from .planka import create_defer_card, planka_defer_configured
from .runner import EntryOutcome


SMOKE_PAYEE = "smoke-finance-defer"
SMOKE_CORRELATION_PREFIX = "live.smoke.finance-planka"


def live_smoke_finance_planka(
    *,
    correlation_id: str | None = None,
    cleanup: bool = True,
) -> dict[str, Any]:
    """Create one defer-style card; delete when ``cleanup`` is true (default)."""
    from apps._shared.planka_client import planka_auth_configured, planka_request

    if not planka_auth_configured():
        raise RuntimeError("Planka auth not configured (PLANKA_BASE_URL + API key/token)")
    if not planka_defer_configured():
        raise RuntimeError(
            "Finance defer list not configured (PLANKA_FINANCE_DEFER_LIST_ID)"
        )

    corr = correlation_id or f"{SMOKE_CORRELATION_PREFIX}-{int(time.time())}"
    outcome = EntryOutcome(
        date="2099-01-01",
        description=SMOKE_PAYEE,
        status="deferred",
        category="Expenses:Misc",
        confidence=0.42,
        reason="live smoke (safe to delete)",
        verifier_rounds=1,
    )
    created = create_defer_card(outcome, correlation_id=corr)
    card_id = str(created.get("card_id", "")).strip()
    if not card_id:
        raise RuntimeError(f"Planka create returned no card id: {created!r}")

    deleted = False
    delete_error = ""
    if cleanup:
        try:
            planka_request(f"cards/{card_id}", method="DELETE")
            deleted = True
        except Exception as exc:
            delete_error = str(exc)

    return {
        "correlation_id": corr,
        "card_id": card_id,
        "url": created.get("url", ""),
        "deleted": deleted if cleanup else None,
        "delete_error": delete_error,
    }
