"""Tests for ``plan_principal`` and ``render_plan_markdown``.

The plan path must be **purely** read-only: no state files written, no
ssh-keygen invocation, no podman build, no API calls. We verify this by:

  - running it against every real registered principal and asserting the
    default state directory is left empty
  - monkey-patching ``subprocess.run`` to fail any test that triggers a
    subprocess call from the issuer module's namespace
  - asserting the rendered markdown has stable structure and references
    the right manifest path
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps._shared.registry import load_registry

from .issuer import (
    Component,
    ComponentStatus,
    plan_principal,
    render_plan_markdown,
)
from .state import StateStore


# ---- read-only guarantee ------------------------------------------------


@pytest.fixture
def fail_on_subprocess(monkeypatch):
    """Any subprocess.run call from inside the issuer module fails the test."""
    import apps._shared.identity.issuer as issuer_mod

    def _refuse(*args, **kwargs):
        raise AssertionError(
            f"plan_principal must not call subprocess.run; got args={args!r}"
        )
    monkeypatch.setattr(issuer_mod.subprocess, "run", _refuse)
    return _refuse


@pytest.mark.parametrize(
    "principal",
    ["agent:executive", "agent:homelab-maintainer", "agent:homelab", "agent:review"],
)
def test_plan_is_pure_for_every_registered_principal(
    principal, fail_on_subprocess, tmp_path, monkeypatch
):
    """No state writes, no subprocess calls, plan returns sane data."""
    # Redirect the identity state dir at tmp_path. plan_principal must NOT
    # create any files here.
    monkeypatch.setenv("HOMELAB_IDENTITY_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(tmp_path / "ssh"))

    plan = plan_principal(principal)
    assert plan.principal == principal
    assert plan.manifest_path.is_file()

    # Six components named in canonical order.
    component_ids = [c.component for c in plan.components]
    assert component_ids == [
        Component.SSH_KEY,
        Component.SANDBOX_IMAGE,
        Component.FORGEJO_ACCOUNT,
        Component.FORGEJO_PAT,
        Component.DISCORD_BOT,
        Component.INFISICAL_TOKEN,
    ]

    # No state files appeared on disk.
    state_dir = tmp_path / "state"
    if state_dir.exists():
        assert list(state_dir.iterdir()) == []

    # The SSH dir was never populated (no key generation).
    ssh_dir = tmp_path / "ssh"
    if ssh_dir.exists():
        assert list(ssh_dir.iterdir()) == []


def test_plan_does_not_touch_default_state_dir(tmp_path, monkeypatch):
    """Belt-and-suspenders: even with the real default state dir env,
    plan_principal must not leave residue."""
    monkeypatch.setenv("HOMELAB_IDENTITY_STATE", str(tmp_path))
    store_before = list(tmp_path.iterdir()) if tmp_path.exists() else []
    plan_principal("agent:executive")
    store_after = list(tmp_path.iterdir()) if tmp_path.exists() else []
    assert store_before == store_after

    # Also confirm a real StateStore at that path has no principal entries.
    store = StateStore(tmp_path)
    assert store.list_principals() == []


# ---- semantic content ---------------------------------------------------


def test_executive_plan_marks_ssh_pending_if_no_key(tmp_path, monkeypatch, fail_on_subprocess):
    """Executive has an identity.git_user, no key on disk in the tmp path,
    so SSH should plan as PENDING (with a 'would-generate' next step)."""
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(tmp_path / "ssh"))
    plan = plan_principal("agent:executive")
    ssh = next(c for c in plan.components if c.component == Component.SSH_KEY)
    assert ssh.would_status == ComponentStatus.PENDING
    assert any("Would generate" in s for s in ssh.next_steps)
    assert any("ed25519" in s for s in ssh.next_steps)


def test_plan_marks_ssh_issued_if_key_already_present(tmp_path, monkeypatch, fail_on_subprocess):
    """If the keypair files already exist on the chosen ssh dir, the plan
    reports SSH as ISSUED with a 'would skip' summary."""
    ssh_dir = tmp_path / "ssh"
    ssh_dir.mkdir()
    (ssh_dir / "agent-executive").write_text("priv stub", encoding="utf-8")
    (ssh_dir / "agent-executive.pub").write_text("pub stub", encoding="utf-8")
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(ssh_dir))
    plan = plan_principal("agent:executive")
    ssh = next(c for c in plan.components if c.component == Component.SSH_KEY)
    assert ssh.would_status == ComponentStatus.ISSUED
    assert "already present" in ssh.summary


def test_plan_marks_sandbox_image_present(tmp_path, monkeypatch, fail_on_subprocess):
    """All shipped agent manifests reference Containerfiles that exist in
    apps/_shared/sandbox/images/. The plan should report present."""
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(tmp_path))
    plan = plan_principal("agent:executive")
    img = next(c for c in plan.components if c.component == Component.SANDBOX_IMAGE)
    assert img.would_status == ComponentStatus.OPERATOR_TODO
    assert "present" in img.summary  # Containerfile is shipped


# ---- markdown rendering -------------------------------------------------


def test_render_markdown_is_deterministic(tmp_path, monkeypatch, fail_on_subprocess):
    """Calling render twice on the same plan must produce identical output
    (no timestamps, no PIDs, etc.). That's the contract for committing the
    runbook to the repo."""
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(tmp_path))
    p = plan_principal("agent:homelab")
    a = render_plan_markdown(p)
    b = render_plan_markdown(p)
    assert a == b
    assert a.startswith("# Identity issuance runbook")
    assert "agent:homelab" in a
    assert "## Component summary" in a
    assert "## Operator checklists" in a
    # Every component appears in the summary table.
    for c in p.components:
        assert f"`{c.component.value}`" in a


def test_render_markdown_lists_operator_steps(tmp_path, monkeypatch, fail_on_subprocess):
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(tmp_path))
    p = plan_principal("agent:executive")
    md = render_plan_markdown(p)
    # The Forgejo checklist must mention the account name from the manifest.
    assert "agent-executive" in md
    # The Discord checklist must mention the channel list.
    assert "#intake" in md or "#approvals" in md
    # The Infisical checklist must reference the secrets_profile.
    assert "executive" in md  # secrets_profile is "executive"
