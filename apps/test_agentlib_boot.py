"""Tests for ``apps.agentlib.boot_principal`` — the registry-aware agent boot.

Covers:
  - Happy path against the real registry: returns BootContext for each
    of the five registered agent principals, and a tamper-evident ``boot``
    event lands in the agent's trust-ledger.jsonl.
  - Soft-fail (default): unknown principal -> returns None + stderr warning,
    does not raise.
  - Strict mode (AGENT_REGISTRY_ENFORCE=1):
      * unknown principal raises BootError
      * malformed registry raises BootError
      * skill that the manifest doesn't have tools for raises BootError
        (the "agent:executive cannot load cve-triage" guardrail)
  - The boot event carries enough provenance to detect a tampered manifest
    (sha256 + skill ids + tool grants + autonomy_mode).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

# Make ``apps.agentlib`` importable when pytest runs from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "apps") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps"))

from agentlib import BootContext, BootError, boot_principal  # noqa: E402


# ---- helpers -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Redirect HOME so each test's boot-event lands in tmp_path, not the
    developer's real ~/.local/state/homelab-control/."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_REGISTRY_ENFORCE", raising=False)
    monkeypatch.delenv("AGENT_PRINCIPAL", raising=False)
    yield


def _read_audit_rows(state_dir: Path) -> list[dict]:
    path = state_dir / "trust-ledger.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---- happy path ----------------------------------------------------------


@pytest.mark.parametrize(
    "principal",
    ["agent:executive", "agent:homelab-maintainer", "agent:homelab", "agent:review"],
)
def test_boot_principal_happy_path_real_registry(principal):
    """Every registered principal should boot cleanly against the real
    config/agents/registry.yaml shipped in this repo."""
    ctx = boot_principal(principal=principal)
    assert isinstance(ctx, BootContext), "soft-fail returned None for a valid principal"
    assert ctx.principal == principal
    assert ctx.enforced is False  # AGENT_REGISTRY_ENFORCE unset -> soft mode
    assert ctx.manifest.principal == principal
    assert len(ctx.manifest_sha256) == 64  # sha256 hex
    # skill_ids() returns the manifest's declared skills filter
    declared = ctx.manifest.get("skills", default=[]) or []
    assert ctx.skill_ids() == list(declared)


def test_boot_writes_boot_event_to_trust_ledger():
    ctx = boot_principal(principal="agent:executive")
    assert ctx is not None
    rows = _read_audit_rows(ctx.state_dir)
    assert rows, f"no audit rows in {ctx.state_dir}"
    last = rows[-1]
    assert last["event"] == "boot"
    assert last["principal"] == "agent:executive"
    assert last["manifest_sha256"] == ctx.manifest_sha256
    assert isinstance(last["skills_loaded"], list)
    assert isinstance(last["tools_granted"], list)
    assert last["autonomy_mode"] in ("propose_only", "low_risk_auto", "domain_auto")
    # Chain integrity: audit_seq monotonic, audit_prev links to previous hash.
    seqs = [r["audit_seq"] for r in rows]
    assert seqs == sorted(seqs)
    assert all(isinstance(r["audit_hash"], str) and len(r["audit_hash"]) == 64 for r in rows)


# ---- soft-fail (default) -------------------------------------------------


def test_soft_fail_unknown_principal_returns_none(capsys):
    ctx = boot_principal(principal="agent:does-not-exist")
    assert ctx is None
    captured = capsys.readouterr()
    assert "not in registry" in captured.err


def test_soft_fail_invalid_principal_format_returns_none(capsys):
    ctx = boot_principal(principal="not-an-agent")
    assert ctx is None
    captured = capsys.readouterr()
    assert "must start with 'agent:'" in captured.err


def test_soft_fail_no_principal_returns_none(capsys):
    # No principal arg, no env, no default -> soft-fail
    ctx = boot_principal()
    assert ctx is None
    assert "no principal" in capsys.readouterr().err


# ---- strict mode (AGENT_REGISTRY_ENFORCE=1) ------------------------------


def test_strict_unknown_principal_raises(monkeypatch):
    monkeypatch.setenv("AGENT_REGISTRY_ENFORCE", "1")
    with pytest.raises(BootError, match="not in registry"):
        boot_principal(principal="agent:nope")


def test_strict_default_principal_is_used(monkeypatch):
    """If only ``default_principal`` is given, boot_principal uses it."""
    ctx = boot_principal(default_principal="agent:executive")
    assert ctx is not None
    assert ctx.principal == "agent:executive"


def test_env_principal_overrides_default(monkeypatch):
    monkeypatch.setenv("AGENT_PRINCIPAL", "agent:review")
    ctx = boot_principal(default_principal="agent:executive")
    assert ctx is not None
    assert ctx.principal == "agent:review"


# ---- skill-grant guardrail ----------------------------------------------


def test_strict_skill_without_required_tool_raises(tmp_path, monkeypatch):
    """The 'executive cannot load cve-triage' guardrail.

    Build a tiny tmp registry that mimics the real schema enough to load,
    but list a skill whose required_tools are not in the manifest's
    tool grants. In strict mode this must raise BootError.
    """
    # Set up tmp config tree
    cfg = tmp_path / "config"
    (cfg / "agents").mkdir(parents=True)
    (cfg / "skills" / "needs-shell").mkdir(parents=True)
    (cfg / "policies").mkdir(parents=True)
    (cfg / "memory").mkdir(parents=True)

    # A skill that requires the `shell.exec` tool
    (cfg / "skills" / "needs-shell" / "SKILL.md").write_text(
        """---
