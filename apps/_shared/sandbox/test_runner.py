"""Tests for the sandbox runner.

These tests do not require Podman to be installed: they shim the runner's
``podman_path`` to a small fake executable that records its argv and exits
cleanly. Real podman integration is exercised on hosts where it's available
via the ``--print-only`` build flag and the ``run`` CLI.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from .runner import BranchStrategy, SandboxError, SandboxRunner


def _fake_podman(tmp_path: Path, *, exit_code: int = 0, stdout: str = "", stderr: str = "") -> Path:
    """Create an executable shim that records its argv to argv.log and exits."""
    log_path = tmp_path / "argv.log"
    script = tmp_path / "fake-podman"
    script.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$@" >> {log_path}
printf '%s' '{stdout}'
printf '%s' '{stderr}' >&2
exit {exit_code}
"""
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _runner(tmp_path: Path, *, allowed_hosts=()) -> SandboxRunner:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    return SandboxRunner(
        principal="agent:test",
        image="agent-test:latest",
        worktree_path=worktree,
        allowed_hosts=allowed_hosts,
        podman_path=str(_fake_podman(tmp_path)),
    )


def test_rejects_non_agent_principal(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(SandboxError, match="agent:"):
        SandboxRunner(principal="human:kevin", image="x:latest", worktree_path=worktree)


def test_rejects_missing_worktree(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="worktree_path"):
        SandboxRunner(
            principal="agent:test",
            image="x:latest",
            worktree_path=tmp_path / "nope",
        )


def test_rejects_empty_command(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    with pytest.raises(SandboxError, match="non-empty"):
        runner.run(command=())


def test_run_records_argv_and_returns_result(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    result = runner.run(command=("echo", "hi"))
    log = (Path(runner.podman_path).parent / "argv.log").read_text().splitlines()
    assert log[0] == "run"
    assert "--rm" in log
    assert "--read-only" in log
    assert "--cap-drop=ALL" in log
    assert "--security-opt=no-new-privileges" in log
    # SELinux re-tightening (2026-05-18): label=disable removed in favor of
    # per-mount :Z relabel via relabel=shared. This guards against regression.
    assert "--security-opt=label=disable" not in log
    assert "--userns=keep-id" in log
    assert "--network=none" in log
    # Worktree mount must carry relabel=shared so Podman gives the bind path
    # the container's MCS categories. Without this, SELinux denies all reads.
    mount_specs = [log[i + 1] for i, ln in enumerate(log) if ln == "--mount" and i + 1 < len(log)]
    assert mount_specs, "runner did not emit any --mount specs"
    worktree_specs = [s for s in mount_specs if "target=/work" in s]
    assert worktree_specs, f"no worktree mount spec found in: {mount_specs!r}"
    assert "relabel=shared" in worktree_specs[0], (
        f"worktree mount missing relabel=shared: {worktree_specs[0]!r}"
    )
    assert log[-2] == "echo"
    assert log[-1] == "hi"
    assert result.exit_code == 0
    assert result.image == "agent-test:latest"
    assert result.network_mode == "none"
    assert result.egress_allowed == ()


def test_allowed_hosts_changes_network_mode(tmp_path: Path) -> None:
    runner = _runner(tmp_path, allowed_hosts=("forgejo.dev-path.org",))
    result = runner.run(command=("true",))
    log = (Path(runner.podman_path).parent / "argv.log").read_text().splitlines()
    assert any(line.startswith("--network=slirp4netns") for line in log)
    assert "--network=none" not in log
    assert result.network_mode == "slirp4netns"
    assert result.egress_allowed == ("forgejo.dev-path.org",)


def test_session_jsonl_is_appended(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    session = tmp_path / "session.jsonl"
    runner.run(command=("echo", "x"), capture_session_to=session)
    runner.run(command=("echo", "y"), capture_session_to=session)
    lines = session.read_text().splitlines()
    assert len(lines) == 2
    import json
    rec0 = json.loads(lines[0])
    assert rec0["principal"] == "agent:test"
    assert rec0["command"] == ["echo", "x"]
    assert rec0["network_mode"] == "none"
    assert "correlation_id" in rec0


def test_propagates_exit_code(tmp_path: Path) -> None:
    runner = SandboxRunner(
        principal="agent:test",
        image="agent-test:latest",
        worktree_path=tmp_path,
        podman_path=str(_fake_podman(tmp_path, exit_code=42)),
    )
    result = runner.run(command=("false",))
    assert result.exit_code == 42


def test_correlation_id_used_when_provided(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    cid = "00000000-1111-2222-3333-444444444444"
    result = runner.run(command=("true",), correlation_id=cid)
    assert result.correlation_id == cid
    assert cid[:8] in result.container_name


def test_cli_strips_leading_double_dash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: argparse.REMAINDER preserves `--`; CLI must strip it before exec."""
    from apps._shared.sandbox import __main__ as cli

    captured: dict = {}

    class _FakeRunner:
        def __init__(self, *, principal, image, worktree_path, allowed_hosts):
            captured["init"] = (principal, image, str(worktree_path), tuple(allowed_hosts))

        def run(self, *, command, timeout_seconds, capture_session_to):
            captured["command"] = tuple(command)
            from apps._shared.sandbox.runner import SandboxResult
            return SandboxResult(
                exit_code=0, stdout="ok\n", stderr="", duration_seconds=0.01,
                container_name="x", image="agent-test:latest",
                correlation_id="abc", network_mode="none", egress_allowed=(),
            )

    monkeypatch.setattr(cli, "SandboxRunner", _FakeRunner)

    # Mimic the user CLI: -- python3 -c "..."
    rc = cli.main([
        "run",
        "--principal", "agent:homelab-maintainer",
        "--worktree", str(tmp_path),
        "--", "python3", "-c", "print(1)",
    ])
    assert rc == 0, captured
    # The leading "--" must have been stripped before reaching the runner.
    assert captured["command"] == ("python3", "-c", "print(1)")


def test_branch_strategy_enum() -> None:
    # Sanity: enum values match the schema's allowed strings.
    assert BranchStrategy.HEAD.value == "head"
    assert BranchStrategy.MERGE_TO_HEAD.value == "merge-to-head"
    assert BranchStrategy.BRANCH.value == "branch"


# ---------------------------------------------------------------------------
# SELinux re-tightening guards (2026-05-18)
# ---------------------------------------------------------------------------


def test_rejects_worktree_under_tmp(tmp_path: Path, monkeypatch) -> None:
    """Constructor refuses /tmp/... because Podman's :Z can't relabel /tmp."""
    monkeypatch.delenv("HOMELAB_SANDBOX_ALLOW_TMP", raising=False)
    bad = Path("/tmp/sandbox-probe-rejected")
    bad.mkdir(exist_ok=True)
    try:
        with pytest.raises(SandboxError, match=r"/tmp.*:Z|policy will refuse"):
            SandboxRunner(
                principal="agent:test",
                image="agent-test:latest",
                worktree_path=bad,
                podman_path=str(_fake_podman(tmp_path)),
            )
    finally:
        try:
            bad.rmdir()
        except OSError:
            pass


def test_tmp_guard_can_be_overridden_for_tests(tmp_path: Path, monkeypatch) -> None:
    """HOMELAB_SANDBOX_ALLOW_TMP=1 lets tests + intentional debugging proceed."""
    monkeypatch.setenv("HOMELAB_SANDBOX_ALLOW_TMP", "1")
    ok = Path("/tmp/sandbox-probe-allowed")
    ok.mkdir(exist_ok=True)
    try:
        runner = SandboxRunner(
            principal="agent:test",
            image="agent-test:latest",
            worktree_path=ok,
            podman_path=str(_fake_podman(tmp_path)),
        )
        assert runner.worktree_path == ok
    finally:
        try:
            ok.rmdir()
        except OSError:
            pass


def test_source_file_does_not_reintroduce_label_disable() -> None:
    """Belt-and-suspenders regression check. If anyone adds back
    --security-opt=label=disable, this test fails before a smoke does."""
    from . import runner as runner_mod
    source = Path(runner_mod.__file__).read_text()
    # The string may still appear in a comment that documents the historical
    # workaround, but it must not appear as an unquoted argv element.
    code_lines = [
        ln for ln in source.splitlines()
        if not ln.lstrip().startswith("#") and not ln.lstrip().startswith('"')
    ]
    code = "\n".join(code_lines)
    assert '"--security-opt=label=disable"' not in code, (
        "label=disable reappeared as an argv element in runner.py; SELinux "
        "policy was supposed to be re-tightened on 2026-05-18 — see "
        "docs/runbooks/author-sandbox.md."
    )


def test_extra_mounts_3tuple_backward_compat(tmp_path: Path) -> None:
    """Legacy callers passing 3-tuples still work (relabel defaults to True)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    extra_host = tmp_path / "extra"
    extra_host.mkdir()
    runner = SandboxRunner(
        principal="agent:test",
        image="agent-test:latest",
        worktree_path=wt,
        extra_mounts=((extra_host, Path("/opt/extra"), True),),  # 3-tuple
        podman_path=str(_fake_podman(tmp_path)),
    )
    runner.run(command=("true",))
    log = (Path(runner.podman_path).parent / "argv.log").read_text().splitlines()
    mount_specs = [log[i + 1] for i, ln in enumerate(log) if ln == "--mount" and i + 1 < len(log)]
    extra_specs = [s for s in mount_specs if "target=/opt/extra" in s]
    assert extra_specs and "ro=true" in extra_specs[0]
    assert "relabel=shared" in extra_specs[0]


def test_extra_mounts_4tuple_relabel_opt_out(tmp_path: Path) -> None:
    """4-tuple with relabel=False lets callers mount a path that must NOT be
    relabeled (e.g. a read-only shared cache)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    runner = SandboxRunner(
        principal="agent:test",
        image="agent-test:latest",
        worktree_path=wt,
        extra_mounts=((cache, Path("/opt/cache"), True, False),),  # 4-tuple, relabel=False
        podman_path=str(_fake_podman(tmp_path)),
    )
    runner.run(command=("true",))
    log = (Path(runner.podman_path).parent / "argv.log").read_text().splitlines()
    mount_specs = [log[i + 1] for i, ln in enumerate(log) if ln == "--mount" and i + 1 < len(log)]
    cache_specs = [s for s in mount_specs if "target=/opt/cache" in s]
    assert cache_specs
    assert "relabel" not in cache_specs[0], cache_specs[0]
