"""Tests for the hash-chained audit ledger."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from .ledger import (
    GENESIS_HASH,
    AuditLog,
    AuditLogError,
    canonical_json,
    compute_hash,
)


# ---------------------------------------------------------------------------
# basic append + verify
# ---------------------------------------------------------------------------


def test_append_creates_file_and_first_entry_links_to_genesis(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "trust-ledger.jsonl")
    rec = log.append({"principal": "agent:foo", "event": "started"})
    assert rec.seq == 1
    assert rec.prev_hash == GENESIS_HASH
    assert rec.entry_hash != GENESIS_HASH
    # File contents are parseable
    lines = log.path.read_text().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["principal"] == "agent:foo"
    assert obj["audit_seq"] == 1
    assert obj["audit_prev"] == GENESIS_HASH


def test_chain_links_each_append(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "x.jsonl")
    r1 = log.append({"k": "a"})
    r2 = log.append({"k": "b"})
    r3 = log.append({"k": "c"})
    assert r2.prev_hash == r1.entry_hash
    assert r3.prev_hash == r2.entry_hash
    assert r1.seq == 1 and r2.seq == 2 and r3.seq == 3
    assert log.verify_chain().ok


def test_verify_empty_file_is_ok(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "empty.jsonl")
    report = log.verify_chain()
    assert report.ok
    assert report.total_lines == 0


def test_reserved_key_rejected(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "x.jsonl")
    with pytest.raises(AuditLogError, match="reserved key"):
        log.append({"event": "x", "audit_hash": "spoof"})


# ---------------------------------------------------------------------------
# tampering detection
# ---------------------------------------------------------------------------


def test_tamper_in_middle_line_breaks_chain(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "t.jsonl")
    log.append({"k": "a"})
    log.append({"k": "b"})
    log.append({"k": "c"})
    lines = log.path.read_text().splitlines()
    # Tamper line 2: change "b" -> "B" (keep audit_* fields intact)
    obj = json.loads(lines[1])
    obj["k"] = "B"
    lines[1] = json.dumps(obj, sort_keys=True)
    log.path.write_text("\n".join(lines) + "\n")
    report = log.verify_chain()
    assert not report.ok
    assert report.first_break_line == 2
    assert "tampered" in report.error


def test_dropped_line_breaks_chain(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "t.jsonl")
    log.append({"k": "a"})
    log.append({"k": "b"})
    log.append({"k": "c"})
    lines = log.path.read_text().splitlines()
    # Drop line 2
    log.path.write_text(lines[0] + "\n" + lines[2] + "\n")
    report = log.verify_chain()
    assert not report.ok
    assert report.first_break_line == 2
    # Detected as seq jump (expected 2, got 3) or prev_hash mismatch — either is a valid signal.
    assert "seq" in report.error or "prev" in report.error


def test_reordered_lines_break_chain(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "t.jsonl")
    log.append({"k": "a"})
    log.append({"k": "b"})
    log.append({"k": "c"})
    lines = log.path.read_text().splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    log.path.write_text("\n".join(lines) + "\n")
    report = log.verify_chain()
    assert not report.ok


# ---------------------------------------------------------------------------
# legacy (un-chained) prefix
# ---------------------------------------------------------------------------


def test_legacy_prefix_preserved_and_chain_starts_fresh(tmp_path: Path) -> None:
    p = tmp_path / "legacy.jsonl"
    # Two pre-existing un-chained lines (older world)
    with p.open("w") as fh:
        fh.write(json.dumps({"ts": 1.0, "principal": "agent:exec", "event": "old1"}) + "\n")
        fh.write(json.dumps({"ts": 2.0, "principal": "agent:exec", "event": "old2"}) + "\n")
    log = AuditLog(p)
    r1 = log.append({"event": "new1"})
    r2 = log.append({"event": "new2"})
    assert r1.seq == 1
    assert r1.prev_hash == GENESIS_HASH  # chain starts fresh
    assert r2.prev_hash == r1.entry_hash
    report = log.verify_chain()
    assert report.ok
    assert report.legacy_prefix_lines == 2
    assert report.chained_lines == 2
    assert report.total_lines == 4


def test_legacy_after_chained_is_a_break(tmp_path: Path) -> None:
    p = tmp_path / "mix.jsonl"
    log = AuditLog(p)
    log.append({"event": "a"})
    log.append({"event": "b"})
    # Manually append a legacy (unchained) line after the chained entries — operator error.
    with p.open("a") as fh:
        fh.write(json.dumps({"event": "legacy-after"}) + "\n")
    report = log.verify_chain()
    assert not report.ok
    assert "legacy" in report.error


# ---------------------------------------------------------------------------
# anchor
# ---------------------------------------------------------------------------


def test_anchor_writes_head(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "x.jsonl")
    log.append({"k": "a"})
    log.append({"k": "b"})
    anchor = tmp_path / "anchor.jsonl"
    rec = log.anchor(anchor, note="daily smoke")
    assert anchor.is_file()
    line = anchor.read_text().strip()
    obj = json.loads(line)
    assert obj["head_hash"] == rec["head_hash"]
    assert obj["chained_lines"] == 2
    assert obj["note"] == "daily smoke"


def test_anchor_refuses_broken_chain(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "t.jsonl")
    log.append({"k": "a"})
    log.append({"k": "b"})
    # Tamper
    lines = log.path.read_text().splitlines()
    obj = json.loads(lines[0])
    obj["k"] = "TAMPER"
    lines[0] = json.dumps(obj, sort_keys=True)
    log.path.write_text("\n".join(lines) + "\n")
    with pytest.raises(AuditLogError, match="broken"):
        log.anchor(tmp_path / "anchor.jsonl")


# ---------------------------------------------------------------------------
# concurrency: serialized appends from many threads stay coherent
# ---------------------------------------------------------------------------


def test_concurrent_appends_serialize_correctly(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "c.jsonl")
    N_THREADS = 8
    PER_THREAD = 25

    def worker(tid: int) -> None:
        for i in range(PER_THREAD):
            log.append({"tid": tid, "i": i})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    report = log.verify_chain()
    assert report.ok, report.error
    assert report.chained_lines == N_THREADS * PER_THREAD


# ---------------------------------------------------------------------------
# hash function: known answer
# ---------------------------------------------------------------------------


def test_compute_hash_is_deterministic_and_order_independent_for_keys(tmp_path: Path) -> None:
    h1 = compute_hash({"a": 1, "b": 2, "audit_seq": 1, "audit_ts": 0.0, "audit_prev": GENESIS_HASH}, GENESIS_HASH)
    h2 = compute_hash({"b": 2, "a": 1, "audit_seq": 1, "audit_ts": 0.0, "audit_prev": GENESIS_HASH}, GENESIS_HASH)
    assert h1 == h2
    # Sanity: differs from a different payload
    h3 = compute_hash({"a": 1, "b": 3, "audit_seq": 1, "audit_ts": 0.0, "audit_prev": GENESIS_HASH}, GENESIS_HASH)
    assert h3 != h1
