"""Tests for the Tier-1/2/3 escalation dispatcher.

We drive the dispatcher with a synthetic monotonic clock and counted-call
``attempt``/``tier2``/``tier3`` mocks so each tier protocol branch is
deterministic. The audit hook is checked for the expected transition
sequence per branch.
"""

from __future__ import annotations

from typing import Any

import pytest

from .dispatcher import (
    AttemptOutcome,
    Dispatcher,
    EscalationResult,
    TierTransition,
)
from .policy import TierBudgets


class FakeClock:
    """Monotonic clock that advances by a fixed step on every read."""

    def __init__(self, step: float = 1.0):
        self.step = step
        self.now = 0.0

    def __call__(self) -> float:
        out = self.now
        self.now += self.step
        return out


def _budgets(**overrides: Any) -> TierBudgets:
    base = TierBudgets(
        tier1_budget_seconds=100,
        tier1_max_attempts=3,
        tier2_budget_seconds=1000,
        tier3_dm_after_seconds=2000,
    )
    return base.with_overrides(overrides) if overrides else base


# ---- Tier 1 success path -------------------------------------------


def test_first_attempt_success_returns_tier1():
    audit: list[dict] = []
    attempts: list[tuple[int, Any]] = []

    def attempt(i, prev):
        attempts.append((i, prev))
        return AttemptOutcome.SUCCESS, {"ok": True}, "first try worked"

    d = Dispatcher(
        budgets=_budgets(),
        attempt=attempt,
        audit_hook=audit.append,
        clock=FakeClock(),
    )
    res = d.execute(task_class="test.class")
    assert res.final_tier == 1
    assert res.outcome == "success"
    assert res.payload == {"ok": True}
    assert res.attempts == 1
    assert res.transitions == ()
    assert audit == []  # no transitions on a clean success


def test_tier1_recovers_after_retries():
    counter = {"i": 0}

    def attempt(i, prev):
        counter["i"] = i
        if i < 3:
            return AttemptOutcome.RETRY, None, f"transient err on attempt {i}"
        return AttemptOutcome.SUCCESS, "fixed", "stable on third try"

    d = Dispatcher(budgets=_budgets(), attempt=attempt, clock=FakeClock())
    res = d.execute(task_class="test.class")
    assert res.final_tier == 1
    assert res.outcome == "success"
    assert res.attempts == 3
    assert res.payload == "fixed"


def test_attempt_receives_previous_failure_context():
    seen_prev: list[Any] = []

    def attempt(i, prev):
        seen_prev.append(prev)
        if i < 2:
            return AttemptOutcome.RETRY, None, f"err-{i}"
        return AttemptOutcome.SUCCESS, "ok", "good"

    d = Dispatcher(budgets=_budgets(), attempt=attempt, clock=FakeClock())
    d.execute(task_class="test.class")
    assert seen_prev[0] is None
    assert seen_prev[1] == {"reason": "err-1", "attempt_index": 1}


# ---- Tier 1 -> Tier 2 ----------------------------------------------


def test_max_attempts_exhausted_routes_to_tier2():
    def attempt(i, prev):
        return AttemptOutcome.RETRY, None, "still failing"

    def tier2(envelope):
        assert envelope["task_class"] == "test.class"
        assert envelope["urgent"] is False
        assert len(envelope["transitions"]) == 1
        assert "max_attempts" in envelope["tier1_last_reason"]
        return True, "rerouted-payload", "executive handled it"

    audit: list[dict] = []
    d = Dispatcher(
        budgets=_budgets(),
        attempt=attempt,
        tier2=tier2,
        audit_hook=audit.append,
        clock=FakeClock(),
    )
    res = d.execute(task_class="test.class")
    assert res.final_tier == 2
    assert res.outcome == "rerouted"
    assert res.payload == "rerouted-payload"
    assert res.attempts == 3
    assert len(audit) == 1
    assert audit[0]["from_tier"] == 1
    assert audit[0]["to_tier"] == 2


def test_hard_fail_jumps_straight_to_tier2():
    def attempt(i, prev):
        return AttemptOutcome.HARD_FAIL, None, "ledger inconsistent"

    def tier2(envelope):
        return True, None, "rerouted"

    d = Dispatcher(budgets=_budgets(), attempt=attempt, tier2=tier2, clock=FakeClock())
    res = d.execute(task_class="test.class")
    assert res.final_tier == 2
    assert res.attempts == 1
    assert "hard_fail" in res.transitions[0].reason


# ---- Tier 1 -> Tier 3 (skip Tier 2) --------------------------------


def test_skip_tier2_jumps_to_tier3():
    def attempt(i, prev):
        return AttemptOutcome.RETRY, None, "transient"

    def tier3(envelope):
        return "human_intervention", "msg-1234"

    audit: list[dict] = []
    d = Dispatcher(
        budgets=_budgets(skip_tier2=True),
        attempt=attempt,
        tier3=tier3,
        audit_hook=audit.append,
        clock=FakeClock(),
    )
    res = d.execute(task_class="finance.categorize")
    assert res.final_tier == 3
    assert res.outcome == "human_intervention"
    assert res.payload == "msg-1234"
    assert len(audit) == 1
    assert audit[0]["from_tier"] == 1
    assert audit[0]["to_tier"] == 3
    assert "tier2 skipped by policy" in audit[0]["reason"]


