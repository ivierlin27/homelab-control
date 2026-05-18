"""Generic verifier-loop primitive (Phase 0.4).

Pattern: a *builder* makes a claim (a structured proposal) based on some
*evidence* it gathered. A *verifier* — a separate, persona-locked
function — re-fetches the evidence from the source of truth and decides
whether the claim still holds. If not, the builder may be invited to
revise; the loop runs at most ``max_rounds`` times before escalating.

The primitive is deliberately tiny:

- It does not know anything domain-specific. The caller supplies the
  ``verifier`` callback (and optionally ``builder_revise``).
- It treats the verifier as un-promptable: the caller passes in a
  ``persona`` string that gets recorded on every audit row; the
  primitive never feeds builder-side content back into the verifier as
  instructions.
- It audits every round into a per-task ``VerifierAuditLog`` chained
  under the existing hash-chained audit ledger.

First production consumers (Phase 2): ``agent:homelab-maintainer`` for
service-upgrade recommendations (verifier re-queries the container
registry); ``agent:finance`` (Phase 1) for any advice turn (verifier
re-fetches the ledger snapshot the advice was conditioned on).
"""

from .loop import (
    VerifierEscalation,
    VerifierRound,
    VerifierVerdict,
    run_verifier_loop,
)

__all__ = [
    "VerifierEscalation",
    "VerifierRound",
    "VerifierVerdict",
    "run_verifier_loop",
]
