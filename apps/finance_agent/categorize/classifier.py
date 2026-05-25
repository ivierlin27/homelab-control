"""Analyst-side classifier (rule-based MVP; smart_importer hook later)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .accounts import resolve_account_role, source_flow
from .ledger import PendingTransaction
from .policy import CategorizePolicy, CategoryRule, ContextRule


def _flow_matches(rule_flow: str, txn_flow: str) -> bool:
    return rule_flow == "any" or rule_flow == txn_flow


def _context_rule_matches(rule: ContextRule, txn: PendingTransaction, role: str) -> bool:
    if not rule.description_pattern.search(txn.description):
        return False
    if rule.source_roles and role not in rule.source_roles:
        return False
    txn_flow = source_flow(txn.source_amount)
    if not _flow_matches(rule.flow, txn_flow):
        return False
    if rule.counterparty_pattern and not rule.counterparty_pattern.search(txn.description):
        return False
    return True


def _rank_context_rules(
    policy: CategorizePolicy, txn: PendingTransaction
) -> list[tuple[str, float, str | None, str]]:
    """Return (category, confidence, rule_id, reason) sorted by confidence desc."""
    role = resolve_account_role(txn.source_account, policy.account_roles)
    hits: list[tuple[str, float, str | None, str]] = []
    for rule in policy.context_rules:
        if _context_rule_matches(rule, txn, role):
            hits.append((rule.category, rule.confidence, rule.id, rule.reason))
    hits.sort(key=lambda x: x[1], reverse=True)
    return hits


def _rank_description_rules(
    policy: CategorizePolicy, description: str
) -> list[tuple[str, float, str | None]]:
    """Return (category, confidence, rule_id) sorted by confidence desc."""
    hits: list[tuple[str, float, str | None]] = []
    for rule in policy.rules:
        if rule.pattern.search(description):
            hits.append((rule.category, rule.confidence, rule.id))
    hits.sort(key=lambda x: x[1], reverse=True)
    return hits


def _best_classification(
    policy: CategorizePolicy, txn: PendingTransaction
) -> tuple[str, float, str | None, dict[str, Any]]:
    role = resolve_account_role(txn.source_account, policy.account_roles)
    flow = source_flow(txn.source_amount)
    ctx_ranked = _rank_context_rules(policy, txn)
    if ctx_ranked:
        category, confidence, rule_id, reason = ctx_ranked[0]
        extras = {
            "account_role": role,
            "source_flow": flow,
            "rule_kind": "context",
            "reason": reason,
        }
        alts = [
            {"category": c, "confidence": conf, "rule_id": rid, "reason": r}
            for c, conf, rid, r in ctx_ranked[1:4]
        ]
        return category, confidence, rule_id, {**extras, "alternatives": alts}

    desc_ranked = _rank_description_rules(policy, txn.description)
    if desc_ranked:
        category, confidence, rule_id = desc_ranked[0]
        return category, confidence, rule_id, {
            "account_role": role,
            "source_flow": flow,
            "rule_kind": "description",
        }

    return (
        policy.default_category,
        policy.default_confidence,
        None,
        {"account_role": role, "source_flow": flow, "rule_kind": "default"},
    )


def analyst_classify(txn: PendingTransaction, policy: CategorizePolicy) -> dict[str, Any]:
    """Build a verifier-loop claim from one pending transaction."""
    category, confidence, rule_id, meta = _best_classification(policy, txn)
    alternatives = list(meta.pop("alternatives", []))
    if not alternatives:
        desc_ranked = _rank_description_rules(policy, txn.description)
        ctx_ranked = _rank_context_rules(policy, txn)
        pool = [
            {"category": c, "confidence": conf, "rule_id": rid}
            for c, conf, rid, _ in ctx_ranked[1:3]
        ] + [
            {"category": c, "confidence": conf, "rule_id": rid}
            for c, conf, rid in desc_ranked[:2]
        ]
        alternatives = pool[:3]

    evidence = {
        "description": txn.description,
        "source_account": txn.source_account,
        "source_amount": str(txn.source_amount),
        "amount": str(txn.amount),
        "currency": txn.currency,
        "matched_rule_id": rule_id,
        **meta,
    }
    return {
        "date": txn.date,
        "description": txn.description,
        "source_account": txn.source_account,
        "source_amount": str(txn.source_amount),
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
    # Re-run classification from txn fields embedded in claim.
    from .ledger import PendingTransaction

    try:
        src_amt = Decimal(str(revised.get("source_amount", "0")))
    except Exception:
        src_amt = Decimal("0")
    try:
        unc_amt = Decimal(str((revised.get("evidence") or {}).get("amount", "0")))
    except Exception:
        unc_amt = Decimal("0")
    txn = PendingTransaction(
        block_start=0,
        block_end=0,
        date=str(revised.get("date", "")),
        description=str(revised.get("description", "")),
        source_account=str(revised.get("source_account", "")),
        source_amount=src_amt,
        amount=unc_amt,
        currency=str((revised.get("evidence") or {}).get("currency", "CAD")),
        lines=(),
    )
    category, confidence, rule_id, meta = _best_classification(policy, txn)
    revised["proposed_category"] = category
    revised["confidence"] = max(float(revised["confidence"]), confidence)
    revised["evidence"] = {
        **dict(revised.get("evidence") or {}),
        "matched_rule_id": rule_id,
        "revise_applied": True,
        **meta,
    }
    return revised
