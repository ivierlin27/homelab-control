"""Tests for the strict DNS allowlist (FU3c, 2026-05-18).

The runner enforces a hosts-only DNS policy when the agent manifest
declares ``sandbox.network.allowed_hosts``. This file covers:

  * the resolver helper (``_resolve_allowed_hosts``) — happy path, hard
    failure, soft-skip via env var
  * the runner's _egress_plan — argv shape, mount specs, result fields
  * the shared DNS-isolation files helper in scratch.py

The unit tests do not require Podman: the runner shim from
``test_runner.py`` is reused so we observe argv only. A separate live
smoke test (in author-sandbox.md) covers the actual containerized
``getent hosts google.com`` failure.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from . import runner as runner_mod  # same module identity as `from .runner import …`
from .runner import (
    SKIP_UNRESOLVED_ENV,
    SandboxError,
    SandboxResult,
    SandboxRunner,
    _resolve_allowed_hosts,
)
from .scratch import (
    SCRATCH_ROOT_ENV,
    default_scratch_root,
    dns_isolation_files,
)

# Why we resolve runner_mod once via `from . import runner` instead of
# `import apps._shared.sandbox.runner as runner_mod` inside each test:
# under some pytest rootdir configurations (notably GitHub Actions, CI
# run 26045803961, 2026-05-18) `apps/` lands on sys.path so the package
# is importable as both ``_shared.sandbox.runner`` AND
# ``apps._shared.sandbox.runner``. Python does NOT deduplicate by file
# path — those are two distinct module objects with independent
# globals. ``monkeypatch.setattr`` on one of them is invisible to the
# function looking up names in the other. The relative `from . import
# runner` guarantees we patch the SAME module that owns the function
# we're testing, regardless of which name pytest used to load us.


@pytest.fixture(autouse=True)
def _allow_tmp_for_tests(monkeypatch):
    """Same rationale as the other sandbox test files."""
    monkeypatch.setenv("HOMELAB_SANDBOX_ALLOW_TMP", "1")
    yield


@pytest.fixture
def isolated_scratch(monkeypatch, tmp_path):
    """Point scratch dir into tmp_path so dns_isolation_files() writes
    somewhere we can clean up + inspect."""
    monkeypatch.setenv(SCRATCH_ROOT_ENV, str(tmp_path / "scratch"))
    return tmp_path / "scratch"


# ---------------------------------------------------------------------------
# _resolve_allowed_hosts
# ---------------------------------------------------------------------------


def test_resolver_happy_path():
    fake = {"forgejo.dev-path.org": "192.168.1.42", "planka.dev-path.org": "192.168.1.42"}
    resolved, unresolved = _resolve_allowed_hosts(
        list(fake), resolver=lambda h: fake[h]
    )
    assert resolved == (
        ("forgejo.dev-path.org", "192.168.1.42"),
        ("planka.dev-path.org", "192.168.1.42"),
    )
    assert unresolved == ()


def test_resolver_empty_input():
    resolved, unresolved = _resolve_allowed_hosts([], resolver=lambda h: "127.0.0.1")
    assert resolved == ()
    assert unresolved == ()


def test_resolver_fails_loud_by_default(monkeypatch):
    monkeypatch.delenv(SKIP_UNRESOLVED_ENV, raising=False)

    def boom(host):
        raise socket.gaierror(-2, "Name or service not known")

    with pytest.raises(SandboxError, match=r"failed to resolve allowed host 'nope.example'"):
        _resolve_allowed_hosts(["nope.example"], resolver=boom)


def test_resolver_skip_unresolved_env(monkeypatch):
    monkeypatch.setenv(SKIP_UNRESOLVED_ENV, "1")

    def maybe_fail(host):
        if host == "down.example":
            raise socket.gaierror(-2, "DNS down")
        return "192.168.1.42"

    resolved, unresolved = _resolve_allowed_hosts(
        ["forgejo.dev-path.org", "down.example", "planka.dev-path.org"],
        resolver=maybe_fail,
    )
    assert resolved == (
        ("forgejo.dev-path.org", "192.168.1.42"),
        ("planka.dev-path.org", "192.168.1.42"),
    )
    assert unresolved == ("down.example",)


def test_resolver_error_message_mentions_skip_var(monkeypatch):
    """A confused operator running into a flaky DNS server should be able to
    grep the error for the env var that solves their problem."""
    monkeypatch.delenv(SKIP_UNRESOLVED_ENV, raising=False)
    with pytest.raises(SandboxError, match=SKIP_UNRESOLVED_ENV):
        _resolve_allowed_hosts(["nope"], resolver=lambda h: (_ for _ in ()).throw(socket.gaierror(-2, "x")))


# ---------------------------------------------------------------------------
# dns_isolation_files
# ---------------------------------------------------------------------------


def test_dns_isolation_files_creates_both(isolated_scratch, monkeypatch):
    monkeypatch.delenv("HOMELAB_SANDBOX_ALLOW_TMP", raising=False)
    monkeypatch.setenv("HOMELAB_SANDBOX_ALLOW_TMP", "1")  # tmp_path is under /tmp on Linux
    ns, resolv = dns_isolation_files()
    assert ns.exists()
    assert resolv.exists()
    assert "hosts: files" in ns.read_text()
    # /etc/resolv.conf override should be effectively empty (only comments).
    assert "nameserver" not in resolv.read_text().lower()


def test_dns_isolation_files_idempotent(isolated_scratch):
    a_ns, a_rv = dns_isolation_files()
    b_ns, b_rv = dns_isolation_files()
    assert a_ns == b_ns and a_rv == b_rv
    # Content shouldn't change on second call.
    assert a_ns.read_text() == b_ns.read_text()


def test_dns_isolation_files_under_scratch_root(isolated_scratch):
    ns, resolv = dns_isolation_files()
    root = default_scratch_root().resolve()
    assert root in ns.resolve().parents
    assert root in resolv.resolve().parents


# ---------------------------------------------------------------------------
# SandboxRunner._egress_plan integration (argv-level)
# ---------------------------------------------------------------------------


def _fake_podman(tmp_path: Path) -> Path:
    """Same shim as test_runner.py — duplicated locally to keep this file
    self-contained for the new feature."""
    import stat as _stat
    log_path = tmp_path / "argv.log"
    script = tmp_path / "fake-podman"
    script.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$@" >> {log_path}
exit 0
"""
    )
    script.chmod(script.stat().st_mode | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)
    return script


