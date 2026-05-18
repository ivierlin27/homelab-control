"""Tests for the sandbox scratch-dir helper."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .scratch import (
    DEFAULT_SCRATCH_ROOT,
    SCRATCH_ROOT_ENV,
    ScratchError,
    _is_under_tmp,
    cleanup_scratch_dir,
    default_scratch_root,
    make_scratch_dir,
    perms_octal,
)


@pytest.fixture(autouse=True)
def _allow_tmp_for_tests(monkeypatch):
    """Pytest's ``tmp_path`` lives under ``/tmp/pytest-of-<user>/...`` on
    Fedora/Linux. Without this fixture, every test that uses ``tmp_path`` as
    a scratch root trips the production guard. Refusal tests opt out by
    re-deleting the env var inside the test body.
    """
    monkeypatch.setenv("HOMELAB_SANDBOX_ALLOW_TMP", "1")
    yield


# ---------------------------------------------------------------------------
# default_scratch_root
# ---------------------------------------------------------------------------


def test_default_root_returns_var_lib_path(monkeypatch):
    monkeypatch.delenv(SCRATCH_ROOT_ENV, raising=False)
    assert default_scratch_root() == DEFAULT_SCRATCH_ROOT
    assert str(default_scratch_root()).startswith("/var/lib/homelab-control/sandbox")


def test_default_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(SCRATCH_ROOT_ENV, str(tmp_path / "custom"))
    assert default_scratch_root() == (tmp_path / "custom").resolve()


def test_default_root_env_override_expands_user(monkeypatch):
    monkeypatch.setenv(SCRATCH_ROOT_ENV, "~/some/path")
    out = default_scratch_root()
    assert str(out).startswith(os.path.expanduser("~"))


# ---------------------------------------------------------------------------
# make_scratch_dir
# ---------------------------------------------------------------------------


def test_make_scratch_dir_creates_under_root(monkeypatch, tmp_path):
    monkeypatch.setenv(SCRATCH_ROOT_ENV, str(tmp_path / "scratch"))
    out = make_scratch_dir("test-corr-id-123")
    assert out.is_dir()
    assert out.name == "test-corr-id-123"
    assert (tmp_path / "scratch").resolve() in out.resolve().parents
    assert perms_octal(out) == 0o700


def test_make_scratch_dir_auto_id_unique(monkeypatch, tmp_path):
    monkeypatch.setenv(SCRATCH_ROOT_ENV, str(tmp_path / "scratch"))
    a = make_scratch_dir()
    b = make_scratch_dir()
    assert a != b
    assert a.is_dir() and b.is_dir()


def test_make_scratch_dir_rejects_path_escape(monkeypatch, tmp_path):
    monkeypatch.setenv(SCRATCH_ROOT_ENV, str(tmp_path / "scratch"))
    # The slug normalizer replaces .. with -, so this should land safely
    # at <root>/-elsewhere instead of escaping the root.
    out = make_scratch_dir("../elsewhere")
    assert (tmp_path / "scratch").resolve() in out.resolve().parents


def test_make_scratch_dir_refuses_tmp_root(monkeypatch):
    # Opt out of the autouse allow-tmp fixture; this test wants the guard ON.
    monkeypatch.delenv("HOMELAB_SANDBOX_ALLOW_TMP", raising=False)
    monkeypatch.setenv(SCRATCH_ROOT_ENV, "/tmp/sandbox")
    with pytest.raises(ScratchError, match=r"/tmp"):
        make_scratch_dir("anything")


def test_make_scratch_dir_tmp_root_allowed_with_env(monkeypatch, tmp_path):
    """The escape hatch lets tests + intentional debugging use /tmp roots."""
    tmp_root = Path("/tmp/sandbox-test-scratch-allowed")
    monkeypatch.setenv("HOMELAB_SANDBOX_ALLOW_TMP", "1")
    monkeypatch.setenv(SCRATCH_ROOT_ENV, str(tmp_root))
    out = make_scratch_dir("test")
    try:
        assert out.is_dir()
        # On macOS /tmp -> /private/tmp; accept either to keep the test
        # portable across the dev laptop and the Fedora deploy target.
        assert str(out).startswith("/tmp/") or str(out).startswith("/private/tmp/")
    finally:
        if out.exists():
            out.rmdir()
        if tmp_root.exists():
            tmp_root.rmdir()


def test_make_scratch_dir_collision_fails(monkeypatch, tmp_path):
    monkeypatch.setenv(SCRATCH_ROOT_ENV, str(tmp_path / "scratch"))
    make_scratch_dir("collide")
    with pytest.raises(FileExistsError):
        make_scratch_dir("collide")


# ---------------------------------------------------------------------------
# cleanup_scratch_dir
# ---------------------------------------------------------------------------


def test_cleanup_removes_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(SCRATCH_ROOT_ENV, str(tmp_path / "scratch"))
    out = make_scratch_dir("toclean")
    (out / "file.txt").write_text("payload")
    cleanup_scratch_dir(out)
    assert not out.exists()


def test_cleanup_refuses_path_outside_root(monkeypatch, tmp_path):
    monkeypatch.setenv(SCRATCH_ROOT_ENV, str(tmp_path / "scratch"))
    # Try to clean up a path that is NOT under the configured root.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ScratchError, match="outside scratch root"):
        cleanup_scratch_dir(elsewhere)
    assert elsewhere.exists()  # never deleted


def test_cleanup_missing_ok_default(monkeypatch, tmp_path):
    monkeypatch.setenv(SCRATCH_ROOT_ENV, str(tmp_path / "scratch"))
    # Path under root that simply doesn't exist — no-op.
    cleanup_scratch_dir(tmp_path / "scratch" / "ghost")  # default missing_ok=True


def test_cleanup_missing_strict_raises(monkeypatch, tmp_path):
    monkeypatch.setenv(SCRATCH_ROOT_ENV, str(tmp_path / "scratch"))
    with pytest.raises(ScratchError, match="does not exist"):
        cleanup_scratch_dir(tmp_path / "scratch" / "ghost", missing_ok=False)


# ---------------------------------------------------------------------------
# _is_under_tmp (used by runner __post_init__)
# ---------------------------------------------------------------------------


def test_is_under_tmp_detects_tmp_paths(monkeypatch):
    # Opt out of the autouse allow-tmp fixture.
    monkeypatch.delenv("HOMELAB_SANDBOX_ALLOW_TMP", raising=False)
    assert _is_under_tmp(Path("/tmp/x")) is True
    assert _is_under_tmp(Path("/tmp")) is True
    assert _is_under_tmp(Path("/var/lib/homelab-control/sandbox/x")) is False
    assert _is_under_tmp(Path("/home/user/x")) is False


def test_is_under_tmp_respects_allow_env(monkeypatch):
    monkeypatch.setenv("HOMELAB_SANDBOX_ALLOW_TMP", "1")
    assert _is_under_tmp(Path("/tmp/x")) is False
