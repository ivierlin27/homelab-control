# Project Agents

Project agents make the local AI stack explicit: each agent owns one domain,
one memory scope, one tool surface, and one routing policy. The executive
assistant remains the front door, but it should route work into project agents
instead of acting like a universal superuser.

This document is the contract every project agent should declare before it is
enabled.

## Why this exists

The stack already has:

- an executive intake surface
- author and review agents
- a shared model gateway
- a shared memory system with provenance

What it needs is a repeatable shape for domain-specific agents so each project
can have:

- distinct trust posture
- distinct data boundaries
- distinct tool grants
- explicit local-vs-cloud routing
- clear human interaction surfaces

## Required contract

Every project agent should declare the fields below in a policy file or agent
descriptor.

### 1. Identity

- `principal`: stable principal name, for example `agent:homelab-maintainer`
- `domain`: human-facing domain slug, for example `homelab`
- `display_name`: short title for dashboards and chat surfaces
- `git_identity`: repo-write identity when the agent can open branches or PRs
- `memory_principal`: principal written into memory-engine records

Rules:

- never reuse a human principal
- never share repo-write identities between unrelated project agents
- prefer one queue root per project agent

### 2. Data scope

Each agent must declare what it may read and write.

- `memory.read_prefixes`: record-key or namespace prefixes it may read
- `memory.write_prefixes`: prefixes it may write
- `files.allowed_roots`: repo or filesystem roots it may inspect
- `files.blocked_roots`: explicit deny-list for sensitive paths
- `secrets_profile`: machine-secret scope, or `none`

Examples:

- `agent:homelab-maintainer` -> reads/writes `homelab.*`
- `agent:finance` -> reads/writes `finance.*`, no cloud by default
- `agent:knowledge` -> reads `knowledge.raw.*`, writes `knowledge.notes.*`

### 3. Tool grants

Tool grants should be treated as permissions, not conveniences.

Each agent declares:

- `planka`: allowed boards/lists/actions
- `memory`: allowed search/write operations
- `queue`: which downstream queues it may enqueue into
- `forgejo`: read-only, review-only, or authoring scope
- `shell`: allowed command families, if any
- `network`: allowed egress hosts or `none`
- `mcp_tools`: explicit allowlist when MCP is used

Default rule: if a tool is not listed, the agent does not get it.

### 4. Routing policy

Project agents do not pick raw model names. They declare allowed task classes
and routing posture. The gateway shim resolves symbolic intent to the current
provider and model tier.

Recommended shape:

```yaml
routing_policy:
  local_only:
    - secrets
    - private_memory_update
  local_preferred:
    - classify
    - summarize
    - code_review_small
  cloud_allowed:
    - architecture_synthesis
    - migration_planning
  cloud_required_review: true
  route_overrides:
    summarize: local-fast
    code_review_small: local-strong
```

Notes:

- `local_only` means the request is blocked if only a cloud path is available
- `local_preferred` means local first, cloud optional with audit
- `cloud_allowed` means cloud use is permitted but still recorded
- every cloud-bound decision should log `(project, task_class, route, reason)`

### 5. Trust posture

Each agent starts with a bounded trust level and earns more.

Required fields:

- `autonomy_mode`: `propose_only`, `low_risk_auto`, or `domain_auto`
- `promotion_rules`: what evidence promotes the agent
- `demotion_rules`: what failures or blocked actions demote it
- `human_review_triggers`: labels, path prefixes, task classes, or data classes
- `audit_stream`: where trust events are written

Recommended interpretation:

- `propose_only`: may classify, summarize, write memory proposals, and enqueue
  human-review work
- `low_risk_auto`: may perform explicitly approved safe actions inside declared
  paths and namespaces
- `domain_auto`: still bounded by scope, but can perform routine domain work
  without re-approval

### 6. Surfaces

Each project agent must list the human entry points it participates in.

- `planka_domain`
- `chat_tags`
- `discord_allowed_channels`
- `dashboard_slug`
- `weekly_review_enabled`
- `intake_match_hints`

This lets the executive assistant and intake layer route raw inputs into the
correct project without hard-coding domain logic in one place.

## Shared policy shape

The following YAML shape is the expected baseline for new project policies:

