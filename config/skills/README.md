# Skill Registry

Skills are scoped, agent-loadable instruction packages. Each skill lives in
`config/skills/<skill-id>/SKILL.md`. An agent only sees the skills its
manifest names in `skills: [...]`.

This mirrors the pattern Cursor's user-skills system uses, but enforced
locally by the platform — there is no global skill pool.

## Skill format (SKILL.md)

Each `SKILL.md` is markdown with a YAML front-matter block:

```markdown
---
id: planka-card
name: Planka Card Updater
description: Append progress as comments on the Planka card driving the task.
local_only: false           # if true, refuses to load when route is cloud
required_tools:             # tools that must be granted in the manifest
  - planka.comment
  - planka.move_to_list
required_task_classes: []   # optional gate; empty = any task class
version: 1
---

# Planka Card Updater

Body is the actual instruction the agent is given when this skill is loaded.
Use natural-language imperatives, not chat dialogue. Cite tool names exactly.
```

Every `SKILL.md` must:

- Have an `id` matching the directory name.
- Declare `description` (one sentence; surfaces in dashboards).
- Declare `local_only` (bool, default `false`).
- Declare `required_tools` (may be empty).
- Have a non-empty body after the front matter.

## Loading rules

The loader filters skills against the agent's manifest:

1. Only skills whose id is in `manifest.skills` load.
2. If the skill declares `required_tools`, every named tool must be present in
   `manifest.tools`. Otherwise the loader raises `SkillError`.
3. If the skill declares `local_only: true` and the agent's currently-resolved
   route is not `local-*`, the skill is omitted (and a `tier_transition` is
   recorded later in 0.11).

## CLI

```bash
python3 -m apps._shared.skills list
python3 -m apps._shared.skills show planka-card
python3 -m apps._shared.skills load --principal agent:executive
python3 -m apps._shared.skills load --principal agent:executive --route cloud-frontier
python3 -m apps._shared.skills validate
```

`validate` runs every SKILL.md through the front-matter checks and confirms
that every skill referenced from a registered agent manifest exists on disk.

## Layout

```
config/skills/
├── README.md              this file
├── planka-card/SKILL.md
├── intake-classify/SKILL.md
├── weekly-review/SKILL.md
├── homelab-inventory/SKILL.md
├── cve-triage/SKILL.md
├── upgrade-planning/SKILL.md
├── render-plan/SKILL.md
├── execute-task/SKILL.md
└── pr-review/SKILL.md
```

Add a new skill: create `config/skills/<id>/SKILL.md`, then list its id in
the relevant agent manifest's `skills:` field. Re-run
`python3 -m apps._shared.skills validate` and `python3 -m apps._shared.registry validate`.
