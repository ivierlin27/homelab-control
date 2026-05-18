"""Unit tests for the local-only enforcement primitive.

Covers:
  - prefix parsing
  - snapshot loading (happy path, missing, malformed)
  - check_call decision matrix (no skill / unknown skill / not-local-only /
    local-only-but-local-model / local-only-and-cloud-model)
  - LocalOnlyViolation carries enough info for the audit row

The gateway-side integration with LiteLLM's pre-call hook lives in
``test_custom_callbacks.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .local_only_policy import (
    DEFAULT_LOCAL_PREFIXES,
    LocalOnlyViolation,
    PolicySnapshot,
    SkillPolicy,
    check_call,
    load_snapshot,
    parse_local_prefixes,
)


# ---- prefix parsing ----------------------------------------------------


def test_parse_local_prefixes_defaults_when_unset():
    assert parse_local_prefixes(None) == DEFAULT_LOCAL_PREFIXES
    assert parse_local_prefixes("") == DEFAULT_LOCAL_PREFIXES


def test_parse_local_prefixes_csv():
    assert parse_local_prefixes("homelab-, alienware-") == ("homelab-", "alienware-")


def test_parse_local_prefixes_drops_empties():
    assert parse_local_prefixes(",homelab-, ,alienware-") == ("homelab-", "alienware-")


# ---- snapshot loading -------------------------------------------------


def _write_snapshot(path: Path, skills: dict) -> None:
    path.write_text(json.dumps({"schema": 1, "skills": skills}), encoding="utf-8")


def test_load_snapshot_returns_empty_when_file_missing(tmp_path):
    snap = load_snapshot(tmp_path / "missing.json")
    assert snap.skills == {}
    assert snap.local_prefixes == DEFAULT_LOCAL_PREFIXES


def test_load_snapshot_parses_skill_entries(tmp_path):
    f = tmp_path / "p.json"
    _write_snapshot(
        f,
        {
            "intake-classify": {"local_only": True, "version": 2},
            "execute-task": {"local_only": False, "version": 1},
            "with-defaults": {},
        },
    )
    snap = load_snapshot(f)
    assert snap.get_skill("intake-classify").local_only is True
    assert snap.get_skill("intake-classify").version == 2
    assert snap.get_skill("execute-task").local_only is False
    assert snap.get_skill("with-defaults").local_only is False
    assert snap.get_skill("nope") is None


def test_load_snapshot_treats_malformed_as_empty(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not json", encoding="utf-8")
    snap = load_snapshot(f)
    assert snap.skills == {}


def test_load_snapshot_uses_env_var_when_path_is_none(tmp_path, monkeypatch):
    f = tmp_path / "via-env.json"
    _write_snapshot(f, {"intake-classify": {"local_only": True}})
    monkeypatch.setenv("SKILL_POLICY_SNAPSHOT", str(f))
    snap = load_snapshot(None)
    assert snap.get_skill("intake-classify").local_only is True


def test_load_snapshot_honours_local_prefix_env(tmp_path, monkeypatch):
    f = tmp_path / "p.json"
    _write_snapshot(f, {})
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_PREFIXES", "alienware-")
    snap = load_snapshot(f)
    assert snap.local_prefixes == ("alienware-",)


# ---- is_local_model ---------------------------------------------------


@pytest.mark.parametrize(
    "model,expected",
    [
        ("homelab-strong-long", True),
        ("homelab-fast", True),
        ("local-mistral", True),
        ("openai/gpt-4o-mini", False),
        ("anthropic/claude-3-opus", False),
        ("", False),
        (None, False),
    ],
)
def test_is_local_model_uses_prefixes(model, expected):
    snap = PolicySnapshot(skills={}, local_prefixes=DEFAULT_LOCAL_PREFIXES)
    assert snap.is_local_model(model) is expected


# ---- check_call decision matrix ---------------------------------------


def _snap(local_only: bool) -> PolicySnapshot:
    return PolicySnapshot(
        skills={"finance-categorize": SkillPolicy("finance-categorize", local_only, 1)},
        local_prefixes=DEFAULT_LOCAL_PREFIXES,
    )


def test_check_call_no_skill_id_allows():
    check_call(skill_id=None, model="openai/gpt-4o", snapshot=_snap(True))
    check_call(skill_id="", model="openai/gpt-4o", snapshot=_snap(True))


def test_check_call_unknown_skill_allows():
    check_call(skill_id="not-in-snapshot", model="openai/gpt-4o", snapshot=_snap(True))


def test_check_call_skill_not_local_only_allows_cloud_model():
    check_call(
        skill_id="finance-categorize",
        model="openai/gpt-4o",
        snapshot=_snap(False),
    )


def test_check_call_skill_local_only_with_local_model_allows():
    check_call(
        skill_id="finance-categorize",
        model="homelab-strong",
        snapshot=_snap(True),
    )


def test_check_call_skill_local_only_with_cloud_model_rejects():
    with pytest.raises(LocalOnlyViolation) as exc_info:
        check_call(
            skill_id="finance-categorize",
            model="openai/gpt-4o",
            snapshot=_snap(True),
        )
    exc = exc_info.value
    assert exc.skill_id == "finance-categorize"
    assert exc.model == "openai/gpt-4o"
    assert "local_only=true" in exc.reason
    assert "openai/gpt-4o" in exc.reason


def test_check_call_skill_local_only_with_missing_model_rejects():
    """A null model on a local_only skill is treated as 'not local'."""
    with pytest.raises(LocalOnlyViolation) as exc_info:
        check_call(skill_id="finance-categorize", model=None, snapshot=_snap(True))
    assert exc_info.value.model == "<missing>"


def test_violation_as_audit_dict():
    exc = LocalOnlyViolation(
        skill_id="x", model="openai/y", reason="because"
    )
    row = exc.as_audit_dict()
    assert row == {
        "kind": "local_only_violation",
        "skill_id": "x",
        "model": "openai/y",
        "reason": "because",
    }
