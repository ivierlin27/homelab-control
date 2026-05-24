"""Handle A2A ``help_request`` jobs in the executive worker queue."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from apps._shared.a2a.envelope import A2AEnvelope


def render_help_request_description(envelope: A2AEnvelope) -> str:
    """Build a Planka card body from an escalation ``help_request`` envelope."""
    payload = envelope.payload or {}
    lines = [
        f"**Caller:** `{envelope.caller}`",
        f"**Task class:** `{payload.get('task_class', 'unknown')}`",
        f"**Urgent:** {bool(payload.get('urgent', False))}",
        "",
        "## Blocked reason",
        "",
        str(payload.get("blocked_reason") or "(none provided)"),
        "",
        "## Tier transitions",
        "",
    ]
    transitions = payload.get("transitions") or []
    if not transitions:
        lines.append("(no transition history in payload)")
    else:
        for index, transition in enumerate(transitions, start=1):
            if not isinstance(transition, Mapping):
                lines.append(f"{index}. {transition!r}")
                continue
            lines.append(
                f"{index}. Tier {transition.get('from_tier')} → {transition.get('to_tier')}: "
                f"{transition.get('reason', '')}"
            )
    lines.extend(
        [
            "",
            "## A2A metadata",
            "",
            f"- correlation_id: `{envelope.correlation_id}`",
            f"- envelope_id: `{envelope.id}`",
            f"- created_at: {envelope.created_at}",
            "",
            "Executive acknowledged this help request. Review and delegate manually if needed.",
        ]
    )
    return "\n".join(lines)


def handle_help_request(
    envelope: A2AEnvelope,
    *,
    state_dir: Path,
    queue_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Process one ``help_request`` A2A envelope from another agent.

    Creates an intake Planka card when configured, appends a trust-ledger row,
    and returns a dict suitable for :func:`reply_to_caller`.
    """
    from apps.executive_agent import main as executive_main
    from apps.executive_agent.help_request_dedup import (
        duplicate_help_request_result,
        help_request_already_handled,
    )

    if queue_dir is not None and help_request_already_handled(
        envelope.correlation_id,
        state_dir=state_dir,
        queue_dir=queue_dir,
    ):
        return duplicate_help_request_result(envelope.correlation_id, dry_run=dry_run)

    # Import lazily so unit tests can patch Planka helpers on ``main``.
    payload = dict(envelope.payload or {})
    dry_run = dry_run or bool(payload.get("dry_run", False))
    task_class = str(payload.get("task_class") or "unknown")
    urgent = bool(payload.get("urgent", False))
    title = f"A2A help: {task_class} ({envelope.caller})"
    description = render_help_request_description(envelope)

    labels = ["trust-escalation", "a2a-help-request"]
    if urgent:
        labels.append("urgent")

    card_result: dict[str, Any] = {"created": False}
    if dry_run:
        card_result["dry_run"] = True
    else:
        try:
            created = executive_main.create_intake_card(title, description, labels)
            card = created["card"]
            card_id = str(card.get("id", ""))
            card_result = {
                "created": True,
                "card_id": card_id,
                "url": executive_main.card_url(card_id),
                "list_id": created["list_id"],
            }
        except Exception as exc:  # noqa: BLE001 — acknowledge even when Planka is down
            card_result = {"created": False, "error": str(exc)}

    event = {
        "event": "a2a-help-request",
        "occurred_at": executive_main.utc_now(),
        "principal": executive_main.DEFAULT_PRINCIPAL,
        "caller": envelope.caller,
        "correlation_id": envelope.correlation_id,
        "envelope_id": envelope.id,
        "task_class": task_class,
        "urgent": urgent,
        "blocked_reason": payload.get("blocked_reason", ""),
        "card": card_result,
        "dry_run": dry_run,
    }
    executive_main.append_jsonl(state_dir / "trust-ledger.jsonl", event)

    if card_result.get("created"):
        outcome = "executive acknowledged; intake card created"
    elif dry_run:
        outcome = "executive acknowledged (dry run)"
    elif card_result.get("error"):
        outcome = f"executive acknowledged (card failed: {card_result['error']})"
    else:
        outcome = "executive acknowledged"

    return {
        "ok": True,
        "outcome": outcome,
        "reply_payload": {
            "correlation_id": envelope.correlation_id,
            "task_class": task_class,
            "card": card_result,
        },
    }
