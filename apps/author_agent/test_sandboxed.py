"""Unit tests for the author-agent sandboxed-check helper.

The real Podman invocation lives in :class:`apps._shared.sandbox.SandboxRunner`
and has its own tests; here we exercise the contract between
``run_command_sandboxed`` and the runner:

  - the manifest's ``sandbox.base_image`` and
    ``sandbox.network.allowed_hosts`` are read correctly
  - the shell-string contract is preserved via ``sh -c``
  - the return dict shape matches the legacy ``run_command`` so
    ``execute_task``'s ``ensure_success`` keeps working unchanged
  - a ``sandbox_check`` row is appended to the audit chain with the
    correlation id, image, network mode, and exit code
  - missing manifest / missing image config raises ``SandboxedCheckError``
  - ``sandbox_checks_enabled()`` honors the env flag
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apps._shared.audit import AuditLog  # noqa: E402
from apps._shared.sandbox.runner import SandboxResult  # noqa: E402
from apps.author_agent.sandboxed import (  # noqa: E402
    SANDBOX_FLAG_ENV,
    SandboxedCheckError,
    run_command_sandboxed,
    sandbox_checks_enabled,
)


# ---- shared fakes -------------------------------------------------------


@dataclass
class _FakeManifest:
    """Minimal AgentManifest stand-in honoring `.get(*keys, default=)`."""
    data: dict

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur


def _fake_registry_loader(manifest_data: dict):
    def loader():
        reg = MagicMock()
        reg.get.return_value = _FakeManifest(manifest_data)
        return reg
    return loader


def _result(exit_code: int = 0, stdout: str = "ok\n", stderr: str = "") -> SandboxResult:
    return SandboxResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.12,
        container_name="sb-agent-homelab-test1234",
        image="agent-homelab:latest",
        correlation_id="corr-1234",
        network_mode="none",
        egress_allowed=(),
    )


def _runner_factory(captured: dict, *, result: SandboxResult | None = None):
    """Return a class that records init args and returns ``result`` on run()."""
    res = result or _result()

    class FakeRunner:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            captured["init_called"] = True

        def run(self, *, command, timeout_seconds):
            captured["run_command"] = list(command)
            captured["run_timeout"] = timeout_seconds
            return res
    return FakeRunner


# ---- env flag ------------------------------------------------------------


def test_sandbox_checks_enabled_default_off(monkeypatch):
    monkeypatch.delenv(SANDBOX_FLAG_ENV, raising=False)
    assert sandbox_checks_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE", "On"])
def test_sandbox_checks_enabled_truthy(monkeypatch, val):
    monkeypatch.setenv(SANDBOX_FLAG_ENV, val)
    assert sandbox_checks_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "off", "", "no"])
def test_sandbox_checks_enabled_falsy(monkeypatch, val):
    monkeypatch.setenv(SANDBOX_FLAG_ENV, val)
    assert sandbox_checks_enabled() is False


# ---- happy path ----------------------------------------------------------


def test_returns_same_dict_shape_as_legacy_run_command(tmp_path):
    """run_command_sandboxed must be a drop-in: same keys, same types."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    captured: dict = {}
    res = run_command_sandboxed(
        "pytest -q",
        worktree=worktree,
        principal="agent:homelab",
        registry_loader=_fake_registry_loader({
            "sandbox": {"base_image": "agent-homelab"},
        }),
        runner_cls=_runner_factory(captured),
    )
    assert set(res.keys()) == {"command", "returncode", "stdout", "stderr"}
    assert res["command"] == "pytest -q"
    assert res["returncode"] == 0
    assert res["stdout"] == "ok\n"
    assert res["stderr"] == ""


def test_shell_string_is_wrapped_as_sh_c(tmp_path):
    """Existing job formats use shell strings; sandbox needs argv. We wrap
    them with sh -c so authors keep using ``pytest && mypy`` style commands."""
    captured: dict = {}
    run_command_sandboxed(
        "pytest && mypy .",
        worktree=tmp_path,
        registry_loader=_fake_registry_loader({
            "sandbox": {"base_image": "agent-homelab"},
        }),
        runner_cls=_runner_factory(captured),
    )
    assert captured["run_command"] == ["sh", "-c", "pytest && mypy ."]


