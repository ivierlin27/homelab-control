"""Tests for the skill loader."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from apps._shared.registry import load_registry

from .loader import (
    SkillError,
    SkillRegistry,
    load_skill,
    load_skill_registry,
    skills_for_agent,
)


def _write_skill(root: Path, skill_id: str, *, fm: dict, body: str = "Body text.") -> Path:
    d = root / skill_id
    d.mkdir(parents=True, exist_ok=True)
    fm_yaml = yaml.safe_dump(fm, sort_keys=False)
    text = f"---\n{fm_yaml}---\n\n{body}\n"
    p = d / "SKILL.md"
    p.write_text(text)
    return p


# ---------------------------------------------------------------------------
# integration: real skills + real registry
# ---------------------------------------------------------------------------


def test_real_skills_load() -> None:
    reg = load_skill_registry()
    assert "planka-card" in reg.ids()
    skill = reg.get("planka-card")
    assert skill.description
    assert "planka.comment" in skill.required_tools


def test_every_manifest_skill_resolves_with_required_tools() -> None:
    sreg = load_skill_registry()
    areg = load_registry()
    for principal in areg.list_principals():
        manifest = areg.get(principal)
        # Should not raise
        skills = skills_for_agent(manifest, registry=sreg)
        assert isinstance(skills, list)


# ---------------------------------------------------------------------------
# loader unit tests
# ---------------------------------------------------------------------------


def test_load_skill_happy(tmp_path: Path) -> None:
    p = _write_skill(
        tmp_path,
        "good-skill",
        fm={
            "id": "good-skill",
            "name": "Good",
            "description": "A test skill.",
            "local_only": True,
            "required_tools": ["x.y"],
            "version": 1,
        },
    )
    skill = load_skill(p)
    assert skill.id == "good-skill"
    assert skill.local_only is True
    assert skill.required_tools == ("x.y",)


def test_load_skill_rejects_missing_frontmatter(tmp_path: Path) -> None:
    d = tmp_path / "bad"
    d.mkdir()
    (d / "SKILL.md").write_text("just a body\n")
    with pytest.raises(SkillError, match="front matter"):
        load_skill(d / "SKILL.md")


def test_load_skill_rejects_id_dir_mismatch(tmp_path: Path) -> None:
    p = _write_skill(
        tmp_path,
        "dir-name",
        fm={
            "id": "different-id",
            "description": "x",
        },
    )
    with pytest.raises(SkillError, match="does not match directory name"):
        load_skill(p)


def test_load_skill_rejects_empty_body(tmp_path: Path) -> None:
    d = tmp_path / "empty-body"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nid: empty-body\ndescription: x\n---\n\n   \n")
    with pytest.raises(SkillError, match="body is empty"):
        load_skill(d / "SKILL.md")


def test_load_skill_rejects_invalid_id(tmp_path: Path) -> None:
    p = _write_skill(
        tmp_path, "Bad_ID", fm={"id": "Bad_ID", "description": "x"}
    )
    with pytest.raises(SkillError, match="invalid or missing 'id'"):
        load_skill(p)


def test_load_skill_registry_loads_multiple(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", fm={"id": "alpha", "description": "x"})
    _write_skill(tmp_path, "beta", fm={"id": "beta", "description": "y"})
    reg = load_skill_registry(tmp_path)
    assert reg.ids() == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# manifest filter
# ---------------------------------------------------------------------------


def _mini_registry(tmp_path: Path, *, manifest_extra: dict) -> tuple:
    repo_root = tmp_path / "repo"
    (repo_root / "config" / "memory").mkdir(parents=True)
    (repo_root / "config" / "memory" / "principals.yaml").write_text(
        "principals:\n  - id: agent:t\n    kind: agent\n"
    )
    (repo_root / "config" / "agents").mkdir(parents=True)
    base_manifest = {
        "principal": "agent:t",
        "display_name": "T",
        "domain": "test",
        "queue_dir": "~/x",
        "references": {"memory_principal": "config/memory/principals.yaml"},
    }
    base_manifest.update(manifest_extra)
    (repo_root / "config" / "agents" / "agent-t.yaml").write_text(
        yaml.safe_dump(base_manifest)
    )
    (repo_root / "config" / "agents" / "registry.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "agents": [
                    {"principal": "agent:t", "manifest": "config/agents/agent-t.yaml"}
                ],
            }
        )
    )
    return repo_root


def test_skills_for_agent_missing_tool_raises(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir,
        "needs-tool",
        fm={
            "id": "needs-tool",
            "description": "x",
            "required_tools": ["foo.bar"],
        },
    )
    sreg = load_skill_registry(skills_dir)
    repo_root = _mini_registry(
        tmp_path, manifest_extra={"skills": ["needs-tool"], "tools": ["other.tool"]}
    )
    areg = load_registry(
        repo_root / "config" / "agents" / "registry.yaml", repo_root=repo_root
    )
    with pytest.raises(SkillError, match="not granted"):
        skills_for_agent(areg.get("agent:t"), registry=sreg)


def test_skills_for_agent_local_only_filtered_on_cloud_route(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir,
        "local-skill",
        fm={
            "id": "local-skill",
            "description": "x",
            "local_only": True,
        },
    )
    _write_skill(
        skills_dir,
        "any-skill",
        fm={
            "id": "any-skill",
            "description": "y",
            "local_only": False,
        },
    )
    sreg = load_skill_registry(skills_dir)
    repo_root = _mini_registry(
        tmp_path,
        manifest_extra={
            "skills": ["local-skill", "any-skill"],
            "tools": [],
        },
    )
    areg = load_registry(
        repo_root / "config" / "agents" / "registry.yaml", repo_root=repo_root
    )
    manifest = areg.get("agent:t")
    local = skills_for_agent(manifest, registry=sreg, route="local-fast")
    cloud = skills_for_agent(manifest, registry=sreg, route="cloud-frontier")
    assert [s.id for s in local] == ["local-skill", "any-skill"]
    assert [s.id for s in cloud] == ["any-skill"]


def test_skills_for_agent_unknown_skill_raises(tmp_path: Path) -> None:
    sreg = SkillRegistry(skills={}, root=tmp_path)
    repo_root = _mini_registry(
        tmp_path, manifest_extra={"skills": ["does-not-exist"], "tools": []}
    )
    areg = load_registry(
        repo_root / "config" / "agents" / "registry.yaml", repo_root=repo_root
    )
    with pytest.raises(SkillError, match="unknown skill"):
        skills_for_agent(areg.get("agent:t"), registry=sreg)
