"""Tamper-evident audit logs (hash-chained JSONL).

Every append writes one JSON line with these reserved fields layered on top
of the caller's payload:

- ``audit_seq``    monotonically increasing integer, starts at 1 for the
                   first chained entry in a given file
- ``audit_ts``     wall-clock time in seconds since epoch (float)
- ``audit_prev``   the previous chained entry's ``audit_hash`` (or the
                   64-zero genesis string)
- ``audit_hash``   SHA-256 of canonical-JSON(payload-without-audit_hash) +
                   ``audit_prev``, hex-encoded

Any tamper that changes a record after the fact (edit, delete, reorder)
breaks ``audit_hash`` for that record and every record after it. The
``verify`` CLI walks the file and reports the first break.

Backwards compatibility: existing un-chained JSONL files (no ``audit_hash``
field) are not rewritten. The first append after upgrade starts a fresh
chain on top; ``verify`` reports the unchained prefix count separately so
the operator knows what is and is not covered by the chain.

See `docs/plans/phase-0-platform.md` section 0.3.
"""

from .ledger import (
    AuditLog,
    AuditLogError,
    AuditRecord,
    VerifyReport,
    GENESIS_HASH,
    canonical_json,
    compute_hash,
)

__all__ = [
    "AuditLog",
    "AuditLogError",
    "AuditRecord",
    "VerifyReport",
    "GENESIS_HASH",
    "canonical_json",
    "compute_hash",
]
