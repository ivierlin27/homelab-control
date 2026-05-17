"""Hash-chained append-only JSONL log.

Concurrency model: each ``append`` takes an exclusive ``fcntl.flock`` on the
log file for the duration of (read-tail → compute-hash → append → fsync).
This is safe across processes on the same host. Cross-host concurrent
writes to the same file are out of scope; ledgers are per-agent and live
on the host that runs that agent.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

GENESIS_HASH = "0" * 64

# Reserved keys we layer onto every chained record. Caller payloads must not
# collide with these — appending checks and raises if they do.
RESERVED_KEYS: frozenset[str] = frozenset({"audit_seq", "audit_ts", "audit_prev", "audit_hash"})


class AuditLogError(Exception):
    """Raised on schema, IO, or invariant violations."""


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Stable JSON: sorted keys, no whitespace, UTF-8 escapes preserved."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(payload_without_audit_hash: Mapping[str, Any], prev_hash: str) -> str:
    """SHA-256 of canonical-JSON(payload) concatenated with prev_hash, hex.

    ``payload_without_audit_hash`` must include the caller's payload **plus**
    the ``audit_seq``, ``audit_ts``, and ``audit_prev`` fields. ``audit_hash``
    itself is, of course, omitted (it's the output).
    """
    if "audit_hash" in payload_without_audit_hash:
        raise AuditLogError("compute_hash: payload must not contain 'audit_hash'")
    h = hashlib.sha256()
    h.update(canonical_json(payload_without_audit_hash).encode("utf-8"))
    h.update(prev_hash.encode("ascii"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditRecord:
    """One chained entry as parsed from the log."""

    seq: int
    ts: float
    prev_hash: str
    entry_hash: str
    payload: dict
    raw_line: str
    line_number: int  # 1-based, includes any legacy unchained prefix

    @property
    def is_chained(self) -> bool:
        return True


@dataclass(frozen=True)
class VerifyReport:
    """Outcome of ``AuditLog.verify_chain``."""

    path: Path
    total_lines: int
    legacy_prefix_lines: int        # unchained lines at the head (pre-0.3 history)
    chained_lines: int              # lines with audit_hash
    first_break_line: int | None    # 1-based line number of first failed chain link
    head_hash: str | None           # hash of the last chained entry, or None
    error: str | None = None        # human-readable description of any break

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        if self.ok:
            return (
                f"ok: {self.path} — {self.chained_lines} chained "
                f"({self.legacy_prefix_lines} legacy prefix); head={self.head_hash[:12] if self.head_hash else '-'}"
            )
        return (
            f"FAIL: {self.path} line {self.first_break_line}: {self.error}"
        )


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------


@dataclass
class AuditLog:
    """Append-only hash-chained JSONL file."""

    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path).expanduser()

    # ------------------------------------------------------------------
    # append
    # ------------------------------------------------------------------

    def append(self, payload: Mapping[str, Any], *, ts: float | None = None) -> AuditRecord:
        """Append one record, returning the chained ``AuditRecord``.

        Raises ``AuditLogError`` if the payload contains a reserved key.
        """
        collisions = sorted(set(payload.keys()) & RESERVED_KEYS)
        if collisions:
            raise AuditLogError(
                f"payload uses reserved key(s) {collisions}; rename in caller"
            )
        payload_dict = dict(payload)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Open for read+append; create if missing.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            prev_hash, prev_seq = _tail_chain_state(fd)
            seq = prev_seq + 1
            stamp = float(ts) if ts is not None else time.time()
            chained = {
                **payload_dict,
                "audit_seq": seq,
                "audit_ts": stamp,
                "audit_prev": prev_hash,
            }
            entry_hash = compute_hash(chained, prev_hash)
            chained["audit_hash"] = entry_hash
            line = canonical_json(chained) + "\n"
            # Seek to end, write, fsync.
            os.lseek(fd, 0, os.SEEK_END)
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

        return AuditRecord(
            seq=seq,
            ts=stamp,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            payload=payload_dict,
            raw_line=line.rstrip("\n"),
            line_number=-1,  # unknown without re-reading; callers usually don't need it
        )

    # ------------------------------------------------------------------
    # read / verify
    # ------------------------------------------------------------------

    def iter_records(self) -> Iterator[AuditRecord | dict]:
        """Yield every line as either an ``AuditRecord`` (chained) or a dict (legacy)."""
        if not self.path.is_file():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                stripped = raw.rstrip("\n")
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise AuditLogError(
                        f"{self.path} line {line_no}: invalid JSON: {exc}"
                    ) from exc
                if not isinstance(obj, dict):
                    raise AuditLogError(
                        f"{self.path} line {line_no}: top-level value must be an object"
                    )
                if "audit_hash" in obj:
                    yield AuditRecord(
                        seq=obj.get("audit_seq", 0),
                        ts=obj.get("audit_ts", 0.0),
                        prev_hash=obj.get("audit_prev", ""),
                        entry_hash=obj["audit_hash"],
                        payload={k: v for k, v in obj.items() if k not in RESERVED_KEYS},
                        raw_line=stripped,
                        line_number=line_no,
                    )
                else:
                    yield obj

    def verify_chain(self) -> VerifyReport:
        """Walk the file, reporting any break in the chain."""
        if not self.path.is_file():
            return VerifyReport(
                path=self.path, total_lines=0, legacy_prefix_lines=0,
                chained_lines=0, first_break_line=None, head_hash=None,
            )
        legacy = 0
        chained = 0
        prev_hash = GENESIS_HASH
        prev_seq = 0
        total = 0
        head = None
        seen_chained = False
        for item in self.iter_records():
            total += 1
            if isinstance(item, dict):
                # legacy line
                if seen_chained:
                    return VerifyReport(
                        path=self.path,
                        total_lines=total,
                        legacy_prefix_lines=legacy,
                        chained_lines=chained,
                        first_break_line=total,
                        head_hash=head,
                        error="legacy unchained line appeared after chained entries",
                    )
                legacy += 1
                continue
            # chained
            seen_chained = True
            chained += 1
            line_no = item.line_number
            if item.seq != prev_seq + 1:
                return VerifyReport(
                    path=self.path, total_lines=total, legacy_prefix_lines=legacy,
                    chained_lines=chained, first_break_line=line_no, head_hash=head,
                    error=f"audit_seq jumped: expected {prev_seq + 1}, got {item.seq}",
                )
            if item.prev_hash != prev_hash:
                return VerifyReport(
                    path=self.path, total_lines=total, legacy_prefix_lines=legacy,
                    chained_lines=chained, first_break_line=line_no, head_hash=head,
                    error=f"audit_prev mismatch: expected {prev_hash[:12]}..., got {item.prev_hash[:12]}...",
                )
            # recompute hash
            payload_full = {
                **item.payload,
                "audit_seq": item.seq,
                "audit_ts": item.ts,
                "audit_prev": item.prev_hash,
            }
            expected = compute_hash(payload_full, item.prev_hash)
            if expected != item.entry_hash:
                return VerifyReport(
                    path=self.path, total_lines=total, legacy_prefix_lines=legacy,
                    chained_lines=chained, first_break_line=line_no, head_hash=head,
                    error=f"audit_hash mismatch: payload tampered (expected {expected[:12]}..., got {item.entry_hash[:12]}...)",
                )
            prev_hash = item.entry_hash
            prev_seq = item.seq
            head = item.entry_hash
        return VerifyReport(
            path=self.path, total_lines=total, legacy_prefix_lines=legacy,
            chained_lines=chained, first_break_line=None, head_hash=head,
        )

    # ------------------------------------------------------------------
    # anchor
    # ------------------------------------------------------------------

    def anchor(self, anchor_path: Path, *, note: str = "") -> dict:
        """Emit the current chain head to ``anchor_path`` as one JSON line.

        Anchor files are themselves checked into git (or otherwise pushed to an
        external trust store) on a cadence, providing an external pin that
        makes silent in-place rewrites of the ledger detectable.
        """
        report = self.verify_chain()
        if not report.ok:
            raise AuditLogError(
                f"refusing to anchor a broken ledger: {report.summary()}"
            )
        anchor_path = Path(anchor_path).expanduser()
        anchor_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "ledger_path": str(self.path),
            "head_hash": report.head_hash,
            "chained_lines": report.chained_lines,
            "legacy_prefix_lines": report.legacy_prefix_lines,
            "note": note,
        }
        with anchor_path.open("a", encoding="utf-8") as fh:
            fh.write(canonical_json(record) + "\n")
        return record


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _tail_chain_state(fd: int) -> tuple[str, int]:
    """Find the latest ``audit_hash`` and ``audit_seq`` in the open file.

    Reads the file from the start; OK for our scale (kB–MB per ledger per
    day). If/when we need to optimize this, swap for a reverse byte scan.
    """
    size = os.fstat(fd).st_size
    if size == 0:
        return GENESIS_HASH, 0
    os.lseek(fd, 0, os.SEEK_SET)
    data = b""
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        data += chunk
    prev_hash = GENESIS_HASH
    prev_seq = 0
    for raw in data.splitlines():
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AuditLogError(f"corrupt JSONL in audit log: {exc}") from exc
        if not isinstance(obj, dict):
            raise AuditLogError("audit log line is not a JSON object")
        if "audit_hash" in obj:
            prev_hash = obj["audit_hash"]
            prev_seq = obj.get("audit_seq", prev_seq)
    return prev_hash, prev_seq
