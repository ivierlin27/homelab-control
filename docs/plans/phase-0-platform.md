# Phase 0: Platform Hardening

The platform-level work that every later domain agent inherits. Each item below
is small enough to land as one or two PRs.

Decisions locked at planning time:

- **Sandbox runtime:** rootless Podman (no Docker daemon).
- **SSO / IdP:** Authentik (OIDC for Forgejo / Planka / Khoj / Fava / dashboard;
  self-service portal for family/friend onboarding).
- **Backup target:** local NAS over SFTP via restic. Offsite deferred.
- **Master dashboard tech:** FastAPI + Jinja2 + HTMX + Tailwind, server-rendered,
  SSE for live tiles. Grafana embedded for metrics deep-dives.
- **Knowledge browser:** Quartz v4 publishes the Obsidian vault to
  `kb.dev-path.org` behind Authentik. Obsidian on Mac is the read/write
  workstation surface; Syncthing keeps both in sync.
- **Discord:** single existing guild; per-agent bots; threads per Planka card;
  sensitive agents read-only from Discord.
- **Plain finance ledger:** Beancount + Fava (Phase 1, listed for context).

## Status

| Item                               | Section | Status      |
| ---------------------------------- | ------- | ----------- |
| Capability registry                | 0.5     | done        |
| Sandbox runner                     | 0.1     | not started |
| Identity issuer                    | 0.2     | not started |
| Hash-chained audit                 | 0.3     | not started |
| Verifier-loop primitive            | 0.4     | not started |
| Gateway cost/latency log           | 0.6     | not started |
| Per-agent Discord presence         | 0.7     | not started |
| Per-agent skill registry           | 0.8     | not started |
| Inter-agent communication (A2A)    | 0.9     | not started |
| Sub-agent spawner                  | 0.10    | not started |
| Tiered escalation                  | 0.11    | not started |
| Master dashboard + KB browser      | 0.12    | not started |
| Backup + restore                   | 0.13    | not started |

---

## 0.1 Sandbox runner — `apps/_shared/sandbox/`

Rootless Podman wrapped in a sandcastle-style API:

- Inputs: `agent_principal`, `branch_strategy`, `tool_grants`, `network`
  allowlist (default deny), `mounts` (default worktree only).
- Outputs: stdout/stderr stream, commit SHAs, captured session JSONL, exit
  reason.
- Per-agent base images at `apps/_shared/sandbox/images/<principal>.Containerfile`,
  built and tagged by the identity issuer (0.2).
- Migrate `apps/author_agent` and `apps/homelab_maintainer_agent` worktree
  management onto the runner.
- Acceptance: existing author/maintainer jobs run unchanged through the new
  runner; egress to a host not in the agent's `network.allowed_hosts` is
  rejected at the container level (slirp4netns / netavark), not just by the
  agent's prompt.

## 0.2 Identity issuer — `apps/_shared/identity/`

One CLI: `python3 -m apps._shared.identity issue --principal agent:foo`.

For each agent the issuer provisions:

- Forgejo bot account + SSH keypair + scoped Forgejo PAT.
- Discord bot application token (guided flow — Discord requires a human in the
  Developer Portal; the CLI prompts the user, then stores the token in
  Infisical).
- Infisical service token scoped to the agent's `secrets_profile`.
- Per-agent Containerfile under `apps/_shared/sandbox/images/`.

Vaultwarden is not touched; humans only.

Acceptance: re-running `issue` is idempotent; `revoke` exists; identities show
up in [docs/ACCESS_MATRIX.md](../ACCESS_MATRIX.md) generation; tokens never
written to disk outside Infisical.

## 0.3 Hash-chained audit — `apps/_shared/audit/`

Wrap the existing `trust-ledger.jsonl` and `lifecycle-events.jsonl` writers
with a hash-chained appender (each line includes `prev_hash`, `entry_hash`).

- Daily anchor: write the day's tip hash into memory-engine and DM Kevin.
- `verify-ledger` CLI: walks every chain, exits non-zero on any break.
- Acceptance: an agent cannot rewrite its own history without breaking the
  chain.

