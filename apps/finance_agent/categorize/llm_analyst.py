"""LLM analyst for transactions rules cannot approve (local gateway only)."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Callable

from apps._shared.rlm.subcall import SubCallInvoker, SubCallSchemaError

from .accounts import resolve_account_role, source_flow
from .accounts_chart import CATEGORIZE_ACCOUNTS
from .classifier import analyst_classify, analyst_revise
from .ledger import PendingTransaction
from .policy import CategorizePolicy

log = logging.getLogger(__name__)

FINANCE_CATEGORIZE_SKILL = "finance-categorize"
_LLM_INTENT = "classify"

# Cap LLM-reported confidence so risk still gates obvious mistakes.
_LLM_CONFIDENCE_CAP = 0.92

_CLASSIFY_SCHEMA_HINT = (
    "Put in the response `summary` field ONLY a JSON object (no markdown) with keys: "
    "proposed_category (string, must be one of the allowed accounts), "
    "confidence (number 0.0-1.0), alternatives (array of up to 3 objects with category and "
    "confidence), reason (short string citing payee/amount/flow). "
    "Set citations to [] and open_questions to []."
)


def _allowed_accounts_text() -> str:
    return "\n".join(f"- {name}" for name in CATEGORIZE_ACCOUNTS)


def _normalize_category(raw: str, policy: CategorizePolicy) -> str | None:
    cat = (raw or "").strip()
    if not cat:
        return None
    if cat in CATEGORIZE_ACCOUNTS:
        return cat
    # Model sometimes omits Expenses:/Income: prefix typos — reject, do not guess.
    if cat == policy.default_category:
        return cat
    return None


def _parse_llm_payload(summary: str) -> dict[str, Any]:
    text = (summary or "").strip()
    if not text:
        raise SubCallSchemaError("empty LLM summary")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SubCallSchemaError(f"LLM summary is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SubCallSchemaError("LLM summary JSON must be an object")
    return payload


def _llm_propose(
    txn: PendingTransaction,
    policy: CategorizePolicy,
    rules_claim: dict[str, Any],
    *,
    invoker: SubCallInvoker,
    hint: str | None = None,
) -> dict[str, Any]:
    role = resolve_account_role(txn.source_account, policy.account_roles)
    flow = source_flow(txn.source_amount)
    sub_prompt = (
        "Categorize this personal household bank transaction for Beancount.\n"
        f"{_CLASSIFY_SCHEMA_HINT}\n\n"
        f"Allowed accounts:\n{_allowed_accounts_text()}\n\n"
        f"Transaction:\n"
        f"- date: {txn.date}\n"
        f"- description: {txn.description}\n"
        f"- source_account: {txn.source_account}\n"
        f"- account_role: {role}\n"
        f"- source_flow: {flow}\n"
        f"- source_amount: {txn.source_amount}\n"
        f"- uncategorized_amount: {txn.amount}\n"
        f"- currency: {txn.currency}\n\n"
        f"Rule-based prior (may be wrong): category={rules_claim.get('proposed_category')}, "
        f"confidence={rules_claim.get('confidence')}\n"
    )
    if hint:
        sub_prompt += f"\nRisk reviewer notes (address these): {hint}\n"

    result = invoker.call(
        intent=_LLM_INTENT,
        sub_prompt=sub_prompt,
        context={"task": "finance_categorize", "date": txn.date},
        skill_id=FINANCE_CATEGORIZE_SKILL,
    )
    payload = _parse_llm_payload(result.summary)
    category = _normalize_category(str(payload.get("proposed_category", "")), policy)
    if category is None:
        raise SubCallSchemaError(
            f"LLM proposed invalid category {payload.get('proposed_category')!r}"
        )
    try:
        confidence = float(payload.get("confidence", 0))
    except (TypeError, ValueError) as exc:
        raise SubCallSchemaError(f"invalid confidence: {exc}") from exc
    confidence = max(0.0, min(_LLM_CONFIDENCE_CAP, confidence))

    alts_raw = payload.get("alternatives") or []
    alternatives: list[dict[str, Any]] = []
    if isinstance(alts_raw, list):
        for item in alts_raw[:3]:
            if not isinstance(item, dict):
                continue
            alt_cat = _normalize_category(str(item.get("category", "")), policy)
            if not alt_cat:
                continue
            try:
                alt_conf = float(item.get("confidence", 0))
            except (TypeError, ValueError):
                alt_conf = 0.0
            alternatives.append(
                {"category": alt_cat, "confidence": alt_conf, "rule_id": "llm-alt"}
            )

    reason = str(payload.get("reason", "")).strip() or result.summary[:200]
    return {
        "category": category,
        "confidence": confidence,
        "alternatives": alternatives,
        "reason": reason,
        "model": result.model,
        "route": result.route,
    }


def _claim_from_llm(
    txn: PendingTransaction,
    rules_claim: dict[str, Any],
    llm: dict[str, Any],
) -> dict[str, Any]:
    evidence = dict(rules_claim.get("evidence") or {})
    evidence.update(
        {
            "rule_kind": "llm",
            "llm_reason": llm.get("reason", ""),
            "llm_model": llm.get("model", ""),
            "rules_category": rules_claim.get("proposed_category"),
            "rules_confidence": rules_claim.get("confidence"),
        }
    )
    alts = llm.get("alternatives") or rules_claim.get("alternatives") or []
    return {
        "date": txn.date,
        "description": txn.description,
        "source_account": txn.source_account,
        "source_amount": str(txn.source_amount),
        "proposed_category": llm["category"],
        "confidence": llm["confidence"],
        "alternatives": alts,
        "evidence": evidence,
    }


def analyst_classify_llm(
    txn: PendingTransaction,
    policy: CategorizePolicy,
    *,
    invoker: SubCallInvoker | None = None,
) -> dict[str, Any]:
    """Rules first; call local LLM when confidence is below policy threshold."""
    rules_claim = analyst_classify(txn, policy)
    if float(rules_claim.get("confidence", 0)) >= policy.threshold:
        return rules_claim

    inv = invoker or SubCallInvoker()
    try:
        llm = _llm_propose(txn, policy, rules_claim, invoker=inv, hint=None)
        return _claim_from_llm(txn, rules_claim, llm)
    except (SubCallSchemaError, RuntimeError) as exc:
        log.warning("LLM classify failed for %s: %s", txn.description[:60], exc)
        fallback = dict(rules_claim)
        ev = dict(fallback.get("evidence") or {})
        ev["llm_error"] = str(exc)
        fallback["evidence"] = ev
        return fallback


def analyst_revise_llm(
    claim: dict[str, Any],
    *,
    hint: str,
    policy: CategorizePolicy,
    invoker: SubCallInvoker | None = None,
) -> dict[str, Any]:
    """Re-classify with LLM when risk asked for revision; else rule bump."""
    from .ledger import PendingTransaction

    try:
        src_amt = Decimal(str(claim.get("source_amount", "0")))
    except Exception:
        src_amt = Decimal("0")
    try:
        unc_amt = Decimal(str((claim.get("evidence") or {}).get("amount", "0")))
    except Exception:
        unc_amt = Decimal("0")
    txn = PendingTransaction(
        block_start=0,
        block_end=0,
        date=str(claim.get("date", "")),
        description=str(claim.get("description", "")),
        source_account=str(claim.get("source_account", "")),
        source_amount=src_amt,
        amount=unc_amt,
        currency=str((claim.get("evidence") or {}).get("currency", "CAD")),
        lines=(),
    )
    rules_claim = analyst_classify(txn, policy)
    inv = invoker or SubCallInvoker()
    try:
        llm = _llm_propose(txn, policy, rules_claim, invoker=inv, hint=hint)
        revised = _claim_from_llm(txn, rules_claim, llm)
    except (SubCallSchemaError, RuntimeError):
        revised = analyst_revise(claim, hint=hint, policy=policy)
        return revised

    revised["revise_hint"] = hint
    revised["confidence"] = max(
        float(revised.get("confidence", 0)),
        float(claim.get("confidence", 0)) + 0.02,
    )
    return revised


def make_llm_classify_fns(
    policy: CategorizePolicy,
    *,
    invoker: SubCallInvoker | None = None,
) -> tuple[
    Callable[[PendingTransaction, CategorizePolicy], dict[str, Any]],
    Callable[..., dict[str, Any]],
]:
    """Return (classify, revise) callables bound to one invoker instance."""
    inv = invoker or SubCallInvoker()

    def classify(txn: PendingTransaction, pol: CategorizePolicy) -> dict[str, Any]:
        return analyst_classify_llm(txn, pol, invoker=inv)

    def revise(
        claim: dict[str, Any], *, hint: str, policy: CategorizePolicy
    ) -> dict[str, Any]:
        return analyst_revise_llm(claim, hint=hint, policy=policy, invoker=inv)

    return classify, revise
