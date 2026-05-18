"""Verifier-loop core. See package docstring for the pattern."""

from __future__ import annotations

import enum
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional


class VerifierVerdict(str, enum.Enum):
    """Outcome of a single verification round."""

    ACCEPT = "accept"
    REJECT = "reject"
    NEEDS_REVISION = "needs_revision"


@dataclass
class VerifierRound:
    """One round of verification (whether accepted, rejected, or revised)."""

    round_index: int
    verdict: VerifierVerdict
    notes: str
    rechecked_evidence: Optional[dict[str, Any]] = None
    claim_under_review: Optional[dict[str, Any]] = None
    duration_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d


@dataclass
class VerifierEscalation(Exception):
    """Raised when ``max_rounds`` is exhausted without an accept verdict.

    Carries the full round history so the caller can hand it to the
    escalation tier (#approvals, human DM) untouched.
    """

    persona: str
    rounds: list[VerifierRound]
    last_claim: dict[str, Any]
    reason: str = "max_rounds exhausted"

    def __post_init__(self) -> None:
        super().__init__(
            f"verifier {self.persona!r} did not accept after {len(self.rounds)} round(s): "
            f"{self.reason}"
        )

    def history(self) -> list[dict[str, Any]]:
        return [r.as_dict() for r in self.rounds]


Verifier = Callable[[dict[str, Any], dict[str, Any]], "VerifierRound | tuple[VerifierVerdict, str, Optional[dict[str, Any]]]"]
"""Verifier callable contract.

Signature: ``verifier(claim, original_evidence) -> VerifierRound | (verdict, notes, rechecked_evidence)``.

The verifier MUST re-fetch its own evidence from the source of truth;
it must NOT trust ``original_evidence`` blindly. ``original_evidence``
is supplied only so the verifier can diff against what the builder saw.
"""

BuilderRevise = Callable[[dict[str, Any], "VerifierRound"], dict[str, Any]]
"""Builder-revise callable. ``builder_revise(claim, last_round) -> new_claim``.

Called only when the previous round returned ``NEEDS_REVISION``. The
builder may use the verifier's notes + rechecked_evidence to repair
the claim. If ``None``, ``NEEDS_REVISION`` is treated like ``REJECT``.
"""

AuditCallback = Callable[[dict[str, Any]], None]
"""Per-round audit hook. Receives a serializable record."""


def _normalize(result: Any, *, round_index: int, claim: dict[str, Any]) -> VerifierRound:
    if isinstance(result, VerifierRound):
        r = result
        if r.claim_under_review is None:
            r.claim_under_review = claim
        return r
    if isinstance(result, tuple) and 2 <= len(result) <= 3:
        verdict_raw, notes, *rest = result
        verdict = (
            verdict_raw if isinstance(verdict_raw, VerifierVerdict)
            else VerifierVerdict(str(verdict_raw))
        )
        rechecked = rest[0] if rest else None
        return VerifierRound(
            round_index=round_index,
            verdict=verdict,
            notes=str(notes),
            rechecked_evidence=rechecked,
            claim_under_review=claim,
        )
    raise TypeError(
        f"verifier must return VerifierRound or (verdict, notes[, evidence]); got {type(result)!r}"
    )


def run_verifier_loop(
    *,
    claim: dict[str, Any],
    evidence: dict[str, Any],
    verifier: Verifier,
    persona: str,
    builder_revise: Optional[BuilderRevise] = None,
    max_rounds: int = 3,
    audit: Optional[AuditCallback] = None,
    correlation_id: Optional[str] = None,
) -> tuple[dict[str, Any], list[VerifierRound]]:
    """Run ``verifier`` up to ``max_rounds`` times; return ``(accepted_claim, history)``.

    On every round, the verifier re-checks ``claim`` against the source
    of truth. On ``NEEDS_REVISION``, ``builder_revise`` is called (if
    provided) to produce a new claim for the next round. On ``REJECT``
    OR exhaustion of ``max_rounds`` without ``ACCEPT``, raises
    :class:`VerifierEscalation` so the caller hands the chain to the
    human-escalation tier (Phase 0.11) untouched.
    """
    if max_rounds < 1:
        raise ValueError("max_rounds must be >= 1")

    history: list[VerifierRound] = []
    current_claim = dict(claim)  # shallow copy; builder may mutate

    for idx in range(1, max_rounds + 1):
        started = time.monotonic()
        round_obj = _normalize(
            verifier(current_claim, evidence), round_index=idx, claim=current_claim
        )
        round_obj.duration_ms = int((time.monotonic() - started) * 1000)
        history.append(round_obj)

        if audit is not None:
            audit({
                "event": "verifier_round",
                "persona": persona,
                "correlation_id": correlation_id,
                "round": round_obj.as_dict(),
            })

        if round_obj.verdict is VerifierVerdict.ACCEPT:
            return current_claim, history
        if round_obj.verdict is VerifierVerdict.REJECT:
            raise VerifierEscalation(
                persona=persona,
                rounds=history,
                last_claim=current_claim,
                reason=f"verifier rejected: {round_obj.notes}"[:240],
            )

        # NEEDS_REVISION: invite the builder to amend the claim
        if builder_revise is None:
            raise VerifierEscalation(
                persona=persona,
                rounds=history,
                last_claim=current_claim,
                reason="verifier requested revision but no builder_revise callback supplied",
            )
        current_claim = builder_revise(current_claim, round_obj)

    raise VerifierEscalation(
        persona=persona,
        rounds=history,
        last_claim=current_claim,
    )
