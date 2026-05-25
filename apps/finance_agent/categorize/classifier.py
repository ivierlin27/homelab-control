"""Analyst-side classifier (rule-based MVP; smart_importer hook later)."""

from __future__ import annotations

from typing import Any

from .ledger import PendingTransaction
from .policy import CategorizePolicy, CategoryRule


def _rank_alternatives(
    policy: CategorizePolicy, description: str
) -> list[tuple[str, float, str | None]]:
    """Return (category, confidence, rule_id) sorted by confidence desc."""
    hits: list[tuple[str, float, str | None]] = []
    for rule in policy.rules:
        if rule.pattern.search(description):
            hits.append((rule.category, rule.confidence, rule.id))
    if not hits:
        hits.append((policy.default_category, policy.default_confidence, None))
    hits.sort(key=lambda x: x[1], reverse=True)
    return hits


def analyst_classify(txn: PendingTransaction, policy: CategorizePolicy) -> dict[str, Any]:
    """Build a verifier-loop claim from one pending transaction."""
    ranked = _rank_alternatives(policy, txn.description)
    category, confidence, rule_id = ranked[0]
    alternatives = [
        {"category": c, "confidence": conf, "rule_id": rid}
        for c, conf, rid in ranked[1:4]
    ]
    evidence = {
        "description": txn.description,
        "source_account": txn.source_account,
        "amount": str(txn.amount),
        "currency": txn.currency,
        "matched_rule_id": rule_id,
    }
    return {
        "date": txn.date,
        "description": txn.description,
        "source_account": txn.source_account,
        "proposed_category": category,
        "confidence": confidence,
        "alternatives": alternatives,
        "evidence": evidence,
    }


def analyst_revise(
    claim: dict[str, Any],
    *,
    hint: str,
    policy: CategorizePolicy,
) -> dict[str, Any]:
    """One re-round: bump confidence slightly when risk asked for revision."""
    revised = dict(claim)
    conf = float(revised.get("confidence", 0))
    revised["confidence"] = min(1.0, conf + 0.05)
    revised["revise_hint"] = hint
    # If hint mentions a known rule keyword, prefer the highest matching rule.
    desc = str(revised.get("description", ""))
    ranked = _rank_alternatives(policy, desc)
    if ranked:
        revised["proposed_category"] = ranked[0][0]
        revised["confidence"] = max(float(revised["confidence"]), ranked[0][1])
        revised["evidence"] = {
            **dict(revised.get("evidence") or {}),
            "matched_rule_id": ranked[0][2],
            "revise_applied": True,
        }
    return revised
