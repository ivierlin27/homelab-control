# Capability Registry

This directory is the single source of truth for which agents exist and what
they may do. Each agent has one manifest at `config/agents/<principal>.yaml`,
and `registry.yaml` is the index that the executive (and every platform tool)
reads on startup.

The schema is defined in [registry.schema.yaml](registry.schema.yaml) and
enforced by `apps/_shared/registry/`.

## Layout

- `registry.schema.yaml` — formal schema for manifest + index files
- `registry.yaml` — list of registered agent principals + manifest paths
- `agent-<name>.yaml` — per-agent manifest (one file per principal)

## Source-of-truth split

Existing config files keep their roles; the manifest references them rather
than duplicating their contents:

- memory principal entry -> `config/memory/principals.yaml`
- domain trust + Shield + symbolic routes -> `config/policies/<name>.yaml`
- review/auto-merge rules -> `config/policies/review-policy.yaml`

The manifest adds the new platform fields the existing files do not cover:
identity, sandbox, skills, tools, A2A allowlist, Discord presence, escalation
overrides.

## Validate

```bash
python3 -m apps._shared.registry validate
python3 -m apps._shared.registry list
python3 -m apps._shared.registry show agent:homelab-maintainer
```

`validate` exits non-zero on any schema or cross-file integrity violation
(duplicate git user, unknown referenced principal, missing memory entry, etc.).
