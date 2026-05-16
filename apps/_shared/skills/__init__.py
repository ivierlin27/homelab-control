"""Per-agent skill registry.

A skill is a SKILL.md file under ``config/skills/<id>/`` with a YAML
front-matter header describing what tools and routes it requires. The loader
filters skills against an agent manifest so each agent only ever sees the
skills it is authorized to load.

See `config/skills/README.md` for the format and `docs/plans/phase-0-platform.md`
section 0.8 for the design.
"""

from .loader import (
    Skill,
    SkillError,
    SkillRegistry,
    default_skills_dir,
    load_skill,
    load_skill_registry,
    skills_for_agent,
)

__all__ = [
    "Skill",
    "SkillError",
    "SkillRegistry",
    "default_skills_dir",
    "load_skill",
    "load_skill_registry",
    "skills_for_agent",
]
