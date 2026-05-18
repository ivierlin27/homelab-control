"""Tiered escalation primitive (Phase 0.11, minimal slice).

Three tiers between an agent's first attempt and Kevin's inbox:

  - **Tier 1** — agent self-recovery (in-loop retries with verifier feedback,
    capped by attempts and wall-clock budget).
  - **Tier 2** — executive reroute (hand off to ``agent:executive``'s
    queue; one reroute max per request).
  - **Tier 3** — human (Discord post to ``#approvals``; DM Kevin if urgent
    or the channel ping goes unacknowledged past a budget).

This module is intentionally minimal: it owns the policy (config schema)
and the dispatcher (loop + transitions + audit). It does NOT own the
concrete Tier 2 (queue) or Tier 3 (Discord) implementations — those are
injected as callables so the platform primitive can be unit-tested
without a running queue or Discord bridge.

Why a primitive here at all? Because every agent needs the same shape:
budget, attempt loop, transition audit. Without this primitive each
agent reinvents it differently, and the audit chain loses comparability
across domains.

See ``docs/plans/phase-0-platform.md`` Phase 0.11 for the design, and
``docs/plans/phase-1-finance.md`` for the first consumer (finance has
``escalation_overrides`` that skip Tier 2 entirely so verifier failures
land in Kevin DM within 5 minutes).
"""

from .policy import (
    DEFAULT_TASK_CLASS,
    EscalationConfig,
    EscalationConfigError,
    TierBudgets,
    load_config,
    resolve_budgets,
)
from .dispatcher import (
    AttemptOutcome,
    Dispatcher,
    EscalationResult,
    TierTransition,
)

__all__ = [
    "DEFAULT_TASK_CLASS",
    "AttemptOutcome",
    "Dispatcher",
    "EscalationConfig",
    "EscalationConfigError",
    "EscalationResult",
    "TierBudgets",
    "TierTransition",
    "load_config",
    "resolve_budgets",
]
