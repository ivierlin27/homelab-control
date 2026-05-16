"""Tests for the identity issuer state machine."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from apps._shared.registry import load_registry

from .issuer import (
    issue_principal,
    issue_ssh_key,
    register_sandbox_image,
    revoke_component,
    verify_principal,
)
from .state import (
    Component,
    ComponentStatus,
    IdentityState,
    StateStore,
)


# ---------------------------------------------------------------------------
# state machine basics
# ---------------------------------------------------------------------------


def test_state_round_trip(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    state = store.load("agent:foo")
    state.set_component(
        Component.SSH_KEY,
        status=ComponentStatus.ISSUED,
        details={"private_key": "/x", "public_key": "/x.pub"},
    )
    path = store.save(state)
    assert path.is_file()
    raw = json.loads(path.read_text())
    assert raw["principal"] == "agent:foo"
    assert raw["components"]["ssh_key"]["status"] == "issued"
    again = store.load("agent:foo")
    assert again.status(Component.SSH_KEY) == ComponentStatus.ISSUED
    assert again.get_details(Component.SSH_KEY)["private_key"] == "/x"


def test_status_unset_is_pending(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    state = store.load("agent:foo")
    assert state.status(Component.DISCORD_BOT) == ComponentStatus.PENDING


def test_state_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOMELAB_IDENTITY_STATE", str(tmp_path / "elsewhere"))
    from .state import default_state_dir

    assert default_state_dir() == tmp_path / "elsewhere"


# ---------------------------------------------------------------------------
# end-to-end on the real registry (with isolated state dir + ssh dir)
# ---------------------------------------------------------------------------


def test_issue_real_registry_principal(tmp_path: Path) -> None:
    registry = load_registry()
    state_dir = tmp_path / "state"
    ssh_dir = tmp_path / "ssh"
    store = StateStore(state_dir)

    report = issue_principal(
        "agent:homelab-maintainer",
        registry=registry,
        state_store=store,
        ssh_dir=ssh_dir,
    )

    assert report.principal == "agent:homelab-maintainer"
    assert report.state_path.is_file()

    state = store.load("agent:homelab-maintainer")

    # SSH key actually issued: files exist, perms 0600/0644
    assert state.status(Component.SSH_KEY) == ComponentStatus.ISSUED
    details = state.get_details(Component.SSH_KEY)
    priv = Path(details["private_key"])
    pub = Path(details["public_key"])
    assert priv.is_file()
    assert pub.is_file()
    assert (priv.stat().st_mode & 0o777) == 0o600
    assert (pub.stat().st_mode & 0o777) == 0o644

    # Sandbox image: Containerfile is present in repo, so OPERATOR_TODO with build step
    assert state.status(Component.SANDBOX_IMAGE) == ComponentStatus.OPERATOR_TODO
    sandbox_steps = state.get_next_steps(Component.SANDBOX_IMAGE)
    assert any("podman" in s or "sandbox build" in s for s in sandbox_steps)

    # Operator-mediated steps include real account names from the manifest
    assert state.status(Component.FORGEJO_ACCOUNT) == ComponentStatus.OPERATOR_TODO
    assert any("agent-homelab-maintainer" in s for s in state.get_next_steps(Component.FORGEJO_ACCOUNT))
    assert state.status(Component.DISCORD_BOT) == ComponentStatus.OPERATOR_TODO
    discord_steps = state.get_next_steps(Component.DISCORD_BOT)
    assert any("discord.com/developers" in s for s in discord_steps)
    assert state.status(Component.INFISICAL_TOKEN) == ComponentStatus.OPERATOR_TODO


def test_issue_idempotent(tmp_path: Path) -> None:
    registry = load_registry()
    store = StateStore(tmp_path / "state")
    ssh_dir = tmp_path / "ssh"

    issue_principal("agent:review", registry=registry, state_store=store, ssh_dir=ssh_dir)
    state1 = store.load("agent:review")
    fp1 = state1.get_details(Component.SSH_KEY)["fingerprint"]

    # Re-run — should not regenerate the key
    issue_principal("agent:review", registry=registry, state_store=store, ssh_dir=ssh_dir)
    state2 = store.load("agent:review")
    fp2 = state2.get_details(Component.SSH_KEY)["fingerprint"]

    assert fp1 == fp2
    assert state2.status(Component.SSH_KEY) == ComponentStatus.ISSUED


# ---------------------------------------------------------------------------
# verify + revoke
# ---------------------------------------------------------------------------


def test_verify_passes_after_issue(tmp_path: Path) -> None:
    registry = load_registry()
    store = StateStore(tmp_path / "state")
    issue_principal(
        "agent:homelab-maintainer",
        registry=registry,
        state_store=store,
        ssh_dir=tmp_path / "ssh",
    )
    results = verify_principal("agent:homelab-maintainer", registry=registry, state_store=store)
    ssh_status, ssh_note = results[Component.SSH_KEY]
    assert ssh_status == ComponentStatus.ISSUED
    assert "ok" in ssh_note or "warning" in ssh_note  # macOS umask sometimes loosens
    sandbox_status, _ = results[Component.SANDBOX_IMAGE]
    assert sandbox_status == ComponentStatus.OPERATOR_TODO


def test_revoke_marks_component(tmp_path: Path) -> None:
    registry = load_registry()
    store = StateStore(tmp_path / "state")
    issue_principal(
        "agent:review",
        registry=registry,
        state_store=store,
        ssh_dir=tmp_path / "ssh",
    )
    revoke_component(
        "agent:review",
        Component.SSH_KEY,
        state_store=store,
        delete_local_artifacts=True,
    )
    state = store.load("agent:review")
    assert state.status(Component.SSH_KEY) == ComponentStatus.REVOKED
    details = state.get_details(Component.SSH_KEY)
    if details.get("private_key"):
        assert not Path(details["private_key"]).exists()


# ---------------------------------------------------------------------------
# manifest-driven NOT_REQUIRED handling
# ---------------------------------------------------------------------------


def test_components_without_grants_marked_not_required(tmp_path: Path) -> None:
    """An agent whose manifest doesn't request a discord bot should land NOT_REQUIRED."""
    # Build a minimal in-memory registry with one agent missing optional fields.
    from textwrap import dedent

    import yaml

    repo_root = tmp_path / "repo"
    (repo_root / "config" / "memory").mkdir(parents=True)
    (repo_root / "config" / "memory" / "principals.yaml").write_text(
        dedent("""
            principals:
              - id: agent:bare
                kind: agent
        """).strip()
        + "\n"
    )
    (repo_root / "config" / "agents").mkdir(parents=True)
    (repo_root / "config" / "agents" / "agent-bare.yaml").write_text(
        yaml.safe_dump(
            {
                "principal": "agent:bare",
                "display_name": "Bare",
                "domain": "test",
                "queue_dir": "~/.local/state/test/agent-bare",
                "references": {"memory_principal": "config/memory/principals.yaml"},
                # no identity, no sandbox, no discord
            }
        )
    )
    (repo_root / "config" / "agents" / "registry.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "agents": [
                    {"principal": "agent:bare", "manifest": "config/agents/agent-bare.yaml"}
                ],
            }
        )
    )
    registry = load_registry(
        repo_root / "config" / "agents" / "registry.yaml",
        repo_root=repo_root,
    )
    store = StateStore(tmp_path / "state")
    issue_principal("agent:bare", registry=registry, state_store=store, ssh_dir=tmp_path / "ssh")
    state = store.load("agent:bare")
    assert state.status(Component.SSH_KEY) == ComponentStatus.NOT_REQUIRED
    assert state.status(Component.SANDBOX_IMAGE) == ComponentStatus.NOT_REQUIRED
    assert state.status(Component.FORGEJO_ACCOUNT) == ComponentStatus.NOT_REQUIRED
    assert state.status(Component.DISCORD_BOT) == ComponentStatus.NOT_REQUIRED
    assert state.status(Component.INFISICAL_TOKEN) == ComponentStatus.NOT_REQUIRED
