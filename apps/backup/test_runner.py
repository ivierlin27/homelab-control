"""Unit tests for the tiered restic backup runner."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from .runner import (
    RunPlan,
    TierConfig,
    build_backup_argv,
    build_forget_argv,
    build_plan,
    expand_path,
    parse_config,
    run_plan,
)


def _tier(**overrides) -> TierConfig:
    base = dict(
        name="hot",
        tag="hot",
        paths=("$HOME/.local/state/homelab-control",),
        excludes=("**/__pycache__",),
        keep={"hourly": 48, "daily": 30},
    )
    base.update(overrides)
    return TierConfig(**base)


def test_parse_config_basic():
    raw = {
        "tiers": {
            "hot": {
                "tag": "hot",
                "paths": ["$HOME/a", "$HOME/b"],
                "excludes": ["**/.venv"],
                "keep": {"hourly": 48, "daily": 30},
            }
        }
    }
    cfg = parse_config(raw)
    assert set(cfg) == {"hot"}
    tier = cfg["hot"]
    assert tier.paths == ("$HOME/a", "$HOME/b")
    assert tier.excludes == ("**/.venv",)
    assert tier.keep == {"hourly": 48, "daily": 30}


def test_parse_config_rejects_empty():
    with pytest.raises(ValueError):
        parse_config({"tiers": {}})


def test_expand_path_replaces_home():
    assert expand_path("$HOME/x", home="/h") == "/h/x"
    assert expand_path("${HOME}/y", home="/h") == "/h/y"


def test_expand_path_rejects_other_env():
    with pytest.raises(ValueError):
        expand_path("$XDG_DATA_HOME/foo", home="/h")


def test_build_plan_separates_existing_and_missing(tmp_path: Path):
    real = tmp_path / "exists"
    real.mkdir()
    tier = _tier(paths=(str(real), str(tmp_path / "nope")))
    plans = build_plan(tier, ["repo1", "repo2"], home="/unused")
    assert len(plans) == 2
    assert plans[0].expanded_paths == (str(real),)
    assert plans[0].skipped_paths == (str(tmp_path / "nope"),)
    assert plans[0].repository == "repo1"
    assert plans[1].repository == "repo2"


def test_build_backup_argv_includes_tag_excludes_and_paths():
    plan = RunPlan(_tier(), "repo", expanded_paths=("/a", "/b"), skipped_paths=())
    argv = build_backup_argv(plan, restic_bin="restic")
    assert argv[:4] == ["restic", "backup", "--tag", "hot"]
    assert "--exclude" in argv
    assert "**/__pycache__" in argv
    assert argv[-2:] == ["/a", "/b"]


def test_build_backup_argv_empty_when_no_paths():
    plan = RunPlan(_tier(), "repo", expanded_paths=(), skipped_paths=("/a",))
    assert build_backup_argv(plan, restic_bin="restic") == []


def test_build_forget_argv_with_policy():
    plan = RunPlan(_tier(), "repo", expanded_paths=("/a",), skipped_paths=())
    argv = build_forget_argv(plan, restic_bin="restic")
    assert argv is not None
    assert argv[:4] == ["restic", "forget", "--prune", "--tag"]
    assert "--keep-hourly" in argv
    assert "48" in argv
    assert "--keep-daily" in argv
    assert "30" in argv


def test_build_forget_argv_returns_none_when_no_policy():
    plan = RunPlan(_tier(keep={"hourly": 0}), "repo", expanded_paths=("/a",), skipped_paths=())
    assert build_forget_argv(plan, restic_bin="restic") is None


def test_run_plan_invokes_backup_then_forget(monkeypatch: pytest.MonkeyPatch):
    plan = RunPlan(_tier(), "repo", expanded_paths=("/a",), skipped_paths=())
    calls: list[tuple[list[str], dict]] = []

    def fake_run(argv, env, check):  # noqa: ARG001
        calls.append((argv, env))
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("HOME", "/h")
    result = run_plan(plan, restic_bin="restic", password_file="/pw", runner=fake_run)
    assert result.ok is True
    assert len(calls) == 2
    assert calls[0][0][1] == "backup"
    assert calls[1][0][1] == "forget"
    for _, env in calls:
        assert env["RESTIC_REPOSITORY"] == "repo"
        assert env["RESTIC_PASSWORD_FILE"] == "/pw"


def test_run_plan_returns_error_when_no_paths():
    plan = RunPlan(_tier(), "repo", expanded_paths=(), skipped_paths=("/missing",))
    result = run_plan(plan, restic_bin="restic", password_file=None, runner=MagicMock())
    assert result.ok is False
    assert "no existing source paths" in (result.error or "")


def test_run_plan_propagates_backup_failure():
    plan = RunPlan(_tier(), "repo", expanded_paths=("/a",), skipped_paths=())

    def fake_run(argv, env, check):  # noqa: ARG001
        return SimpleNamespace(returncode=3)

    result = run_plan(plan, restic_bin="restic", password_file=None, runner=fake_run)
    assert result.ok is False
    assert result.backup_rc == 3
    assert "exited 3" in (result.error or "")


def test_run_plan_skips_forget_after_backup_failure():
    plan = RunPlan(_tier(), "repo", expanded_paths=("/a",), skipped_paths=())
    call_count = {"n": 0}

    def fake_run(argv, env, check):  # noqa: ARG001
        call_count["n"] += 1
        return SimpleNamespace(returncode=1)

    result = run_plan(plan, restic_bin="restic", password_file=None, runner=fake_run)
    assert call_count["n"] == 1
    assert result.forget_rc is None
    assert not result.ok


def test_run_plan_password_file_omitted_when_none(monkeypatch: pytest.MonkeyPatch):
    plan = RunPlan(_tier(), "repo", expanded_paths=("/a",), skipped_paths=())
    monkeypatch.delenv("RESTIC_PASSWORD_FILE", raising=False)
    captured: dict = {}

    def fake_run(argv, env, check):  # noqa: ARG001
        captured["env"] = env
        return SimpleNamespace(returncode=0)

    run_plan(plan, restic_bin="restic", password_file=None, runner=fake_run)
    assert "RESTIC_PASSWORD_FILE" not in captured["env"]