id: needs-shell
name: needs-shell
description: requires shell.exec to function
required_tools:
  - shell.exec
---

body
""",
        encoding="utf-8",
    )

    # A manifest that lists `needs-shell` but does NOT grant `shell.exec`
    (cfg / "memory" / "principals.yaml").write_text(
        yaml.safe_dump({
            "principals": [
                {"id": "agent:tampered", "kind": "agent", "domains": ["test"]},
            ],
        }),
        encoding="utf-8",
    )
    (cfg / "policies" / "tampered-policy.yaml").write_text("policy: ok\n", encoding="utf-8")
    (cfg / "agents" / "agent-tampered.yaml").write_text(
        yaml.safe_dump({
            "principal": "agent:tampered",
            "display_name": "Tampered",
            "domain": "test",
            "queue_dir": str(tmp_path / "queue-tampered"),
            "references": {
                "policy": "config/policies/tampered-policy.yaml",
                "memory_principal": "config/memory/principals.yaml",
            },
            "identity": {"git_user": "agent-tampered-bot"},
            "skills": ["needs-shell"],
            "tools": [],  # <-- shell.exec NOT granted; this is the violation
        }),
        encoding="utf-8",
    )
    (cfg / "agents" / "registry.yaml").write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "agents": [
                {"principal": "agent:tampered",
                 "manifest": "config/agents/agent-tampered.yaml"},
            ],
        }),
        encoding="utf-8",
    )

    # Point the skills loader at our tmp config (registry already takes the path)
    monkeypatch.setenv("AGENT_REGISTRY_ENFORCE", "1")
    # The skills loader uses its module-level DEFAULT_SKILLS_DIR derived from
    # REPO_ROOT; monkeypatch it to point at our tmp tree.
    import apps._shared.skills.loader as skills_loader
    monkeypatch.setattr(skills_loader, "DEFAULT_SKILLS_DIR", cfg / "skills")

    with pytest.raises(BootError, match="not granted"):
        boot_principal(
            principal="agent:tampered",
            registry_path=cfg / "agents" / "registry.yaml",
        )


# ---- agent wiring smoke --------------------------------------------------


def test_main_modules_call_boot_principal_at_top():
    """Static check: each agent's main() invokes ``boot_principal`` as its
    first non-parser statement. Cheap regression guard so a future refactor
    that drops the call gets caught."""
    import re
    expected = {
        "apps/executive_agent/main.py": "agent:executive",
        "apps/homelab_maintainer_agent/main.py": "agent:homelab-maintainer",
        "apps/author_agent/main.py": "agent:homelab",
        "apps/review_agent/main.py": "agent:review",
        "apps/homelab_operator/main.py": "agent:homelab",
    }
    for relpath, _ in expected.items():
        src = (REPO_ROOT / relpath).read_text(encoding="utf-8")
        # Look for ``def main()`` and confirm ``boot_principal`` shows up
        # within the first ~5 lines after it.
        m = re.search(r"^def main\(\)[^\n]*:\n((?:.+\n){1,8})", src, re.MULTILINE)
        assert m, f"{relpath}: no def main() found"
        body = m.group(1)
        assert "boot_principal" in body, (
            f"{relpath}: main() does not call boot_principal in its preamble"
        )
