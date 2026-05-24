"""Finance-agent escalation helpers (Tier 3 DM on ingest/verifier failures)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from apps._shared.escalation import AttemptOutcome, EscalationResult
from apps._shared.escalation.factory import run_escalation

DEFAULT_PRINCIPAL = "agent:finance"
DEFAULT_QUEUE_DIR = Path.home() / ".local/state/homelab-control/agent-finance"


def escalation_disabled() -> bool:
    return os.environ.get("HOMELAB_ESCALATION_DISABLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def escalate_finance_failure(
    *,
    task_class: str,
    reason: str,
    urgent: bool = False,
    envelope_extra: dict[str, Any] | None = None,
    queue_dir: Path | None = None,
) -> EscalationResult | None:
    """Run finance escalation (skips Tier 2 per policy). Returns None when disabled."""
    if escalation_disabled():
        return None

    def attempt(
        attempt_index: int,
        previous: dict[str, Any] | None,
    ) -> tuple[AttemptOutcome, Any, str]:
        return (AttemptOutcome.HARD_FAIL, None, reason)

    return run_escalation(
        DEFAULT_PRINCIPAL,
        task_class=task_class,
        attempt=attempt,
        urgent=urgent,
        envelope_extra=envelope_extra,
        queue_dir=queue_dir or DEFAULT_QUEUE_DIR,
        dm_only_tier3=True,
    )
