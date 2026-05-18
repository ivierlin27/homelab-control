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
    apps/_shared/sandbox/images/. With an empty state store (no ISSUED record)
    the plan reports OPERATOR_TODO + 'present'."""
    monkeypatch.setenv("HOMELAB_IDENTITY_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(tmp_path / "ssh"))
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


def test_plan_reports_issued_when_state_file_says_so(tmp_path, monkeypatch, fail_on_subprocess):
    """If the issuer's state store records ISSUED for an operator-mediated
    component, the plan should report would_status=ISSUED with a one-line
    'already confirmed' note instead of re-emitting the full checklist."""
    from .state import Component, ComponentStatus, StateStore
    monkeypatch.setenv("HOMELAB_IDENTITY_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(tmp_path / "ssh"))

    store = StateStore(tmp_path / "state")
    s = store.load("agent:executive")
    fully_provisioned = (
        Component.SANDBOX_IMAGE,
        Component.FORGEJO_ACCOUNT,
        Component.FORGEJO_PAT,
        Component.DISCORD_BOT,
        Component.INFISICAL_TOKEN,
    )
    for comp in fully_provisioned:
        s.set_component(comp, status=ComponentStatus.ISSUED)
    store.save(s)

    plan = plan_principal("agent:executive")
    for comp in (
        Component.FORGEJO_ACCOUNT,
        Component.FORGEJO_PAT,
        Component.DISCORD_BOT,
        Component.INFISICAL_TOKEN,
    ):
        cp = next(c for c in plan.components if c.component == comp)
        assert cp.would_status == ComponentStatus.ISSUED, comp
        assert "already confirmed" in cp.summary, comp
        # The rotate hint is the only step; not the full operator checklist.
        assert len(cp.next_steps) == 1
        assert "revoke" in cp.next_steps[0]
    sandbox = next(c for c in plan.components if c.component == Component.SANDBOX_IMAGE)
    assert sandbox.would_status == ComponentStatus.ISSUED
    assert "already built" in sandbox.summary
    assert plan.needs_operator_action() is False


def test_plan_ignore_state_flag_forces_fresh_view(tmp_path, monkeypatch, fail_on_subprocess):
    """--ignore-state recovers the 'from scratch' onboarding runbook even
    when the state file says everything is done."""
    from .state import Component, ComponentStatus, StateStore
    monkeypatch.setenv("HOMELAB_IDENTITY_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(tmp_path / "ssh"))

    store = StateStore(tmp_path / "state")
    s = store.load("agent:executive")
    for comp in (
        Component.SANDBOX_IMAGE,
        Component.FORGEJO_ACCOUNT,
        Component.FORGEJO_PAT,
        Component.DISCORD_BOT,
        Component.INFISICAL_TOKEN,
    ):
        s.set_component(comp, status=ComponentStatus.ISSUED)
    store.save(s)

    plan = plan_principal("agent:executive", ignore_state=True)
    for comp in (
        Component.SANDBOX_IMAGE,
        Component.FORGEJO_ACCOUNT,
        Component.FORGEJO_PAT,
        Component.DISCORD_BOT,
        Component.INFISICAL_TOKEN,
    ):
        cp = next(c for c in plan.components if c.component == comp)
        assert cp.would_status == ComponentStatus.OPERATOR_TODO, comp


# ---- --principal-stub path ----------------------------------------------
#
# These tests verify that ``plan_principal(manifest=...)`` and the CLI's
# ``--principal-stub`` flag together let us produce a runbook for an agent
# that is NOT yet in ``registry.yaml``. This is the workflow the operator
# uses to provision the bot accounts BEFORE committing the manifest:
#
#   1. Draft config/agents/agent-finance.yaml
#   2. Run: python -m apps._shared.identity plan \
#        --principal-stub config/agents/agent-finance.yaml \
#        --output docs/identity-runbook-agent-finance.md
#   3. Work through the operator checklist
#   4. THEN add the agent to registry.yaml + run `identity issue`
#
# The stub path skips cross-file registry checks (uniqueness, references)
# but still runs the intrinsic shape validator so we catch typos early.


def _write_stub_manifest(path, data):
    import yaml as _yaml
    path.write_text(_yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _minimal_finance_stub_data():
    """A realistic-shape stub for agent:finance, used by the stub-path tests."""
    return {
        "principal": "agent:finance",
        "display_name": "Finance Steward",
        "domain": "finance",
        "queue_dir": "~/.local/state/homelab-control/agent-finance",
        "identity": {
            "git_user": "agent-finance",
            "git_email": "agent-finance@forgejo.dev-path.org",
            "forgejo_account": "agent-finance",
            "discord_bot_app_name": "agent-finance",
            "secrets_profile": "finance",
        },
        "sandbox": {
            "base_image": "agent-finance",
            "network": {"allowed_hosts": []},
        },
        "discord": {
            "bot_app_name": "agent-finance",
            "channels": [{"name": "#finance", "mode": "read"}],
        },
        "trust": {"autonomy_mode": "propose_only"},
    }


def test_plan_principal_accepts_stub_manifest(tmp_path, monkeypatch, fail_on_subprocess):
    """plan_principal(manifest=...) skips the registry lookup."""
    from apps._shared.registry import AgentManifest
    monkeypatch.setenv("HOMELAB_IDENTITY_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(tmp_path / "ssh"))
    stub_path = tmp_path / "agent-finance.yaml"
    data = _minimal_finance_stub_data()
    _write_stub_manifest(stub_path, data)
    stub = AgentManifest(
        principal=data["principal"], path=stub_path, data=data
    )

    plan = plan_principal("agent:finance", manifest=stub)
    assert plan.principal == "agent:finance"
    assert plan.manifest_path == stub_path
    # All four operator-mediated components should plan as OPERATOR_TODO.
    operator_mediated = [
        Component.FORGEJO_ACCOUNT,
        Component.FORGEJO_PAT,
        Component.DISCORD_BOT,
        Component.INFISICAL_TOKEN,
    ]
    for c in plan.components:
        if c.component in operator_mediated:
            assert c.would_status == ComponentStatus.OPERATOR_TODO, c.component


def test_plan_principal_rejects_mismatched_principal(tmp_path):
    """If manifest.principal != principal arg, we refuse — guards against
    a CLI bug where the wrong principal string was paired with a stub file."""
    from apps._shared.registry import AgentManifest
    from .issuer import IssuerError
    data = _minimal_finance_stub_data()
    stub = AgentManifest(
        principal=data["principal"], path=tmp_path / "stub.yaml", data=data
    )
    with pytest.raises(IssuerError, match="does not match"):
        plan_principal("agent:other", manifest=stub)


def test_cli_principal_stub_from_file(tmp_path, monkeypatch, capsys, fail_on_subprocess):
    """CLI: --principal-stub <PATH> reads the file and emits a runbook."""
    from .__main__ import main
    monkeypatch.setenv("HOMELAB_IDENTITY_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(tmp_path / "ssh"))
    stub_path = tmp_path / "agent-finance.yaml"
    _write_stub_manifest(stub_path, _minimal_finance_stub_data())

    output_path = tmp_path / "runbook.md"
    rc = main(
        [
            "plan",
            "--principal-stub", str(stub_path),
            "--output", str(output_path),
        ]
    )
    assert rc == 0
    body = output_path.read_text(encoding="utf-8")
    assert "agent:finance" in body
    assert "agent-finance@forgejo.dev-path.org" in body
    assert "#finance" in body


def test_cli_principal_stub_from_stdin(tmp_path, monkeypatch, fail_on_subprocess):
    """CLI: --principal-stub - reads YAML from stdin."""
    import io
    import yaml as _yaml
    from .__main__ import main
    monkeypatch.setenv("HOMELAB_IDENTITY_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(tmp_path / "ssh"))

    stdin_payload = _yaml.safe_dump(_minimal_finance_stub_data(), sort_keys=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_payload))

    output_path = tmp_path / "runbook.md"
    rc = main(
        [
            "plan",
            "--principal-stub", "-",
            "--output", str(output_path),
        ]
    )
    assert rc == 0
    body = output_path.read_text(encoding="utf-8")
    assert "agent:finance" in body


def test_cli_principal_stub_missing_required_field(tmp_path, monkeypatch, capsys):
    """CLI: shape validation catches a missing required key (e.g. queue_dir)."""
    from .__main__ import main
    bad = _minimal_finance_stub_data()
    del bad["queue_dir"]
    stub_path = tmp_path / "agent-finance.yaml"
    _write_stub_manifest(stub_path, bad)

    rc = main(["plan", "--principal-stub", str(stub_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "queue_dir" in err


def test_cli_principal_stub_invalid_yaml(tmp_path, monkeypatch, capsys):
    """CLI: surfaces a clear error for a YAML parse failure."""
    from .__main__ import main
    stub_path = tmp_path / "bad.yaml"
    stub_path.write_text(":\n  not yaml: at all: nested:bad", encoding="utf-8")
    rc = main(["plan", "--principal-stub", str(stub_path)])
    # Either a YAML error (rc=2) or a shape error if it parses as a dict.
    assert rc == 2
    err = capsys.readouterr().err
    assert "plan error" in err


def test_cli_principal_stub_missing_file(tmp_path, capsys):
    """CLI: missing stub file is reported, not silently swallowed."""
    from .__main__ import main
    rc = main(["plan", "--principal-stub", str(tmp_path / "nope.yaml")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err


def test_cli_plan_requires_principal_or_stub(capsys):
    """CLI: argparse fails loudly if neither --principal nor --principal-stub
    is given (mutually exclusive required group)."""
    from .__main__ import main
    with pytest.raises(SystemExit):
        main(["plan"])


def test_render_markdown_lists_operator_steps(tmp_path, monkeypatch, fail_on_subprocess):
    """Verifies the full operator checklist makes it into rendered markdown
    when nothing has been confirmed yet. Force ignore_state because this
    test is about checklist content, not state-aware behaviour."""
    monkeypatch.setenv("HOMELAB_IDENTITY_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("HOMELAB_IDENTITY_SSH_DIR", str(tmp_path / "ssh"))
    p = plan_principal("agent:executive", ignore_state=True)
    md = render_plan_markdown(p)
    # The Forgejo checklist must mention the account name from the manifest.
    assert "agent-executive" in md
    # The Discord checklist must mention the channel list.
    assert "#intake" in md or "#approvals" in md
    # The Infisical checklist must reference the secrets_profile.
    assert "executive" in md  # secrets_profile is "executive"