## 0.4 Verifier-loop primitive — `apps/_shared/verifier/`

Generic two-process pattern: builder writes session JSONL; verifier reads,
calls only `verifier_prompt` back. Max 3 loops. Persona files in
`config/verifiers/<name>.md`.

First two consumers: `agent:homelab-maintainer` for any change touching
`compose/` or `inventory/`; `agent:finance` for any "advice" turn.

Acceptance: a deliberately wrong builder claim is caught; on the 4th attempt
the executive escalates to human via tier 3 (0.11).

## 0.5 Capability registry — `config/agents/` (DONE)

Single source of truth that every platform tool reads at startup:

- `config/agents/registry.yaml` — index of registered principals.
- `config/agents/agent-<name>.yaml` — per-agent manifest.
- `config/agents/registry.schema.yaml` — schema documentation.
- `apps/_shared/registry/` — Python loader + validator + CLI.

CLI:

```bash
python3 -m apps._shared.registry validate
python3 -m apps._shared.registry list
python3 -m apps._shared.registry show agent:executive
python3 -m apps._shared.registry show agent:executive --field discord
```

The registry references existing per-agent policy files
(`config/policies/<name>-policy.yaml`) and the memory principal entry in
`config/memory/principals.yaml` rather than duplicating their contents. New
platform fields (skills, tools, A2A allowlist, Discord, sandbox, escalation
overrides) live in the manifest itself.

Cross-file rules enforced by the loader (see
[config/agents/registry.schema.yaml](../../config/agents/registry.schema.yaml)):

- principal in manifest matches the registry index entry;
- `identity.git_user`, `forgejo_account`, `discord_bot_app_name`, and
  `secrets_profile` are unique across all manifests;
- `references.memory_principal` must contain an entry whose `id` equals the
  manifest's principal;
- `a2a.allowed_callees` may not include self and must reference principals
  that exist in the registry.

Acceptance: removing an agent from the registry stops the executive routing to
it without code changes; `validate` exits non-zero on any cross-file violation
and is wired into CI.

## 0.6 Gateway cost/latency log — `compose/model-gateway/`

LiteLLM callback that writes `{ts, principal, task_class, route, model,
tokens_in, tokens_out, latency_ms, est_cost_usd, cloud_alt_cost_usd}` to
memory-engine. Surface in the master dashboard (0.12). Weekly review shows the
local-vs-cloud spend trade.

## 0.7 Per-agent Discord presence — `apps/_shared/discord/`

Replace the single `alienware-executive-discord.service` with one bridge
process per agent. Each agent gets its own bot user, display name, and avatar;
bot tokens are stored in Infisical by 0.2.

- Single existing guild. All agents live there.
- Channel topology: `#homelab`, `#knowledge`, `#insights`, `#language`
  (domain), `#finance`, `#profile` (sensitive, role-gated, **read-only from
  Discord** — analyze/report/summarize OK, mutations require CLI or dashboard,
  enforced bridge-side by call origin), `#intake`, `#approvals`, `#ops`
  (cross-cutting), `#general` (humans-only, no bots).
- Threads = Planka cards. When a card enters `Plan Ready`, the executive
  auto-creates a thread under its domain channel. Auto-archive on `Done`.
- Bot Discord permissions: `Send Messages`, `Read Messages`, `Create Threads`,
  `Send Messages in Threads`, `Add Reactions`, `Embed Links` only. Never
  `Manage *`, `Mention Everyone`, `Kick`, `Ban`.
- Bot status: online but silent unless @mentioned, in their dedicated
  channel/thread, or in DMs. "Watching" status surfaces verifier loop progress.
- Audit alignment: every action triggered by Discord logs `discord_guild_id`,
  `discord_channel_id`, `discord_thread_id`, `discord_message_id`,
  `discord_author_id` to the trust ledger.
- Notification policy: DMs to Kevin reserved for human-approval requests,
  verifier escalations, audit-anchor mismatches, backup failures.
- Channel + thread membership generated from the registry, not hand-edited in
  Discord.

