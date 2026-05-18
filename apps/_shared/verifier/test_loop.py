"""Tests for the verifier-loop primitive (Phase 0.4)."""

from __future__ import annotations

import pytest

from .loop import (
    VerifierEscalation,
    VerifierRound,
    VerifierVerdict,
    run_verifier_loop,
)


def test_first_round_accept_passes_through_without_calling_builder():
    audit: list[dict] = []
    accepted, history = run_verifier_loop(
        claim={"image": "n8n", "current": "1.0.0", "newest": "1.0.1"},
        evidence={"registry_tags": ["1.0.0", "1.0.1"]},
        verifier=lambda claim, ev: (VerifierVerdict.ACCEPT, "matches registry", {"registry_tags": ["1.0.0", "1.0.1"]}),
        persona="upgrade-verifier",
        builder_revise=lambda *_: pytest.fail("builder_revise must not be called when verifier accepts"),
        audit=audit.append,
    )
    assert accepted["newest"] == "1.0.1"
    assert len(history) == 1
    assert history[0].verdict is VerifierVerdict.ACCEPT
    assert history[0].duration_ms >= 0
    assert audit and audit[0]["event"] == "verifier_round"
    assert audit[0]["persona"] == "upgrade-verifier"


def test_needs_revision_invokes_builder_then_accepts():
    revise_calls: list[VerifierRound] = []

    def verifier(claim, evidence):
        # Reject the first claim's "newest" then accept the revised one
        if claim["newest"] == "wrong":
            return VerifierVerdict.NEEDS_REVISION, "registry says 2.0.0", {"newest_actual": "2.0.0"}
        return VerifierRound(0, VerifierVerdict.ACCEPT, "ok", {"newest_actual": claim["newest"]})

    def builder_revise(claim, last_round):
        revise_calls.append(last_round)
        return {**claim, "newest": last_round.rechecked_evidence["newest_actual"]}

    accepted, history = run_verifier_loop(
        claim={"image": "n8n", "newest": "wrong"},
        evidence={},
        verifier=verifier,
        persona="upgrade-verifier",
        builder_revise=builder_revise,
    )
    assert accepted["newest"] == "2.0.0"
    assert len(history) == 2
    assert history[0].verdict is VerifierVerdict.NEEDS_REVISION
    assert history[1].verdict is VerifierVerdict.ACCEPT
    assert len(revise_calls) == 1


def test_reject_escalates_with_full_history():
    with pytest.raises(VerifierEscalation) as ei:
        run_verifier_loop(
            claim={"x": 1},
            evidence={},
            verifier=lambda c, e: (VerifierVerdict.REJECT, "no such image in registry", None),
            persona="upgrade-verifier",
        )
    esc = ei.value
    assert esc.persona == "upgrade-verifier"
    assert len(esc.rounds) == 1
    assert "rejected" in esc.reason
    assert esc.history()[0]["verdict"] == "reject"


def test_max_rounds_exhaustion_escalates():
    def always_revise(claim, ev):
        return VerifierVerdict.NEEDS_REVISION, "still off", {"hint": "try harder"}

    with pytest.raises(VerifierEscalation) as ei:
        run_verifier_loop(
            claim={"v": 0},
            evidence={},
            verifier=always_revise,
            persona="p",
            builder_revise=lambda claim, r: {**claim, "v": claim["v"] + 1},
            max_rounds=3,
        )
    assert len(ei.value.rounds) == 3
    assert ei.value.last_claim["v"] == 3
    assert "max_rounds" in ei.value.reason


def test_needs_revision_without_builder_revise_escalates_immediately():
    with pytest.raises(VerifierEscalation) as ei:
        run_verifier_loop(
            claim={},
            evidence={},
            verifier=lambda c, e: (VerifierVerdict.NEEDS_REVISION, "needs work", None),
            persona="p",
        )
    assert "no builder_revise" in ei.value.reason


def test_invalid_max_rounds_raises_value_error():
    with pytest.raises(ValueError):
        run_verifier_loop(
            claim={}, evidence={},
            verifier=lambda c, e: (VerifierVerdict.ACCEPT, "n/a", None),
            persona="p", max_rounds=0,
        )


def test_verifier_returning_unexpected_type_raises_type_error():
    with pytest.raises(TypeError):
        run_verifier_loop(
            claim={}, evidence={},
            verifier=lambda c, e: "accept",  # neither tuple nor VerifierRound
            persona="p",
        )


def test_audit_records_every_round_with_correlation_id():
    audit: list[dict] = []

    def verifier(claim, ev):
        if claim["n"] < 2:
            return VerifierVerdict.NEEDS_REVISION, "low", None
        return VerifierVerdict.ACCEPT, "ok", None

    accepted, _ = run_verifier_loop(
        claim={"n": 0},
        evidence={},
        verifier=verifier,
        persona="p",
        builder_revise=lambda c, r: {**c, "n": c["n"] + 1},
        audit=audit.append,
        correlation_id="task-42",
    )
    assert accepted["n"] == 2
    assert [a["round"]["round_index"] for a in audit] == [1, 2, 3]
    assert all(a["correlation_id"] == "task-42" for a in audit)
