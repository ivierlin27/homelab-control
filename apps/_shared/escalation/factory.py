"""Build a wired :class:`Dispatcher` for a registry principal."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from apps._shared.audit import AuditLog
from apps._shared.registry import load_registry

from .dispatcher import AttemptOutcome, Dispatcher, Tier2Fn, Tier3Fn
from .policy import load_config, resolve_budgets
from .tier3_discord import make_tier3_discord_handler


def _append_trust_ledger(queue_dir: Path, event: dict[str, Any]) -> None:
    path = queue_dir / "trust-ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")


def build_dispatcher_for_principal(
    principal: str,
    *,
    task_class: str = "default",
    attempt: Callable[[int, dict[str, Any] | None], tuple[AttemptOutcome, Any, str]] | None = None,
    queue_dir: Path | None = None,
    tier3_principal: str | None = None,
    tier3_channel_name: str | None = None,
    dm_only_tier3: bool = False,
    registry: Any | None = None,
    audit: AuditLog | None = None,
) -> Dispatcher:
    """Return a :class:`Dispatcher` with Tier 2/3 handlers for *principal*."""
    from apps._shared.a2a import make_tier2_executive_handler

    reg = registry or load_registry()
    manifest = reg.get(principal)
    config = load_config()
    budgets = resolve_budgets(
        task_class=task_class,
        config=config,
        manifest_overrides=manifest.get("escalation_overrides"),
    )

    tier2: Tier2Fn | None = None
    if not budgets.skip_tier2:
        tier2 = make_tier2_executive_handler(
            caller=principal,
            reply_queue_dir=queue_dir,
            ask_timeout_seconds=float(min(budgets.tier2_budget_seconds, 300)),
        )

    t3_principal = tier3_principal or (
        "agent:finance" if principal == "agent:finance" else "agent:executive"
    )
    channel = tier3_channel_name
    if channel is None and principal == "agent:finance":
        channel = "#finance"

    tier3: Tier3Fn = make_tier3_discord_handler(
        principal=t3_principal,
        channel_name=channel or "#approvals",
        token=_discord_token_for_principal(t3_principal),
        dm_only=dm_only_tier3 or principal == "agent:finance",
        audit=audit,
        registry=reg,
    )

    def attempt_hard_fail(
        attempt_index: int,
        previous: dict[str, Any] | None,
    ) -> tuple[AttemptOutcome, Any, str]:
        return (AttemptOutcome.HARD_FAIL, None, "attempt not configured")

    audit_hook = None
    if queue_dir is not None:

        def audit_hook(event: dict[str, Any]) -> None:
            _append_trust_ledger(
                queue_dir,
                {
                    "event": "tier_transition",
                    "principal": principal,
                    "task_class": task_class,
                    **event,
                },
            )

    return Dispatcher(
        budgets=budgets,
        attempt=attempt or attempt_hard_fail,
        tier2=tier2,
        tier3=tier3,
        audit_hook=audit_hook,
    )


def _discord_token_for_principal(principal: str) -> str | None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    return token or None


def run_escalation(
    principal: str,
    *,
    task_class: str,
    attempt: Callable[[int, dict[str, Any] | None], tuple[AttemptOutcome, Any, str]],
    urgent: bool = False,
    envelope_extra: dict[str, Any] | None = None,
    queue_dir: Path | None = None,
    dm_only_tier3: bool = False,
    registry: Any | None = None,
) -> Any:
    """Run the three-tier protocol with a real Tier-1 *attempt* callable."""
    dispatcher = build_dispatcher_for_principal(
        principal,
        task_class=task_class,
        attempt=attempt,
        queue_dir=queue_dir,
        dm_only_tier3=dm_only_tier3,
        registry=registry,
    )
    return dispatcher.execute(
        task_class=task_class,
        urgent=urgent,
        envelope_extra=envelope_extra,
    )