Acceptance: a new agent gets a working Discord identity solely from `identity
issue --principal agent:foo` (guided) plus a registry entry; sensitive bots
refuse mutating tool calls when invoked from Discord; one Planka card produces
exactly one Discord thread and one ledger correlation chain.

## 0.8 Per-agent skill registry — `config/skills/` + `apps/_shared/skills/`

Cursor-style: each skill is a `SKILL.md` with a description and instructions
under `config/skills/<skill-id>/SKILL.md`. Each agent manifest declares
`skills: [skill-id, ...]`. Default deny — no agent gets a skill that isn't
named.

- Runtime loads only the listed skills into the agent's prompt; other skills
  aren't visible.
- Tool grants enforced the same way: a tool not declared isn't bound.
- Skills can be marked `local_only: true` so they refuse to load if the
  current route is cloud.
- Acceptance: `agent:finance` cannot see homelab skills/tools and vice-versa,
  even if asked; the dashboard shows each agent's effective skill set.

## 0.9 Inter-agent communication (A2A) — `apps/_shared/a2a/`

Standard "ask another agent" tool exposed only to agents whose manifest lists
`a2a.allowed_callees`. Default empty.

- Implementation: durable enqueue into the callee's existing queue with
  `reply_to_queue` and `correlation_id`; caller awaits a single result envelope
  (timeout escalates per 0.11). All hops are written to the hash-chained audit
  so the chain `who asked whom for what` is always reconstructible.
- Standard role decomposition (researcher -> planner -> executor -> verifier)
  inside one project uses sub-agent spawning (0.10) when the steps share
  context, and A2A when they cross trust boundaries.

Acceptance: a Planka card created in `#knowledge` can produce a verified
homelab inventory excerpt without a human re-typing the request, and the audit
trail names every agent that touched it.

## 0.10 Sub-agent spawner — `apps/_shared/subagent/`

Primitive: `spawn_subagent(role, prompt, tools, route="local-fast",
return_schema=...)` returns only the structured result (or a short summary),
not the full transcript. The transcript is captured to the audit log under the
parent's correlation ID.

- Default route is `local-fast`. Escalating to `local-strong` or cloud is
  permitted only if the parent's routing policy allows.
- Standard sub-roles ship with personas: `researcher`, `planner`, `tool-runner`,
  `verifier`. The `tool-runner` is the default for any tool-heavy step (web
  fetch, grep, file walk) so the parent's context stays clean.
- Acceptance: a parent agent's context window stays under a fixed budget
  regardless of how many tool calls were made under the hood; replays from the
  audit can reconstruct any sub-agent's full transcript.

## 0.11 Tiered escalation — `apps/_shared/escalation/`

Failures escalate in three tiers before the human is bothered:

- **Tier 1 — agent self-recovery.** Agent retries within its own loop
  (verifier feedback included; max 3 iterations per spec). Per-task-class
  budget; default 5 minutes wall-clock per attempt.
- **Tier 2 — executive reroute.** Agent emits a `help_request` envelope into
  the executive's queue. Executive may re-route to a sibling, spawn a
  researcher, or split the task. Hard cap one reroute per `help_request`.
  Default budget 30 minutes.
- **Tier 3 — human.** Posts to `#approvals` with the original card link,
  Tier 1+2 transcript reference, proposed next options, and explicit "what I
  cannot do without you" blockers. DMs Kevin if marked `urgent` or if the
  `#approvals` ping is unacknowledged after 4 hours.

Per-task-class overrides live in `config/escalation.yaml`. Examples:
`homelab.deploy` Tier 1 = 2 min (incidents matter), `knowledge.synthesize`
Tier 1 = 30 min (no rush), `finance.advise` skips Tier 2 entirely.

Every tier transition writes a `tier_transition` event to the hash-chained
audit (0.3) with the reason and the agent that was asked next.

Acceptance: a deliberately broken task surfaces to `#approvals` within the
configured budget without bouncing between agents; an `urgent` task DMs Kevin
within seconds of Tier 3; `verify-ledger` reconstructs the full decision chain
across all three tiers.

