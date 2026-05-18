"""Tests for the StateStore transition engine."""

from __future__ import annotations

import json

import pytest

from .core import CheckResult, StateStore, Status


def _r(name, status, detail=""):
    return CheckResult(name=name, status=Status(status), detail=detail)


def test_first_sighting_healthy_does_not_alert(tmp_path):
    store = StateStore(tmp_path / "s.json")
    ts = store.transitions([_r("x", "healthy", "ok")])
    assert ts == []
    store.save()
    # state is persisted
    assert json.loads((tmp_path / "s.json").read_text())["by_name"]["x"]["status"] == "healthy"


def test_first_sighting_unhealthy_emits_initial_alert(tmp_path):
    store = StateStore(tmp_path / "s.json")
    ts = store.transitions([_r("x", "unhealthy", "down")])
    assert len(ts) == 1
    assert ts[0].previous == Status.UNKNOWN
    assert ts[0].current == Status.UNHEALTHY


def test_healthy_to_unhealthy_transition(tmp_path):
    store = StateStore(tmp_path / "s.json")
    store.transitions([_r("x", "healthy")])  # seed
    ts = store.transitions([_r("x", "unhealthy", "broke")])
    assert len(ts) == 1 and ts[0].current == Status.UNHEALTHY


def test_unhealthy_to_healthy_emits_recovery(tmp_path):
    store = StateStore(tmp_path / "s.json")
    store.transitions([_r("x", "unhealthy")])  # initial alert (1 transition)
    ts = store.transitions([_r("x", "healthy", "back")])
    assert len(ts) == 1 and ts[0].current == Status.HEALTHY


def test_steady_state_is_silent(tmp_path):
    store = StateStore(tmp_path / "s.json")
    store.transitions([_r("x", "healthy")])
    for _ in range(10):
        ts = store.transitions([_r("x", "healthy")])
        assert ts == []


def test_unknown_does_not_alert_until_streak_exhausted(tmp_path):
    store = StateStore(tmp_path / "s.json", unknown_alert_after=3)
    store.transitions([_r("x", "healthy")])  # seed
    assert store.transitions([_r("x", "unknown", "timeout")]) == []
    assert store.transitions([_r("x", "unknown", "timeout")]) == []
    ts = store.transitions([_r("x", "unknown", "timeout")])
    assert len(ts) == 1 and ts[0].current == Status.UNHEALTHY


def test_unknown_streak_resets_when_healthy_observed(tmp_path):
    store = StateStore(tmp_path / "s.json", unknown_alert_after=3)
    store.transitions([_r("x", "healthy")])
    store.transitions([_r("x", "unknown")])
    store.transitions([_r("x", "unknown")])
    store.transitions([_r("x", "healthy")])  # streak resets — no alert
    # next two unknowns should NOT escalate (streak was reset)
    assert store.transitions([_r("x", "unknown")]) == []
    assert store.transitions([_r("x", "unknown")]) == []


def test_state_persists_across_instances(tmp_path):
    s1 = StateStore(tmp_path / "s.json")
    s1.transitions([_r("x", "healthy"), _r("y", "unhealthy")])
    s1.save()
    s2 = StateStore(tmp_path / "s.json")
    # No transition when current matches persisted state
    assert s2.transitions([_r("x", "healthy")]) == []
    # Persisted y was unhealthy; recovering it should now emit
    ts = s2.transitions([_r("y", "healthy", "fixed")])
    assert len(ts) == 1 and ts[0].current == Status.HEALTHY


def test_multiple_checks_each_alert_independently(tmp_path):
    store = StateStore(tmp_path / "s.json")
    store.transitions([_r("a", "healthy"), _r("b", "healthy")])
    ts = store.transitions([_r("a", "unhealthy"), _r("b", "healthy")])
    assert [t.name for t in ts] == ["a"]


def test_save_is_atomic_replace(tmp_path):
    store = StateStore(tmp_path / "s.json")
    store.transitions([_r("x", "healthy", "ok")])
    store.save()
    assert not (tmp_path / "s.json.tmp").exists()  # tmp swapped in
    assert (tmp_path / "s.json").exists()
