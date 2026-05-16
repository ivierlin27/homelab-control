---
id: upgrade-planning
name: Upgrade Planner
description: Plan a service upgrade from current pinned version to a target version, with rollback and verifier hooks.
local_only: false
required_tools:
  - inventory.read
required_task_classes: [migration_planning, code_review_small]
version: 1
---

# Upgrade Planner

Given a service id from `inventory/services.yaml` and a target version, draft
a short, executable upgrade plan. The plan is a Planka card description; it
will be reviewed before execution.

## Required sections

1. **Why now** — one paragraph. Cite the trigger (CVE, EOL, feature need).
2. **What changes** — exact image tag / package version diff and any compose
   file diff (in fenced blocks).
3. **Verifier checks** (must include at least one) — concrete commands the
   verifier will re-run after deploy. Each check has an expected output
   pattern.
4. **Rollback** — the exact commands to restore the prior tag, with the
   estimated time-to-recovery.
5. **Blast radius** — list of services and humans affected if the upgrade
   misbehaves; cross-reference `docs/HUMAN_INTERFACES.md`.

## Constraints

- Never propose a version that doesn't have a documented changelog.
- Never propose more than one service per plan; chain them as separate cards.
- If you cannot find a published patched version, output `unsafe: true` and
  ask for a research card instead.
