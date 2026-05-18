"""Tier-1/2/3 escalation dispatcher.

The dispatcher takes:

  - a ``task_class`` (e.g. ``finance.categorize``, ``homelab.deploy``)
  - the resolved :class:`TierBudgets` for that class+principal combo
  - an ``attempt`` callable for Tier 1 (the agent's own self-recovery loop;
    typically wraps the verifier loop)
  - injectable Tier 2 and Tier 3 handlers
  - an audit hook for ``tier_transition`` events

It runs the three-tier protocol described in
``docs/plans/phase-0-platform.md`` Phase 0.11 and returns an
:class:`EscalationResult` summarizing the outcome and every transition.

This module does not call Discord, the queue, or LiteLLM directly.
Concrete handlers are injected so:

  - tests can drive the dispatcher synchronously with mocks;
  - consumers (agent:finance, agent:homelab-maintainer) can wire the
    handlers their domain demands (queue.enqueue for Tier 2, the per-bot
    Discord bridge for Tier 3, etc.).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from .policy import TierBudgets


class AttemptOutcome(str, Enum):
    """The result the ``attempt`` callable returns for one Tier-1 try."""

    SUCCESS = "success"             # work completed; dispatcher terminates
    RETRY = "retry"                 # transient failure; loop with the next iter
    HARD_FAIL = "hard_fail"         # unrecoverable inside Tier 1; jump to T2/T3


@dataclass(frozen=True)
class TierTransition:
    """One transition recorded for audit + included in the result."""

    from_tier: int
    to_tier: int
    reason: str
    elapsed_seconds: float
    attempts_before: int

    def as_audit_dict(self) -> dict[str, Any]:
        return {
            "kind": "tier_transition",
            "from_tier": self.from_tier,
            "to_tier": self.to_tier,
            "reason": self.reason,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "attempts_before": self.attempts_before,
        }


@dataclass(frozen=True)
class EscalationResult:
    """Returned by ``Dispatcher.execute`` regardless of outcome."""

    task_class: str
    final_tier: int                       # 1 = self-recovered, 2/3 = escalated
    outcome: str                          # "success" | "rerouted" | "human_intervention" | "abandoned"
    elapsed_seconds: float
    attempts: int                         # Tier-1 attempts made
    transitions: tuple[TierTransition, ...] = field(default_factory=tuple)
    payload: Any = None                   # the successful attempt's return value (if any)
    blocked_reason: str | None = None     # populated when outcome != success

    def succeeded(self) -> bool:
        return self.outcome == "success"


# Callable signatures. We use Optional/Tuple here (not the PEP 604 ``|``
# union and built-in generics) because these aliases are evaluated at
# module import time; ``from __future__ import annotations`` does not
# defer assignments, only annotations.
AttemptFn = Callable[[int, Optional[Mapping[str, Any]]], Tuple[AttemptOutcome, Any, str]]
"""Tier-1 attempt callable.

  Args:
    attempt_index: 1-based attempt counter.
    previous_failure: dict with at least ``reason`` from the previous
        attempt's tuple[2] (None on the first call). Use to inject
        verifier feedback / structured retry context.
  Returns:
    (outcome, payload, reason)
      outcome: SUCCESS | RETRY | HARD_FAIL
      payload: arbitrary; only meaningful on SUCCESS
      reason: short human-readable description (audit + debugging)
"""

Tier2Fn = Callable[[Mapping[str, Any]], Tuple[bool, Any, str]]
"""Tier-2 reroute callable.

  Args:
    envelope: dict carrying ``task_class``, ``transitions``, ``transcript``,
        ``urgent`` etc. Up to the caller; the dispatcher passes through.
  Returns:
    (success, payload, reason)
      success: True if the reroute actually resolved the task; False to
        continue to Tier 3.
      payload: arbitrary; SUCCESS payload semantics.
      reason: human-readable.
"""

Tier3Fn = Callable[[Mapping[str, Any]], Tuple[str, Any]]
"""Tier-3 human-handoff callable.

  Args:
    envelope: dict carrying everything from Tier 2 plus ``escalation_path``
        and ``budget_exhausted: bool``.
  Returns:
    (outcome, payload)
      outcome: "human_intervention" | "abandoned"
      payload: typically a Discord message id, an approvals card id, or None
"""

AuditFn = Callable[[Mapping[str, Any]], None]
"""Optional audit hook; receives each TierTransition's as_audit_dict()."""