```yaml
principal: agent:example
project:
  domain: example
  display_name: Example Maintainer
  queue_dir: ~/.local/state/homelab-control/agent-example

identity:
  git_author_name: agent-example
  git_author_email: agent-example@forgejo.dev-path.org
  memory_principal: agent:example

data_scope:
  memory:
    read_prefixes: [example.*]
    write_prefixes: [example.*]
  files:
    allowed_roots: [docs/example, inventory/example]
    blocked_roots: [secrets, finance]
  secrets_profile: none

tool_grants:
  planka:
    enabled: true
    boards: [homelab]
    actions: [create_card, comment]
  queue:
    enqueue_targets: [agent-review]
  memory:
    read: true
    write: true
  shell:
    allowed_commands: []
  network:
    allowed_hosts: []

routing_policy:
  local_only: [private_memory_update]
  local_preferred: [classify, summarize]
  cloud_allowed: []
  cloud_required_review: true
  route_overrides: {}

trust:
  autonomy_mode: propose_only
  promotion_rules:
    - two_consecutive_low_risk_successes
  demotion_rules:
    - blocked_sensitive_action
    - repeated_human_rejection
  human_review_triggers:
    labels: [sensitive]
    paths: []
    task_classes: [architecture_synthesis]

surfaces:
  planka_domain: example
  chat_tags: [example]
  discord_allowed_channels: []
  dashboard_slug: example
  weekly_review_enabled: false
  intake_match_hints: [example]
```

## Reference project: homelab maintainer

The first concrete reference agent should be `agent:homelab-maintainer`.

Recommended declaration:

- principal: `agent:homelab-maintainer`
- memory scope: read/write `homelab.*`
- tool grants:
  - Planka card creation/comments for homelab board
  - inventory read
  - enqueue into author and review queues
  - memory search/write
  - no direct git push
  - no human credentials
  - no secrets access
- routing:
  - `local_only`: `secrets`, `infrastructure_change_planning`
  - `local_preferred`: `summarize`, `classify`, `inventory_refresh`,
    `code_review_small`
  - `cloud_allowed`: `architecture_synthesis`, `migration_planning`
- trust:
  - start at `propose_only` for changes touching `compose/` or `inventory/`
  - allow low-risk memory writes and Planka maintenance automatically

## Namespace reservations

Reserve these memory namespaces now so future project agents can be added
without redesigning the memory layout:

- `homelab.*`
- `finance.*`
- `language.*`
- `knowledge.*`
- `intake.*` for unsorted discovery artifacts
- `trust.*` for cross-agent trust summaries and dashboards

Suggested sub-prefixes:

- `*.raw.*` for unprocessed source material
- `*.facts.*` for durable structured facts
- `*.notes.*` for generated summaries or human-curated notes
- `*.scratch.*` for temporary working state with TTL

## Copy-paste templates for future agents

These are intentionally design-only for now. They should reuse the contract
above and differ mostly by scope and routing.

### Finance agent

Use when handling statements, budgeting, tax prep, or sensitive account notes.

- principal: `agent:finance`
- namespace: `finance.*`
- routing: local-only by default
- tool grants:
  - memory read/write inside `finance.*`
  - no public network egress
  - no repo writes by default
- trust: starts `propose_only`
- surfaces:
  - dedicated Planka domain or board
  - dedicated chat tag
  - explicit allowlist for any interactive channel

### Language agent

Use for vocabulary, drills, journaling feedback, or spaced repetition support.

- principal: `agent:language`
- namespace: `language.*`
- routing: local-preferred, cloud allowed for grading or translation review
- tool grants:
  - memory read/write
  - optional spaced repetition export
  - no shell by default
- trust: low-risk auto for note generation and drill scheduling

### Personal knowledge agent

Use for notes, drafts, project history, journaling, and long-term retrieval.

- principal: `agent:knowledge`
- namespace: `knowledge.*`
- routing: local-preferred
- tool grants:
  - read markdown / notes roots
  - write derived notes and structured memory records
  - no external publishing tools
- trust: low-risk auto for indexing and derived summaries, propose-only for any
  destructive or archival actions

## Intake compatibility rules

The discovery/intake layer should use this contract to route raw inputs:

1. match against `surfaces.intake_match_hints`
2. restrict candidate projects to those whose data/tool/routing policies permit
   the requested task class
3. if no project matches, store under `intake.*`
4. if multiple projects match, escalate with candidate scores instead of making
   a silent choice

## Audit rules

Every project agent should emit trust and lifecycle events with:

- `principal`
- `project_domain`
- `task_class`
- `route`
- `source`
- `artifact_url`
- `allowed_by_policy`
- `requires_human_review`

This keeps the dashboard and memory layer inspectable even as models and
providers change over time.
