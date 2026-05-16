"""Tests for the capability registry loader."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from .loader import (
    DEFAULT_REGISTRY_PATH,
    RegistryError,
    load_registry,
)


# ---------------------------------------------------------------------------
# integration: the real registry on disk loads cleanly
# ---------------------------------------------------------------------------


def test_real_registry_loads() -> None:
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    assert registry.schema_version == 1
    assert "agent:executive" in registry.agents
    assert "agent:homelab-maintainer" in registry.agents
    assert "agent:homelab" in registry.agents
    assert "agent:review" in registry.agents
    exec_manifest = registry.get("agent:executive")
    assert exec_manifest.get("identity", "git_user") == "agent-executive"
    assert "agent:homelab-maintainer" in (
        exec_manifest.get("a2a", "allowed_callees") or []
    )


# ---------------------------------------------------------------------------
# unit: tmp_path scenarios
# ---------------------------------------------------------------------------


def _scaffold_repo(root: Path) -> None:
    """Lay down a minimal repo skeleton: principals.yaml + agents/ index."""
    (root / "config" / "memory").mkdir(parents=True)
    (root / "config" / "memory" / "principals.yaml").write_text(
        dedent(
            """
            principals:
              - id: agent:foo
                kind: agent
              - id: agent:bar
                kind: agent
            """
        ).strip()
        + "\n"
    )
    (root / "config" / "agents").mkdir(parents=True)


def _write_manifest(
    root: Path, principal: str, *, git_user: str | None = None, callees: list[str] | None = None
) -> Path:
    name = principal.replace("agent:", "agent-")
    body = {
        "principal": principal,
        "display_name": principal,
        "domain": "test",
        "queue_dir": f"~/.local/state/test/{name}",
        "references": {"memory_principal": "config/memory/principals.yaml"},
    }
    if git_user is not None:
        body["identity"] = {"git_user": git_user}
    if callees is not None:
        body["a2a"] = {"allowed_callees": callees}
    import yaml

    path = root / "config" / "agents" / f"{name}.yaml"
    path.write_text(yaml.safe_dump(body, sort_keys=False))
    return path


def _write_index(root: Path, entries: list[tuple[str, str]]) -> Path:
    import yaml

    path = root / "config" / "agents" / "registry.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "agents": [
                    {"principal": p, "manifest": m} for p, m in entries
                ],
            },
            sort_keys=False,
        )
    )
    return path


def test_minimal_valid(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    _write_manifest(tmp_path, "agent:foo", git_user="agent-foo")
    index = _write_index(tmp_path, [("agent:foo", "config/agents/agent-foo.yaml")])
    registry = load_registry(index, repo_root=tmp_path)
    assert registry.list_principals() == ["agent:foo"]


def test_duplicate_git_user(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    _write_manifest(tmp_path, "agent:foo", git_user="shared")
    _write_manifest(tmp_path, "agent:bar", git_user="shared")
    index = _write_index(
        tmp_path,
        [
            ("agent:foo", "config/agents/agent-foo.yaml"),
            ("agent:bar", "config/agents/agent-bar.yaml"),
        ],
    )
    with pytest.raises(RegistryError, match="git_user"):
        load_registry(index, repo_root=tmp_path)


def test_unknown_callee(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    _write_manifest(tmp_path, "agent:foo", callees=["agent:nonexistent"])
    index = _write_index(tmp_path, [("agent:foo", "config/agents/agent-foo.yaml")])
    with pytest.raises(RegistryError, match="unknown principal"):
        load_registry(index, repo_root=tmp_path)


def test_self_callee_rejected(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    _write_manifest(tmp_path, "agent:foo", callees=["agent:foo"])
    index = _write_index(tmp_path, [("agent:foo", "config/agents/agent-foo.yaml")])
    with pytest.raises(RegistryError, match="may not include self"):
        load_registry(index, repo_root=tmp_path)


def test_principal_mismatch(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    _write_manifest(tmp_path, "agent:foo")
    # index claims a different principal
    import yaml

    index = tmp_path / "config" / "agents" / "registry.yaml"
    index.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "agents": [
                    {"principal": "agent:bar", "manifest": "config/agents/agent-foo.yaml"}
                ],
            }
        )
    )
    with pytest.raises(RegistryError, match="does not match"):
        load_registry(index, repo_root=tmp_path)


def test_principal_not_in_principals_file(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    _write_manifest(tmp_path, "agent:ghost")  # not in principals.yaml
    index = _write_index(tmp_path, [("agent:ghost", "config/agents/agent-ghost.yaml")])
    with pytest.raises(RegistryError, match="not found in"):
        load_registry(index, repo_root=tmp_path)


def test_invalid_principal_pattern(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    # write a manifest with an invalid principal directly
    import yaml

    bad = tmp_path / "config" / "agents" / "agent-bad.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "principal": "human:foo",  # wrong prefix
                "display_name": "x",
                "domain": "x",
                "queue_dir": "~/x",
            }
        )
    )
    index = _write_index(tmp_path, [("human:foo", "config/agents/agent-bad.yaml")])
    with pytest.raises(RegistryError, match="agent:"):
        load_registry(index, repo_root=tmp_path)


def test_bad_autonomy_mode(tmp_path: Path) -> None:
    _scaffold_repo(tmp_path)
    import yaml

    path = tmp_path / "config" / "agents" / "agent-foo.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "principal": "agent:foo",
                "display_name": "Foo",
                "domain": "test",
                "queue_dir": "~/x",
                "trust": {"autonomy_mode": "yolo"},
            }
        )
    )
    index = _write_index(tmp_path, [("agent:foo", "config/agents/agent-foo.yaml")])
    with pytest.raises(RegistryError, match="autonomy_mode"):
        load_registry(index, repo_root=tmp_path)