## 0.12 Master dashboard + knowledge browser

**Dashboard** at `https://home.dev-path.org/` behind Authentik OIDC.

- **Stack:** FastAPI + Jinja2 + HTMX + Tailwind, server-rendered. Server-Sent
  Events (FastAPI native) drive live tiles. No JS toolchain.
- **Tiles:** system health (services from `inventory/services.yaml`, agent
  heartbeats, queue depths; embeds Grafana panels for metrics), all-agent
  activity (live tail of trust + lifecycle events, filterable by agent and
  tier), knowledge base overview (size, last update, top recent notes, dead
  links), cost/latency from 0.6, backup status from 0.13, open approvals from
  Tier 3 of 0.11.
- **Grafana** stays as the deep-dive surface for system metrics; embedded as
  iframes for the system-health tile and linked out for full drill-down.
- All `?token=...` query params on existing surfaces are removed.

**Knowledge browser** — Quartz v4 + Obsidian client over **one canonical
Obsidian vault** at `~/projects/memory-engine/obsidian_vault/`:

- `vault/raw/` — agent ingest output (write: agents only).
- `vault/compiled/` — wiki-compiler output (write: compiler only).
- `vault/notes/` — Kevin's hand-edited notes (write: Kevin only; agents
  read-only).
- `vault/published/` — explicit publish queue: only files here (or marked
  `publish: true` in frontmatter) appear in the public Quartz output.

Quartz v4 builds the vault into a static site at `https://kb.dev-path.org/`
behind Authentik (rebuild on a systemd path/timer; rsync to nginx). Obsidian
on Mac is the read/write workstation surface; **Syncthing** keeps the vault
in sync between Mac and the memory-engine LXC. Khoj chat sits as a sibling
tab on the same domain.

Acceptance: opening one URL gives a non-technical viewer a useful overview;
every "drill in" is one click; nothing on the homepage works without auth; a
note Kevin types in Obsidian on his Mac appears on `kb.dev-path.org` after the
next rebuild only if it's in `vault/published/` or marked `publish: true`;
nothing from `vault/notes/` ever leaks publicly.

## 0.13 Backup + restore — `scripts/backup/` + `docs/RESTORE.md`

- **Target:** local NAS via SFTP, single restic repository. Repo password in
  Vaultwarden break-glass + a printed paper copy.
- **Daily** systemd timers per source host:
  - Code/config: already in git. Mirror Forgejo to a private GitHub backup
    remote nightly.
  - Data: Postgres dumps (memory-engine), Qdrant snapshots, Vaultwarden
    export, Infisical export, per-agent queue + ledger trees
    (`~/.local/state/homelab-control/`), Planka DB + attachments, Khoj index,
    finance ledger (`~/finance/ledger/`), Beancount-imported source
    statements.
  - Hosts: Proxmox VZDump snapshots of LXCs nightly to NAS; weekly full.
- **Retention:** restic policy `keep-daily 14 keep-weekly 8 keep-monthly 12
  keep-yearly 3`. Weekly `restic check --read-data-subset=10%` on its own
  timer.
- **Restore runbook** ([docs/RESTORE.md](../RESTORE.md)) ordered: Proxmox up
  -> LXC restore -> Postgres -> Qdrant -> Khoj reindex -> Vaultwarden ->
  Infisical -> queues -> finance ledger -> `podman compose up -d` ->
  `verify-ledger` -> spot-check master dashboard -> rotate Discord bot tokens
  (break-glass implies tokens may have been seen) -> announce in `#ops`.
- **Quarterly DR drill** on a throwaway Proxmox node restores from NAS only;
  pass = master dashboard up, `verify-ledger` clean, finance ledger checksums
  match, all bots back online.

## 0.14 Doc + plan layout (this file)

- Vision: [docs/VISION.md](../VISION.md).
- Per-phase build plans: [docs/plans/](.).
- Source of truth from Phase 0 onward is the in-repo copy; the Cursor plan in
  `~/.cursor/plans/` is the working draft only.
