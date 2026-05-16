"""Skill loader: read SKILL.md files, validate, filter by agent manifest."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

import yaml

from apps._shared.registry import AgentManifest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SKILLS_DIR = REPO_ROOT / "config" / "skills"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_VALID_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class SkillError(Exception):
    """Raised on schema or load errors."""


@dataclass(frozen=True)
class Skill:
    """A loaded SKILL.md."""

    id: str
    name: str
    description: str
    body: str
    local_only: bool
    required_tools: tuple[str, ...]
    required_task_classes: tuple[str, ...]
    version: int
    source_path: Path

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "local_only": self.local_only,
            "required_tools": list(self.required_tools),
            "required_task_classes": list(self.required_task_classes),
            "version": self.version,
            "source_path": str(self.source_path),
        }


@dataclass(frozen=True)
class SkillRegistry:
    """All skills found on disk, keyed by id."""

    skills: Mapping[str, Skill]
    root: Path

    def get(self, skill_id: str) -> Skill:
        try:
            return self.skills[skill_id]
        except KeyError as exc:
            raise SkillError(f"unknown skill: {skill_id!r}") from exc

    def ids(self) -> list[str]:
        return sorted(self.skills.keys())


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------


def default_skills_dir() -> Path:
    return DEFAULT_SKILLS_DIR


def load_skill(path: Path) -> Skill:
    """Parse one SKILL.md file."""

    if not path.is_file():
        raise SkillError(f"not a file: {path}")
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        raise SkillError(f"{path}: missing YAML front matter (--- ... ---)")
    fm_text, body = match.group(1), match.group(2).strip()
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"{path}: invalid YAML in front matter: {exc}") from exc
    if not isinstance(fm, dict):
        raise SkillError(f"{path}: front matter must be a YAML mapping")

    skill_id = fm.get("id")
    if not isinstance(skill_id, str) or not _VALID_ID.match(skill_id):
        raise SkillError(f"{path}: invalid or missing 'id' (got {skill_id!r})")
    if skill_id != path.parent.name:
        raise SkillError(
            f"{path}: id {skill_id!r} does not match directory name {path.parent.name!r}"
        )

    name = fm.get("name") or skill_id
    description = fm.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillError(f"{path}: 'description' is required and must be a non-empty string")
    if not body:
        raise SkillError(f"{path}: body is empty after front matter")

    local_only = bool(fm.get("local_only", False))

    required_tools = fm.get("required_tools") or []
    if not isinstance(required_tools, list) or not all(isinstance(t, str) for t in required_tools):
        raise SkillError(f"{path}: 'required_tools' must be a list of strings")

    required_task_classes = fm.get("required_task_classes") or []
    if not isinstance(required_task_classes, list) or not all(
        isinstance(t, str) for t in required_task_classes
    ):
        raise SkillError(f"{path}: 'required_task_classes' must be a list of strings")

    version = fm.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise SkillError(f"{path}: 'version' must be a positive integer")

    return Skill(
        id=skill_id,
        name=str(name),
        description=description.strip(),
        body=body,
        local_only=local_only,
        required_tools=tuple(required_tools),
        required_task_classes=tuple(required_task_classes),
        version=version,
        source_path=path,
    )


def load_skill_registry(root: Path | None = None) -> SkillRegistry:
    """Load every SKILL.md beneath ``root`` (default: ``config/skills``)."""

    root = (root or default_skills_dir()).resolve()
    if not root.is_dir():
        raise SkillError(f"skills root not found: {root}")
    skills: dict[str, Skill] = {}
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            # tolerate empty subdirs (e.g. README placeholders)
            continue
        skill = load_skill(skill_md)
        if skill.id in skills:
            raise SkillError(
                f"duplicate skill id {skill.id!r}: {skill.source_path} vs "
                f"{skills[skill.id].source_path}"
            )
        skills[skill.id] = skill
    return SkillRegistry(skills=skills, root=root)


# ---------------------------------------------------------------------------
# manifest filter
# ---------------------------------------------------------------------------


def skills_for_agent(
    manifest: AgentManifest,
    *,
    registry: SkillRegistry | None = None,
    route: str | None = None,
) -> list[Skill]:
    """Return the ordered list of skills the agent is allowed to load.

    Validates that:

    - every id in ``manifest.skills`` exists in the skill registry
    - every skill's ``required_tools`` is a subset of ``manifest.tools``
    - if ``route`` is given and is not ``local-*``, ``local_only`` skills are
      omitted (rather than raising) — that's the runtime gate for routing
      transitions in 0.11

    Raises ``SkillError`` on misconfiguration; route-based filtering is silent.
    """

    registry = registry or load_skill_registry()
    requested = manifest.get("skills", default=[]) or []
    if not isinstance(requested, list):
        raise SkillError(
            f"{manifest.principal}: 'skills' must be a list, got {type(requested).__name__}"
        )
    granted_tools = set(manifest.get("tools", default=[]) or [])

    out: list[Skill] = []
    for skill_id in requested:
        if not isinstance(skill_id, str):
            raise SkillError(f"{manifest.principal}: skill ids must be strings")
        skill = registry.get(skill_id)
        missing = [t for t in skill.required_tools if t not in granted_tools]
        if missing:
            raise SkillError(
                f"{manifest.principal}: skill {skill_id!r} requires tools "
                f"{missing} not granted in manifest"
            )
        if skill.local_only and route is not None and not route.startswith("local"):
            continue
        out.append(skill)
    return out
