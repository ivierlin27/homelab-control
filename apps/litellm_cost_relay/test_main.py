"""Tests for the LiteLLM cost JSONL relay."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from . import main as relay_main
from .main import (
    RelayConfig,
    read_batch,
    read_offset,
    run_loop,
    write_offset,
)


def _write_records(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _config(tmp_path: Path, **overrides: Any) -> RelayConfig:
    defaults = {
        "jsonl_path": tmp_path / "calls.jsonl",
        "state_path": tmp_path / ".offset",
        "url": None,
        "interval_s": 0.0,
        "batch": 100,
        "timeout_s": 5.0,
    }
    defaults.update(overrides)
    return RelayConfig(**defaults)


def test_offset_roundtrip(tmp_path: Path):
    state = tmp_path / "offset"
    assert read_offset(state) == 0
    write_offset(state, 12345)
    assert read_offset(state) == 12345


def test_read_batch_returns_records_and_new_offset(tmp_path: Path):
    src = tmp_path / "calls.jsonl"
    _write_records(src, [{"i": 0}, {"i": 1}, {"i": 2}])
    records, new_offset = read_batch(src, 0, batch=10)
    assert [r["i"] for r in records] == [0, 1, 2]
    assert new_offset == src.stat().st_size


def test_read_batch_resumes_from_offset(tmp_path: Path):
    src = tmp_path / "calls.jsonl"
    _write_records(src, [{"i": 0}, {"i": 1}])
    _, mid = read_batch(src, 0, batch=10)
    _write_records(src, [{"i": 2}])
    more, _ = read_batch(src, mid, batch=10)
    assert [r["i"] for r in more] == [2]


def test_read_batch_honors_batch_size(tmp_path: Path):
    src = tmp_path / "calls.jsonl"
    _write_records(src, [{"i": n} for n in range(5)])
    records, new_offset = read_batch(src, 0, batch=2)
    assert [r["i"] for r in records] == [0, 1]
    assert new_offset < src.stat().st_size


def test_read_batch_stops_at_partial_trailing_line(tmp_path: Path):
    """Mid-write lines (no trailing newline) must not be consumed; the
    relay leaves the cursor at the end of the last fully-flushed line."""
    src = tmp_path / "calls.jsonl"
    _write_records(src, [{"i": 0}])
    end_of_complete = src.stat().st_size
    with src.open("a", encoding="utf-8") as fh:
        fh.write('{"i": 1, "in_pro')  # writer still flushing
    records, new_offset = read_batch(src, 0, batch=10)
    assert [r["i"] for r in records] == [0]
    assert new_offset == end_of_complete


def test_read_batch_resets_when_file_shrinks(tmp_path: Path):
    src = tmp_path / "calls.jsonl"
    _write_records(src, [{"i": 0}, {"i": 1}])
    _, mid = read_batch(src, 0, batch=10)
    src.unlink()
    _write_records(src, [{"i": 99}])
    records, _ = read_batch(src, mid, batch=10)
    assert [r["i"] for r in records] == [99]


def test_dry_run_advances_offset_without_posting(tmp_path: Path):
    src = tmp_path / "calls.jsonl"
    _write_records(src, [{"i": 0}, {"i": 1}, {"i": 2}])
    cfg = _config(tmp_path, url=None)
    shipped = run_loop(cfg, max_iterations=1)
    assert shipped == 3
    assert read_offset(cfg.state_path) == src.stat().st_size


def test_post_success_advances_offset(tmp_path: Path):
    src = tmp_path / "calls.jsonl"
    _write_records(src, [{"i": 0}, {"i": 1}])
    cfg = _config(tmp_path, url="http://example.invalid/hook")
    with patch.object(relay_main, "post_batch") as mock_post:
        shipped = run_loop(cfg, max_iterations=1)
    assert shipped == 2
    assert mock_post.call_count == 1
    posted = mock_post.call_args.args[1]
    assert [r["i"] for r in posted] == [0, 1]
    assert read_offset(cfg.state_path) == src.stat().st_size


def test_post_failure_does_not_advance_offset(tmp_path: Path):
    src = tmp_path / "calls.jsonl"
    _write_records(src, [{"i": 0}])
    cfg = _config(tmp_path, url="http://example.invalid/hook")
    with patch.object(relay_main, "post_batch", side_effect=RuntimeError("nope")):
        shipped = run_loop(cfg, max_iterations=1)
    assert shipped == 0
    assert read_offset(cfg.state_path) == 0


def test_relay_skips_malformed_lines(tmp_path: Path):
    src = tmp_path / "calls.jsonl"
    src.parent.mkdir(parents=True, exist_ok=True)
    with src.open("w", encoding="utf-8") as fh:
        fh.write('{"i": 0}\n')
        fh.write("not json at all\n")
        fh.write('{"i": 2}\n')
    records, new_offset = read_batch(src, 0, batch=10)
    assert [r["i"] for r in records] == [0, 2]
    assert new_offset == src.stat().st_size
