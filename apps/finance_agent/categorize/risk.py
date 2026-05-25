"""Risk persona verifier for categorization claims."""

from __future__ import annotations

from typing import Any

from apps._shared.verifier import VerifierRound, VerifierVerdict

from .ledger import UNCATEGORIZED
from .policy import CategorizePolicy


def risk_verify(
    claim: dict[str, Any],
    original_evidence: dict[str, Any],
    *,
    policy: CategorizePolicy,
) -> VerifierRound:
    """Re-check claim; do not trust builder evidence alone."""
    category = str(claim.get("proposed_category", "")).strip()
    confidence = float(claim.get("confidence", 0))
    desc = str(claim.get("description", original_evidence.get("description", "")))

    rechecked = {
        "description": desc,
        "source_account": claim.get("source_account"),
        "category": category,
        "confidence": confidence,
    }

    if not category or category == UNCATEGORIZED:
        return VerifierRound(
            round_index=0,
            verdict=VerifierVerdict.REJECT,
            notes="proposed category is missing or still uncategorized",
            rechecked_evidence=rechecked,
            claim_under_review=claim,
        )

    if not (
        category.startswith("Expenses:")
        or category.startswith("Income:")
        or category.startswith("Liabilities:")
        or category.startswith("Equity:")
    ):
        return VerifierRound(
            round_index=0,
            verdict=VerifierVerdict.REJECT,
            notes=f"category {category!r} is not a valid posting account prefix",
            rechecked_evidence=rechecked,
            claim_under_review=claim,
        )

    if confidence < policy.threshold:
        return VerifierRound(
            round_index=0,
            verdict=VerifierVerdict.NEEDS_REVISION,
            notes=(
                f"confidence {confidence:.3f} below threshold {policy.threshold:.3f}; "
                "revise or defer to human"
            ),
            rechecked_evidence=rechecked,
            claim_under_review=claim,
        )

    if not desc.strip():
        return VerifierRound(
            round_index=0,
            verdict=VerifierVerdict.REJECT,
            notes="transaction description is empty",
            rechecked_evidence=rechecked,
            claim_under_review=claim,
        )

    return VerifierRound(
        round_index=0,
        verdict=VerifierVerdict.ACCEPT,
        notes=f"accepted {category} at confidence {confidence:.3f}",
        rechecked_evidence=rechecked,
        claim_under_review=claim,
    )