def test_manifest_drives_image_tag_and_allowed_hosts(tmp_path):
    captured: dict = {}
    run_command_sandboxed(
        "echo hi",
        worktree=tmp_path,
        registry_loader=_fake_registry_loader({
            "sandbox": {
                "base_image": "agent-homelab",
                "network": {"allowed_hosts": ["forgejo.dev-path.org"]},
            },
        }),
        runner_cls=_runner_factory(captured),
    )
    assert captured["image"] == "agent-homelab:latest"
    assert captured["allowed_hosts"] == ("forgejo.dev-path.org",)
    assert captured["principal"] == "agent:homelab"
    assert captured["worktree_path"] == tmp_path


def test_no_allowed_hosts_means_empty_tuple(tmp_path):
    """When manifest omits sandbox.network.allowed_hosts the runner is
    constructed with the empty tuple (which the runner translates to
    --network=none)."""
    captured: dict = {}
    run_command_sandboxed(
        "echo hi",
        worktree=tmp_path,
        registry_loader=_fake_registry_loader({
            "sandbox": {"base_image": "agent-homelab"},
        }),
        runner_cls=_runner_factory(captured),
    )
    assert captured["allowed_hosts"] == ()


# ---- audit ---------------------------------------------------------------


def test_audit_row_carries_correlation_id_and_exit_code(tmp_path):
    audit_path = tmp_path / "trust-ledger.jsonl"
    audit = AuditLog(str(audit_path))
    res = run_command_sandboxed(
        "exit 7",
        worktree=tmp_path,
        registry_loader=_fake_registry_loader({
            "sandbox": {"base_image": "agent-homelab"},
        }),
        runner_cls=_runner_factory({}, result=_result(exit_code=7, stderr="boom")),
        audit=audit,
    )
    assert res["returncode"] == 7
    rows = [
        json.loads(line)
        for line in audit_path.read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "sandbox_check"
    assert row["command"] == "exit 7"
    assert row["exit_code"] == 7
    assert row["correlation_id"] == "corr-1234"
    assert row["image"] == "agent-homelab:latest"
    assert row["network_mode"] == "none"
    # chain integrity
    assert row["audit_seq"] == 1
    assert len(row["audit_hash"]) == 64


def test_no_audit_arg_means_no_ledger_write(tmp_path):
    """The function must work with audit=None; the host-path also has no
    audit today, so this preserves equivalence under the flag-off scenario
    when someone uses the helper standalone."""
    audit_path = tmp_path / "trust-ledger.jsonl"
    run_command_sandboxed(
        "echo hi",
        worktree=tmp_path,
        registry_loader=_fake_registry_loader({
            "sandbox": {"base_image": "agent-homelab"},
        }),
        runner_cls=_runner_factory({}),
        audit=None,
    )
    assert not audit_path.exists()


# ---- error paths ---------------------------------------------------------


def test_missing_base_image_raises(tmp_path):
    with pytest.raises(SandboxedCheckError, match="base_image is required"):
        run_command_sandboxed(
            "echo hi",
            worktree=tmp_path,
            registry_loader=_fake_registry_loader({"sandbox": {}}),
            runner_cls=_runner_factory({}),
        )


def test_unknown_principal_raises(tmp_path):
    from apps._shared.registry import RegistryError

    def loader():
        reg = MagicMock()
        reg.get.side_effect = RegistryError("unknown principal: agent:nope")
        return reg

    with pytest.raises(SandboxedCheckError, match="not in registry"):
        run_command_sandboxed(
            "echo hi",
            worktree=tmp_path,
            principal="agent:nope",
            registry_loader=loader,
            runner_cls=_runner_factory({}),
        )


def test_sandbox_launch_failure_is_wrapped(tmp_path):
    from apps._shared.sandbox import SandboxError

    class FailingRunner:
        def __init__(self, **kw):
            pass
        def run(self, *, command, timeout_seconds):
            raise SandboxError("podman binary not found on PATH (looked up 'podman')")

    with pytest.raises(SandboxedCheckError, match="sandbox launch failed"):
        run_command_sandboxed(
            "echo hi",
            worktree=tmp_path,
            registry_loader=_fake_registry_loader({
                "sandbox": {"base_image": "agent-homelab"},
            }),
            runner_cls=FailingRunner,
        )
