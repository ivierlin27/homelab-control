"""Tests for sub-agent production wiring helpers."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from apps._shared.subagent.spawner import SubagentResult
from apps._shared.subagent.wiring import (
    append_subagent_section,
    route_policy_from_manifest_policy,
    subagent_enabled,
    try_researcher_summary,
)


def test_subagent_enabled_requires_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("HOMELAB_SUBAGENT_DISABLE", raising=False)
    assert subagent_enabled() is False
    monkeypatch.setenv("MODEL_GATEWAY_BASE_URL", "http://127.0.0.1:4000")
    assert subagent_enabled() is True
    monkeypatch.setenv("HOMELAB_SUBAGENT_DISABLE", "1")
    assert subagent_enabled() is False


def test_route_policy_from_manifest_defaults() -> None:
    policy = route_policy_from_manifest_policy({})
    assert policy.allowed_routes == frozenset({"local"})
    assert policy.allow_cloud is False


def test_route_policy_from_manifest_cloud_opt_in() -> None:
    policy = route_policy_from_manifest_policy(
        {
            "routing_policy": {
                "subagent_allow_cloud": True,
                "subagent_allowed_routes": ["local", "cloud-frontier"],
            }
        }
    )
    assert policy.allow_cloud is True
    assert "cloud-frontier" in policy.allowed_routes


def test_append_subagent_section_skips_empty() -> None:
    assert append_subagent_section("base", None, heading="Researcher summary") == "base"
    empty = SubagentResult(
        role="researcher",
        summary="",
        confidence="low",
        parent_correlation_id="p",
        subagent_correlation_id="s",
        route="local",
        model="homelab-strong-long",
    )
    assert append_subagent_section("base", empty, heading="Researcher summary") == "base"


def test_append_subagent_section_appends_heading() -> None:
    sub = SubagentResult(
        role="researcher",
        summary="One-line summary.",
        confidence="high",
        parent_correlation_id="p",
        subagent_correlation_id="s",
        route="local",
        model="homelab-strong-long",
    )
    out = append_subagent_section("Card body", sub, heading="Researcher summary")
    assert "## Researcher summary" in out
    assert "One-line summary." in out


def test_try_researcher_summary_returns_none_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MODEL_GATEWAY_BASE_URL", raising=False)
    assert (
        try_researcher_summary(
            "prompt",
            parent_correlation_id="corr",
            audit_path=tmp_path / "ledger.jsonl",
        )
        is None
    )


def test_try_researcher_summary_soft_fails_on_spawn_error(tmp_path: Path) -> None:
    audit = tmp_path / "ledger.jsonl"
    with mock.patch(
        "apps._shared.subagent.wiring.subagent_enabled",
        return_value=True,
    ), mock.patch(
        "apps._shared.subagent.wiring.spawn_subagent",
        side_effect=RuntimeError("gateway down"),
    ):
        assert (
            try_researcher_summary(
                "prompt",
                parent_correlation_id="corr",
                audit_path=audit,
            )
            is None
        )


def test_try_researcher_summary_delegates_to_spawn(tmp_path: Path) -> None:
    audit = tmp_path / "ledger.jsonl"
    expected = SubagentResult(
        role="researcher",
        summary="ok",
        confidence="high",
        parent_correlation_id="corr",
        subagent_correlation_id="sub",
        route="local",
        model="homelab-strong-long",
    )
    with mock.patch(
        "apps._shared.subagent.wiring.subagent_enabled",
        return_value=True,
    ), mock.patch(
        "apps._shared.subagent.wiring.spawn_subagent",
        return_value=expected,
    ) as spawn:
        got = try_researcher_summary(
            "Summarize intake.",
            parent_correlation_id="corr",
            audit_path=audit,
            route="local-fast",
        )
    assert got is expected
    spawn.assert_called_once()
    assert spawn.call_args.kwargs["route"] == "local-fast"