def test_tier2_handler_absent_jumps_to_tier3():
    def attempt(i, prev):
        return AttemptOutcome.HARD_FAIL, None, "out of options"

    def tier3(envelope):
        return "human_intervention", None

    d = Dispatcher(budgets=_budgets(), attempt=attempt, tier3=tier3, clock=FakeClock())
    res = d.execute(task_class="x")
    assert res.final_tier == 3
    assert res.outcome == "human_intervention"


# ---- Tier 2 declines -> Tier 3 -------------------------------------


def test_tier2_returns_false_falls_through_to_tier3():
    def attempt(i, prev):
        return AttemptOutcome.RETRY, None, "x"

    def tier2(envelope):
        return False, None, "executive can't take this"

    captured_envelope: dict = {}

    def tier3(envelope):
        captured_envelope.update(envelope)
        return "human_intervention", "discord-msg-id"

    d = Dispatcher(
        budgets=_budgets(),
        attempt=attempt,
        tier2=tier2,
        tier3=tier3,
        clock=FakeClock(),
    )
    res = d.execute(task_class="z", urgent=True)
    assert res.final_tier == 3
    assert res.outcome == "human_intervention"
    assert captured_envelope["urgent"] is True
    # Two transitions: 1->2 and 2->3
    assert len(res.transitions) == 2
    assert res.transitions[0].from_tier == 1
    assert res.transitions[1].from_tier == 2
    assert "executive can't" in res.transitions[1].reason


# ---- Tier 3 missing handler ----------------------------------------


def test_no_tier3_handler_returns_abandoned():
    def attempt(i, prev):
        return AttemptOutcome.HARD_FAIL, None, "no recovery"

    d = Dispatcher(
        budgets=_budgets(skip_tier2=True), attempt=attempt, clock=FakeClock()
    )
    res = d.execute(task_class="x")
    assert res.outcome == "abandoned"
    assert res.final_tier == 3
    assert res.blocked_reason and "no tier3 handler" in res.blocked_reason


# ---- Wall-clock budget exhaustion ----------------------------------


def test_tier1_wall_clock_budget_exhausts_before_max_attempts():
    """Each attempt 'takes' enough time that the budget runs out first."""

    def attempt(i, prev):
        return AttemptOutcome.RETRY, None, f"err-{i}"

    # FakeClock step=50, budget=100. Clock reads: start=0, then 50 (after
    # the start), then 100 -> budget reached -> break before attempt 3.
    d = Dispatcher(
        budgets=_budgets(tier1_budget_seconds=100, tier1_max_attempts=10),
        attempt=attempt,
        tier3=lambda env: ("human_intervention", None),
        clock=FakeClock(step=50.0),
    )
    res = d.execute(task_class="x")
    assert res.attempts < 10
    # Look at the transition reason
    assert "wall-clock budget" in res.transitions[-1].reason or "max_attempts" not in res.transitions[-1].reason


# ---- Audit hook safety -----------------------------------------------


def test_audit_hook_exception_does_not_break_loop():
    def attempt(i, prev):
        return AttemptOutcome.HARD_FAIL, None, "boom"

    def tier3(envelope):
        return "human_intervention", None

    def bad_audit(_row):
        raise RuntimeError("audit pipe broken")

    d = Dispatcher(
        budgets=_budgets(skip_tier2=True),
        attempt=attempt,
        tier3=tier3,
        audit_hook=bad_audit,
        clock=FakeClock(),
    )
    res = d.execute(task_class="x")
    assert res.outcome == "human_intervention"


# ---- Transition audit dict shape ------------------------------------


def test_transition_as_audit_dict_is_serializable():
    t = TierTransition(
        from_tier=1,
        to_tier=3,
        reason="r",
        elapsed_seconds=1.234567,
        attempts_before=2,
    )
    d = t.as_audit_dict()
    assert d == {
        "kind": "tier_transition",
        "from_tier": 1,
        "to_tier": 3,
        "reason": "r",
        "elapsed_seconds": 1.235,
        "attempts_before": 2,
    }


# ---- Envelope extras are passed through -----------------------------


def test_envelope_extra_reaches_tier2_and_tier3():
    captured: list[dict] = []

    def attempt(i, prev):
        return AttemptOutcome.RETRY, None, "x"

    def tier2(envelope):
        captured.append({"tier": 2, **envelope})
        return False, None, "no go"

    def tier3(envelope):
        captured.append({"tier": 3, **envelope})
        return "human_intervention", None

    d = Dispatcher(
        budgets=_budgets(),
        attempt=attempt,
        tier2=tier2,
        tier3=tier3,
        clock=FakeClock(),
    )
    d.execute(
        task_class="finance.categorize",
        envelope_extra={"card_id": "PLN-42", "card_url": "https://planka/x"},
    )
    for env in captured:
        assert env["card_id"] == "PLN-42"
        assert env["card_url"] == "https://planka/x"