@dataclass
class Dispatcher:
    """Runs the three-tier escalation protocol for one task."""

    budgets: TierBudgets
    attempt: AttemptFn
    tier2: Tier2Fn | None = None
    tier3: Tier3Fn | None = None
    audit_hook: AuditFn | None = None
    clock: Callable[[], float] = field(default=time.monotonic)

    def execute(
        self,
        *,
        task_class: str,
        urgent: bool = False,
        envelope_extra: Mapping[str, Any] | None = None,
    ) -> EscalationResult:
        start = self.clock()
        transitions: list[TierTransition] = []
        previous: Mapping[str, Any] | None = None
        attempts = 0
        last_reason = ""

        # ---- Tier 1 -------------------------------------------------
        while True:
            now = self.clock()
            elapsed = now - start
            if attempts >= self.budgets.tier1_max_attempts:
                last_reason = (
                    f"tier1 max_attempts {self.budgets.tier1_max_attempts} reached"
                )
                break
            if elapsed >= self.budgets.tier1_budget_seconds:
                last_reason = (
                    f"tier1 wall-clock budget {self.budgets.tier1_budget_seconds}s "
                    f"exhausted after {attempts} attempt(s)"
                )
                break

            attempts += 1
            outcome, payload, reason = self.attempt(attempts, previous)
            last_reason = reason

            if outcome == AttemptOutcome.SUCCESS:
                return EscalationResult(
                    task_class=task_class,
                    final_tier=1,
                    outcome="success",
                    elapsed_seconds=self.clock() - start,
                    attempts=attempts,
                    transitions=tuple(transitions),
                    payload=payload,
                )
            if outcome == AttemptOutcome.HARD_FAIL:
                last_reason = f"tier1 hard_fail: {reason}"
                break
            previous = {"reason": reason, "attempt_index": attempts}

        # ---- Tier 2 -------------------------------------------------
        if self.budgets.skip_tier2 or self.tier2 is None:
            transition_reason = (
                "tier2 skipped by policy" if self.budgets.skip_tier2 else
                "tier2 handler not configured"
            )
            transitions.append(
                self._emit_transition(
                    from_tier=1, to_tier=3,
                    reason=f"{last_reason}; {transition_reason}",
                    elapsed=self.clock() - start,
                    attempts_before=attempts,
                )
            )
            return self._run_tier3(
                task_class=task_class,
                urgent=urgent,
                envelope_extra=envelope_extra,
                start=start,
                attempts=attempts,
                transitions=transitions,
                final_blocked_reason=last_reason,
            )

        transitions.append(
            self._emit_transition(
                from_tier=1, to_tier=2,
                reason=last_reason,
                elapsed=self.clock() - start,
                attempts_before=attempts,
            )
        )
        tier2_envelope = {
            "task_class": task_class,
            "urgent": urgent,
            "transitions": [t.as_audit_dict() for t in transitions],
            "tier1_last_reason": last_reason,
            **(envelope_extra or {}),
        }
        t2_success, t2_payload, t2_reason = self.tier2(tier2_envelope)
        if t2_success:
            return EscalationResult(
                task_class=task_class,
                final_tier=2,
                outcome="rerouted",
                elapsed_seconds=self.clock() - start,
                attempts=attempts,
                transitions=tuple(transitions),
                payload=t2_payload,
            )

        # ---- Tier 3 -------------------------------------------------
        transitions.append(
            self._emit_transition(
                from_tier=2, to_tier=3,
                reason=t2_reason or "tier2 returned no resolution",
                elapsed=self.clock() - start,
                attempts_before=attempts,
            )
        )
        return self._run_tier3(
            task_class=task_class,
            urgent=urgent,
            envelope_extra=envelope_extra,
            start=start,
            attempts=attempts,
            transitions=transitions,
            final_blocked_reason=t2_reason or last_reason,
        )

    # ------------------------------------------------------------------

    def _emit_transition(
        self,
        *,
        from_tier: int,
        to_tier: int,
        reason: str,
        elapsed: float,
        attempts_before: int,
    ) -> TierTransition:
        transition = TierTransition(
            from_tier=from_tier,
            to_tier=to_tier,
            reason=reason,
            elapsed_seconds=elapsed,
            attempts_before=attempts_before,
        )
        if self.audit_hook is not None:
            try:
                self.audit_hook(transition.as_audit_dict())
            except Exception:  # noqa: BLE001 - audit failure must never break the loop
                pass
        return transition

    def _run_tier3(
        self,
        *,
        task_class: str,
        urgent: bool,
        envelope_extra: Mapping[str, Any] | None,
        start: float,
        attempts: int,
        transitions: Sequence[TierTransition],
        final_blocked_reason: str,
    ) -> EscalationResult:
        if self.tier3 is None:
            return EscalationResult(
                task_class=task_class,
                final_tier=3,
                outcome="abandoned",
                elapsed_seconds=self.clock() - start,
                attempts=attempts,
                transitions=tuple(transitions),
                blocked_reason=(
                    f"{final_blocked_reason}; no tier3 handler configured"
                ),
            )
        envelope = {
            "task_class": task_class,
            "urgent": urgent,
            "transitions": [t.as_audit_dict() for t in transitions],
            "blocked_reason": final_blocked_reason,
            **(envelope_extra or {}),
        }
        outcome, payload = self.tier3(envelope)
        return EscalationResult(
            task_class=task_class,
            final_tier=3,
            outcome=outcome,
            elapsed_seconds=self.clock() - start,
            attempts=attempts,
            transitions=tuple(transitions),
            payload=payload,
            blocked_reason=final_blocked_reason if outcome != "human_intervention" else None,
        )
