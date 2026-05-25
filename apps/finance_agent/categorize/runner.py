"""Orchestrate categorize batch: classify → verify → ledger rewrite."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from apps._shared.audit import AuditLog
from apps._shared.verifier import VerifierEscalation, VerifierVerdict, run_verifier_loop

from ..ingest import MAIN_FILENAME, TRANSACTIONS_FILENAME

from .accounts_chart import ensure_accounts_open
from .classifier import analyst_classify, analyst_revise
from .llm_analyst import make_llm_classify_fns
from .ledger import PendingTransaction, apply_category, find_pending, rewrite_blocks
from .policy import CategorizePolicy, load_policy
from .risk import risk_verify


class CategorizeError(Exception):
    """Raised when categorization cannot proceed."""


@dataclass
class EntryOutcome:
    date: str
    description: str
    status: str  # approved | deferred | failed
    category: str = ""
    confidence: float = 0.0
    reason: str = ""
    verifier_rounds: int = 0


@dataclass
class CategorizeBatchResult:
    correlation_id: str
    scanned: int
    approved: int
    deferred: int
    failed: int
    outcomes: list[EntryOutcome] = field(default_factory=list)
    ledger_path: str = ""
    audit_path: str = ""
    bean_check_ran: bool = False
    bean_check_passed: bool | None = None
    bean_check_message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "scanned": self.scanned,
            "approved": self.approved,
            "deferred": self.deferred,
            "failed": self.failed,
            "outcomes": [entry.__dict__ for entry in self.outcomes],
            "ledger_path": self.ledger_path,
            "audit_path": self.audit_path,
            "bean_check_ran": self.bean_check_ran,
            "bean_check_passed": self.bean_check_passed,
            "bean_check_message": self.bean_check_message,
        }


def _maybe_bean_check(main_path: Path, *, run: bool) -> tuple[bool, bool | None, str]:
    if not run:
        return False, None, "skipped"
    if not shutil.which("bean-check"):
        return False, None, "bean-check not on PATH"
    proc = subprocess.run(
        ["bean-check", str(main_path.expanduser())],
        capture_output=True,
        text=True,
        check=False,
    )
    ok = proc.returncode == 0
    msg = (proc.stdout or proc.stderr or "").strip()[:500]
    return True, ok, msg or ("ok" if ok else f"exit {proc.returncode}")


def categorize_pending(
    *,
    ledger_dir: Path,
    audit_path: Path,
    policy_path: Path | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    run_bean_check: bool = True,
    correlation_id: str | None = None,
    use_llm: bool = False,
) -> CategorizeBatchResult:
    """Categorize pending (!) uncategorized transactions in the ledger."""
    ledger_dir = Path(ledger_dir).expanduser()
    transactions_path = ledger_dir / TRANSACTIONS_FILENAME
    main_path = ledger_dir / MAIN_FILENAME
    if not main_path.is_file():
        raise CategorizeError(f"ledger not initialized: {main_path} missing")

    accounts_path = ledger_dir / "accounts.beancount"
    added_accounts = ensure_accounts_open(accounts_path)
    if added_accounts:
        import sys

        print(
            f"categorize: opened {len(added_accounts)} new accounts in {accounts_path}",
            file=sys.stderr,
        )

    policy = load_policy(policy_path)
    corr = correlation_id or str(uuid.uuid4())
    pending = find_pending(transactions_path, limit=limit)

    classify_fn = analyst_classify
    revise_fn = analyst_revise
    if use_llm:
        classify_fn, revise_fn = make_llm_classify_fns(policy)

    outcomes: list[EntryOutcome] = []
    replacements: list[tuple[PendingTransaction, list[str]]] = []
    audit_rows: list[dict[str, Any]] = []

    def audit_cb(row: dict[str, Any]) -> None:
        audit_rows.append(row)

    for i, txn in enumerate(pending, start=1):
        if use_llm and i % 25 == 1:
            import sys

            print(
                f"categorize-llm: {i}/{len(pending)} …",
                file=sys.stderr,
                flush=True,
            )
        claim = classify_fn(txn, policy)
        evidence = dict(claim.get("evidence") or {})

        def verifier(c: dict[str, Any], ev: dict[str, Any]) -> Any:
            return risk_verify(c, ev, policy=policy)

        def builder_revise(c: dict[str, Any], last_round: Any) -> dict[str, Any]:
            return revise_fn(c, hint=last_round.notes, policy=policy)

        try:
            accepted, history = run_verifier_loop(
                claim=claim,
                evidence=evidence,
                verifier=verifier,
                persona="finance-risk",
                builder_revise=builder_revise,
                max_rounds=policy.max_verifier_rounds,
                audit=audit_cb,
                correlation_id=corr,
            )
            category = str(accepted.get("proposed_category", ""))
            confidence = float(accepted.get("confidence", 0))
            new_lines = apply_category(
                txn,
                category=category,
                confidence=confidence,
                correlation_id=corr,
            )
            replacements.append((txn, new_lines))
            outcomes.append(
                EntryOutcome(
                    date=txn.date,
                    description=txn.description,
                    status="approved",
                    category=category,
                    confidence=confidence,
                    verifier_rounds=len(history),
                )
            )
        except VerifierEscalation as exc:
            outcomes.append(
                EntryOutcome(
                    date=txn.date,
                    description=txn.description,
                    status="deferred",
                    category=str(claim.get("proposed_category", "")),
                    confidence=float(claim.get("confidence", 0)),
                    reason=exc.reason,
                    verifier_rounds=len(exc.rounds),
                )
            )

    approved = sum(1 for o in outcomes if o.status == "approved")
    deferred = sum(1 for o in outcomes if o.status == "deferred")
    failed = sum(1 for o in outcomes if o.status == "failed")

    bean_ran, bean_ok, bean_msg = False, None, "skipped (dry-run)"
    if replacements and not dry_run:
        original = transactions_path.read_text(encoding="utf-8")
        rewrite_blocks(transactions_path, replacements)
        bean_ran, bean_ok, bean_msg = _maybe_bean_check(main_path, run=run_bean_check)
        if bean_ran and bean_ok is False:
            transactions_path.write_text(original, encoding="utf-8")
            raise CategorizeError(f"bean-check failed after categorize: {bean_msg}")

    if not dry_run:
        audit = AuditLog(audit_path)
        audit.append(
            {
                "event": "finance_categorize",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "correlation_id": corr,
                "scanned": len(pending),
                "approved": approved,
                "deferred": deferred,
                "failed": failed,
                "dry_run": dry_run,
                "use_llm": use_llm,
                "bean_check_passed": bean_ok,
            }
        )

    return CategorizeBatchResult(
        correlation_id=corr,
        scanned=len(pending),
        approved=approved,
        deferred=deferred,
        failed=failed,
        outcomes=outcomes,
        ledger_path=str(transactions_path),
        audit_path=str(audit_path),
        bean_check_ran=bean_ran,
        bean_check_passed=bean_ok,
        bean_check_message=bean_msg,
    )