def _mk_runner(tmp_path: Path, allowed=()) -> SandboxRunner:
    wt = tmp_path / "wt"
    wt.mkdir()
    return SandboxRunner(
        principal="agent:test",
        image="agent-test:latest",
        worktree_path=wt,
        allowed_hosts=allowed,
        podman_path=str(_fake_podman(tmp_path)),
    )


def test_no_allowed_hosts_no_dns_isolation(tmp_path, isolated_scratch):
    runner = _mk_runner(tmp_path)
    res = runner.run(command=("true",))
    log = (Path(runner.podman_path).parent / "argv.log").read_text().splitlines()
    assert "--network=none" in log
    # No --add-host, no nsswitch/resolv mounts when there's nothing to allow.
    assert not any(s.startswith("--add-host=") for s in log)
    assert not any("nsswitch.conf" in s for s in log)
    assert not any("/etc/resolv.conf" in s for s in log)
    assert res.network_mode == "none"
    assert res.resolved_egress == ()
    assert res.unresolved_egress == ()


def test_allowed_hosts_emits_add_host_and_dns_mounts(tmp_path, isolated_scratch, monkeypatch):
    # Inject a deterministic resolver via monkeypatching the module-level default.
    fake = {"forgejo.dev-path.org": "192.168.1.42", "planka.dev-path.org": "192.168.1.42"}
    monkeypatch.setattr(runner_mod, "_default_resolver", lambda h: fake[h])

    runner = _mk_runner(tmp_path, allowed=("forgejo.dev-path.org", "planka.dev-path.org"))
    res = runner.run(command=("true",))
    log = (Path(runner.podman_path).parent / "argv.log").read_text().splitlines()

    assert "--network=slirp4netns:enable_ipv6=false" in log
    assert "--add-host=forgejo.dev-path.org:192.168.1.42" in log
    assert "--add-host=planka.dev-path.org:192.168.1.42" in log

    mount_specs = [log[i + 1] for i, ln in enumerate(log) if ln == "--mount" and i + 1 < len(log)]
    ns_mount = [s for s in mount_specs if "target=/etc/nsswitch.conf" in s]
    rv_mount = [s for s in mount_specs if "target=/etc/resolv.conf" in s]
    assert ns_mount, f"no nsswitch.conf mount in: {mount_specs!r}"
    assert rv_mount, f"no resolv.conf mount in: {mount_specs!r}"
    assert "ro=true" in ns_mount[0] and "relabel=shared" in ns_mount[0]
    assert "ro=true" in rv_mount[0] and "relabel=shared" in rv_mount[0]

    assert res.network_mode == "slirp4netns-dns-allowlist"
    assert res.resolved_egress == (
        ("forgejo.dev-path.org", "192.168.1.42"),
        ("planka.dev-path.org", "192.168.1.42"),
    )
    assert res.unresolved_egress == ()


def test_allowed_hosts_unresolvable_fails_loud(tmp_path, isolated_scratch, monkeypatch):
    monkeypatch.delenv(SKIP_UNRESOLVED_ENV, raising=False)
    monkeypatch.setattr(
        runner_mod,
        "_default_resolver",
        lambda h: (_ for _ in ()).throw(socket.gaierror(-2, "broken")),
    )
    runner = _mk_runner(tmp_path, allowed=("does-not-resolve.example",))
    with pytest.raises(SandboxError, match=r"failed to resolve allowed host"):
        runner.run(command=("true",))


def test_allowed_hosts_skip_unresolved_continues(tmp_path, isolated_scratch, monkeypatch):
    monkeypatch.setenv(SKIP_UNRESOLVED_ENV, "1")

    def resolver(host):
        if host == "broken.example":
            raise socket.gaierror(-2, "DNS down")
        return "192.168.1.42"

    monkeypatch.setattr(runner_mod, "_default_resolver", resolver)
    runner = _mk_runner(
        tmp_path, allowed=("forgejo.dev-path.org", "broken.example", "planka.dev-path.org")
    )
    res = runner.run(command=("true",))

    log = (Path(runner.podman_path).parent / "argv.log").read_text().splitlines()
    assert "--add-host=forgejo.dev-path.org:192.168.1.42" in log
    assert "--add-host=planka.dev-path.org:192.168.1.42" in log
    assert not any(s.startswith("--add-host=broken.example") for s in log)
    assert res.resolved_egress == (
        ("forgejo.dev-path.org", "192.168.1.42"),
        ("planka.dev-path.org", "192.168.1.42"),
    )
    assert res.unresolved_egress == ("broken.example",)
    # Backward-compat field still includes the FULL manifest list (it's what
    # was declared, not what was reachable). Audit infra can diff against
    # resolved_egress to see what actually got an /etc/hosts entry.
    assert set(res.egress_allowed) == {
        "forgejo.dev-path.org", "broken.example", "planka.dev-path.org"
    }


def test_result_back_compat_defaults_for_no_allowlist(tmp_path, isolated_scratch):
    """A run with no allowlist should report empty resolved/unresolved, not
    None — old audit consumers that assumed tuple iteration keep working."""
    runner = _mk_runner(tmp_path)
    res = runner.run(command=("true",))
    assert isinstance(res.resolved_egress, tuple)
    assert isinstance(res.unresolved_egress, tuple)
    assert res.resolved_egress == ()
    assert res.unresolved_egress == ()
