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

| Item                               | Section | Status              |
| ---------------------------------- | ------- | ------------------- |
| Capability registry                | 0.5     | done                |
| Sandbox runner                     | 0.1     | done (egress: gap)  |
| Identity issuer                    | 0.2     | done                |
| Hash-chained audit                 | 0.3     | done                |
| Verifier-loop primitive            | 0.4     | not started         |
| Gateway cost/latency log           | 0.6     | done (live to PG)   |
| Per-agent Discord presence         | 0.7     | done                |
| Per-agent skill registry           | 0.8     | done                |
| Inter-agent communication (A2A)    | 0.9     | not started         |
| Sub-agent spawner                  | 0.10    | not started         |
| Tiered escalation                  | 0.11    | not started         |
| Master dashboard + KB browser      | 0.12    | not started         |
| Backup + restore                   | 0.13    | done (local + Proxmox SFTP) |

## Known gaps / deferred work

These items were uncovered during build-out and explicitly deferred. Each must
be addressed before any agent that depends on the gap goes live.

### Gap 0.1-egress: per-host egress enforcement is not implemented

The sandbox runner records the manifest's `sandbox.network.allowed_hosts` in
the audit log but does not enforce it. With `allowed_hosts` non-empty the
container runs under `--network=slirp4netns` with **wide-open egress** — any
TCP/UDP destination is reachable. With an empty list it correctly runs under
`--network=none`.

**Why deferred (2026-05-16):** the first concrete agent runs (homelab-maintainer
CVE triage, executive intake classification on trusted Planka cards) read
from operator-curated inputs and reach only first-party hosts. The risk of
exfiltration via this gap is bounded by the threat model: the homelab LAN
itself, not random untrusted internet input.

**Hard gate before lifting the deferral:** no agent that processes content
from an untrusted source (URLs from intake, web fetches, third-party RSS,
attached files from unknown senders) may run in production until this is
fixed.

**Planned implementation (Phase 0.1.1):**

- Run a small `httpx`-based forward proxy on the runner host. The proxy reads
  each agent's `allowed_hosts` from the registry and 403s anything else.
- Sandboxes get `HTTP_PROXY`/`HTTPS_PROXY` env vars pointed at the proxy.
- Container `--network=slirp4netns:port_handler=slirp4netns` plus an explicit
  reject-all default — the proxy is the only reachable address.
- Acceptance: a sandboxed `curl https://1.1.1.1` returns 403 when 1.1.1.1
  is not in the manifest; a sandboxed `curl https://forgejo.dev-path.org`
  succeeds when it is.

**Tracked card:** to be filed on Planka when the homelab-maintainer agent
goes live and can own its own backlog.

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

## 0.3 Hash-chained audit — `apps/_shared/audit/` (DONE)

`AuditLog` wraps existing JSONL ledgers with SHA-256 hash-chaining. Each
appended record adds `audit_seq`, `audit_ts`, `audit_prev`, and
`audit_hash` (= `sha256(canonical_json(payload) + audit_prev)`). The first
chained record's `audit_prev` is `GENESIS_HASH = "0" * 64`.

Implementation highlights:

- **Concurrency-safe append** via `fcntl.flock` on the ledger file; multiple
  writers in the same process or across processes serialize cleanly.
- **Legacy-prefix tolerant**: pre-existing un-chained lines are preserved
  as a "legacy prefix"; chaining starts on top. Useful for adopting the
  scheme on running systems without rewriting history (which would itself
  break trust assumptions).
- **External anchoring**: `AuditLog.anchor(receipt_path, note=...)` writes
  `{ts, head_hash, chained_lines, legacy_prefix_lines, note}` to a separate
  receipt file. Receipts can be backed up / printed / pinned to a Forgejo
  branch / DM'd to operator — verifier later compares the recomputed head
  to the anchored value.

CLI:

```bash
python3 -m apps._shared.audit info   <ledger>
python3 -m apps._shared.audit verify <ledger>
python3 -m apps._shared.audit tail   <ledger> [--lines N]
python3 -m apps._shared.audit anchor <ledger> --to <receipt> [--note ...]
```

Integration: `apps/executive_agent/main.py:append_jsonl` and
`apps/homelab_maintainer_agent/main.py:append_jsonl` were refactored to
delegate to `AuditLog`. All other agent code paths that write to those
files automatically inherit chaining.

Verified live (2026-05-17): a Discord message in `#intake` →
executive-agent handler → `AuditLog.append` → chained record written →
`verify_chain` returns `chain_ok: True`. First operator anchor at
`~/.local/state/homelab-control/audit-anchors/agent-executive.jsonl`.

Acceptance met: any byte modification to a chained record (including
re-ordering fields) breaks subsequent records' hash recomputation and is
reported by `verify`. Test coverage includes tamper, legacy-prefix,
concurrent-append, and round-trip-after-anchor scenarios (13 tests).

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

## 0.6 Gateway cost/latency log — `apps/_shared/litellm_callbacks/` + `apps/litellm_cost_relay/` (DONE — live to memory-engine PG)

Two halves:

1. **Capture (`apps/_shared/litellm_callbacks/custom_callbacks.py`)** — a
   LiteLLM `CustomLogger` mounted into the `homelab-model-gateway`
   container. Appends one JSON line per call to
   `~/.local/state/homelab-control/llm-calls/llm-calls.jsonl` with:
   `{schema, ts, status, model, agent_principal, request_id,
   prompt_tokens, completion_tokens, total_tokens, cost_usd, latency_ms,
   user, error?}`. Writes are best-effort and **never raise into the
   request path** — a logging failure cannot break an LLM call.
   `agent_principal` is sourced from the `x-agent-principal` header
   forwarded by `apps/_shared/rlm/subcall.py` (driven by per-agent
   `AGENT_PRINCIPAL` env, already set on the maintainer/homelab/review/
   executive bridges).

2. **Ship (`apps/litellm_cost_relay/main.py`)** — long-running daemon
   that tails the JSONL with durable byte-offset persistence and POSTs
   batches to `$LLM_COST_RELAY_URL` (typically an n8n webhook on the
   memory-engine LXC). Dry-run when the URL is unset. Exponential
   backoff (1s → 60s) on failures; offset only advances after a
   successful POST so memory-engine outages cause replay, never loss.
   See `docs/runbooks/litellm-cost-relay.md` for the PG DDL and n8n
   workflow spec.

Bonus fixes shipped with this phase:

- The `homelab-model-gateway.service` unit was crash-looping ~7000
  times because of a wrong mount path. Unit committed to
  `systemd/alienware-model-gateway.service` with the path fix plus
  the new callback + JSONL mounts.
- Added a `homelab-strong-long` route so the gateway has at least one
  live upstream. The other configured routes (`homelab-fast`,
  `homelab-fast-vllm`, `homelab-strong`, `global-embed`) point at
  upstreams that are currently inactive — see follow-up below.

Acceptance: a real call through the gateway produced
`{"status":"success","model":"homelab-strong-long-vllm","agent_principal":
"agent:executive","prompt_tokens":14,"completion_tokens":2,"latency_ms":673,
...}` in the JSONL; the relay (in dry-run) caught up to byte-equality
with the file on its first iteration. 18 unit tests covering the
callback's record builder and the relay's offset/batch/backoff
semantics, all green.

End-to-end pipeline live as of 2026-05-17:

```
agent → homelab-model-gateway (LiteLLM)
   → apps/_shared/litellm_callbacks (CustomLogger)
   → JSONL on Alienware (/var/log/llm-calls/llm-calls.jsonl)
   → apps/litellm_cost_relay (every 30s, HTTPS POST batch)
   → n8n webhook (https://n8n.dev-path.org/webhook/llm-calls)
   → ON CONFLICT DO NOTHING insert into memory-engine PG llm_calls
```

n8n workflow definition committed to
[`compose/n8n-workflows/homelab-llm-cost-ingest.json`](../../compose/n8n-workflows/homelab-llm-cost-ingest.json);
re-importable via the n8n CLI pattern in the runbook.

Smoke verified: 6 records in `llm_calls` after first non-dry-run
iteration (3 gateway smoke calls + 1 direct webhook test + 2
historical agent:executive calls); relay log:
`shipped 5 records (offset 0 -> 1466)`.

Model fleet was trimmed (2026-05-17): `homelab-fast` and
`homelab-strong` are now aliases for `homelab-strong-long-vllm`
(Qwen3-Coder-30B AWQ, TP=2, 131k ctx) — the only live backend. When
real fast/embed backends come back online, repoint the corresponding
`model_list` entry; no agent changes needed.

Follow-ups (not blocking 0.9):

- **0.12 surface**: master dashboard reads the PG `llm_calls` table
  for per-agent / per-day spend and latency curves; weekly review
  pulls the local-vs-cloud spend trade.
- ~~**Tagging**: forward `x-task-intent`…~~ **(DONE 2026-05-17)**
  `apps/_shared/rlm/subcall.py` now sends `x-task-intent` alongside
  `x-agent-principal`; the callback writes `task_intent` into the
  JSONL record; n8n inserts it into the new
  `llm_calls.task_intent text` column (indexed for
  `task_intent IS NOT NULL`). End-to-end verified: a smoke call with
  `x-task-intent: classify` lands in PG with the column populated.

## 0.7 Per-agent Discord presence — `apps/_shared/discord_bridge/` (DONE)

`apps/_shared/discord_bridge/` extracts the connect/intent/filter/audit/
chunk machinery that the executive bridge has been carrying since cut. Per-
agent bridges are now thin wrappers that supply a `BridgeConfig` (principal,
label, prefix, audit ledger path) plus an async `handler(MessageContext) ->
reply | None`.

Initial fleet (Phase 0.7 ship):

| Agent | Bridge module | Prefix | Channels (env allowlist) |
| --- | --- | --- | --- |
| `agent:executive` | `apps/executive_agent/discord_bot.py` | `!assistant` | `#intake`, `#approvals`, `#ops`, `#homelab`, `#executive-assistant` |
| `agent:homelab-maintainer` | `apps/homelab_maintainer_agent/discord_bot.py` | `!maintainer` | `#ops`, `#homelab` |
| `agent:homelab` | `apps/author_agent/discord_bot.py` | `!homelab` | `#homelab` |
| `agent:review` | `apps/review_agent/discord_bot.py` | `!review` | `#approvals`, `#homelab` |

Each bridge:

- Connects with the intents we proved necessary in 0.3:
  `message_content` + `members` + `dm_messages` (privileged toggles must be
  enabled per-app in the Discord developer portal).
- Filters by `DISCORD_ALLOWED_USER_IDS` and `DISCORD_ALLOWED_CHANNEL_IDS`
  (env-only — IDs intentionally out of git).
- Writes a hash-chained `discord-message` event to the agent's own trust
  ledger for every inbound, outbound, and handler exception. Per-agent
  ledgers are independent: a compromise of one agent's storage cannot forge
  another's history.
- Chunks replies at 1800 chars.

Initial handlers expose only `help` and `status` plus an "ack-and-audit"
fallback for free-form text. They deliberately do **not** act on free-form
input until A2A lands in 0.9 — this keeps the audit boundary explicit:
anything that reaches an agent over Discord is logged before any work
decision.

systemd units per bridge under `systemd/alienware-agent-<name>-discord.service`
read `~/.config/homelab-control/agent-<name>{,-discord}.env` and run on the
same machine as the rest of the agent fleet.

Pending finer cuts (deferred to follow-up tickets, not blocking 0.9):

- Planka-card → thread automation (thread creation on `Plan Ready`).
- Sensitive-channel mutation guard (read-only mode for `agent:finance` and
  `agent:profile` once those agents land).
- Bot avatars + presence text ("Watching: verifier-loop step 2/3").
- Channel + thread membership generated from the registry (today bot
  membership is operator-managed via Discord UI; the registry already
  declares the intent).

Acceptance met: four bots online; each responds in its allowlisted
channels and in DMs from Kevin; each produces a fully verified hash chain
in its own per-agent ledger; ledgers anchored.

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

## 0.12 Master dashboard + knowledge browser (MVP DONE — auth + KB browser deferred)

**MVP shipped 2026-05-17** — `apps/master_dashboard/` is a FastAPI +
Jinja2 + HTMX + Tailwind app, running at `http://192.168.1.45:8800/`
on Alienware via `systemd/alienware-master-dashboard.service` (LAN-only;
no public expose yet). Four tiles, all rendering live data:

- **LLM cost & latency · 24h** — pulls from the new n8n workflow
  `compose/n8n-workflows/homelab-llm-cost-summary.json` which aggregates
  the `llm_calls` table (calls, total tokens, p95 latency; per
  `agent_principal × task_intent` table sorted by call count).
- **Agent & service presence** — `systemctl --user show` across 14
  services + 3 timers (gateway, vLLM, cost relay, 4 agent processes,
  4 Discord bridges, glue, backup timers); green/yellow/red dot per
  unit.
- **Backup status** — `restic snapshots --json` across the 3 repos
  Alienware can reach directly (local hot+full + sftp to Proxmox +
  inbound LXC mirror); shows the most recent snapshot per
  `(host, tags)` combo.
- **Live audit tail** — tails every `agent-*/trust-ledger.jsonl`,
  initial render shows last 100 events sorted newest-first, color
  per agent_principal; SSE endpoint `/sse/audit` streams new events
  within ~1s of write.

Architecture notes:

- **TTLCache primitive** (5 min cost + backup, 30 s presence) is
  single-flight: concurrent requests coalesce, failures preserve the
  prior good value, every tile degrades independently — a broken n8n
  endpoint shows an error band on the cost tile and the other three
  keep working.
- **No JS toolchain** — HTMX + Tailwind via CDN, no npm/webpack.
- **No direct PG conn from Alienware** — LXC isolation kept; the
  dashboard pulls aggregated cost data through n8n. Means we can
  re-host the dashboard later without touching firewall.
- Tests: 5 pass (`apps/master_dashboard/test_main.py`) — tail reader,
  cross-ledger sort, single-flight + graceful degradation, full page
  render + every tile endpoint via FastAPI TestClient.

**Deferred (becomes its own ticket each):**

- Authentik OIDC forward-auth + `home.dev-path.org` public hostname.
  Today the dashboard is LAN-only.
- Grafana panel iframes for the system-health tile.
- Quartz v4 KB site at `kb.dev-path.org` + the vault layout
  (`raw/compiled/notes/published`) + Syncthing two-way to Mac.
- Khoj chat sibling tab.
- Queue-depth tile (no queue substrate yet — lands with 0.9 A2A bus).
- Open-approvals tile (lands with 0.11 tiered escalation).
- Mobile/narrow layout pass.

**Original full vision (still the target):** dashboard at
`https://home.dev-path.org/` behind Authentik OIDC.

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

## 0.13 Backup + restore — `apps/backup/` + `docs/runbooks/backup.md` (DONE — local target shipped)

Tiered restic runner (`apps/backup/runner.py`) driven by
`config/backup/sources.yaml`:

- **hot** (every hour, `alienware-backup-hot.timer`):
  `~/.local/state/homelab-control/` — per-agent hash-chained ledgers,
  audit anchors, llm-calls JSONL, relay offsets. The irreplaceable
  append-only state. First snapshot: 1205 files, 2.97 MiB raw / 567 KiB
  stored (3.26x compression).
- **full** (daily 03:30, `alienware-backup-full.timer`): hot +
  `~/.config/homelab-control/` (all bot tokens, env files) +
  `~/.config/systemd/user/` + `~/git/{homelab-control,memory-engine}/`
  minus venvs/caches/git internals. First snapshot: 2003 files,
  5.17 MiB raw / 1.46 MiB stored (2.88x compression).

Retention via `restic forget --prune --tag <tier>`:
`--keep-hourly 48 --keep-daily 30 --keep-weekly 8 --keep-monthly 12`
(hourly only applies to hot; full uses daily as its finest grain).

Multi-target via `BACKUP_REPOSITORIES` (comma-separated). Two
targets live today:

- `/mnt/spinny/restic-homelab` — local on Alienware's spinning disk
  (870 GB free). Protects against operator error and SSD failure.
- `sftp:root@proxmox.dev-path.org:/var/lib/vz/dump/restic-homelab-alienware`
  — off-host on Proxmox via restic's SFTP backend (root volume,
  81 GB free at setup). Protects against full Alienware loss.

Both targets receive every snapshot. The runner iterates serially
and continues on individual target failures (per-target success is
logged); service exit code is non-zero if any target failed, so
the journal surfaces red.

Verified live: both tiers ran clean on first invocation in both
local and off-host repos, `restic check` reports `no errors were
found`, both systemd timers armed. Runbook
(`docs/runbooks/backup.md`) covers install, init, restore
(`restic restore latest --tag <tier> --target …`), and adding more
targets to `BACKUP_REPOSITORIES`.

14 unit tests cover config parsing, `$HOME`-only path expansion (the
runner rejects other env tokens to avoid surprising leakage),
existing-vs-missing source separation (a missing optional source is a
logged skip, not a failure), backup/forget argv construction, and the
`run_plan` invocation contract (env-only secret passing,
backup-then-forget ordering, skip-forget-on-backup-failure, no-paths
error).

Follow-ups (not blocking 0.9):

- ~~**LXC-side coverage**~~ **(DONE 2026-05-17)** — daily 04:00
  restic snapshots of every managed LXC's logical state via
  `scripts/proxmox-backup/backup-lxcs.sh` + `systemd/proxmox-backup-lxcs.{service,timer}`,
  documented in [`docs/runbooks/backup-lxcs.md`](../runbooks/backup-lxcs.md).
  Covers `memory-engine` (PG dumpall + qdrant/mem0/planka/n8n/khoj
  volumes + `.env`), `forgejo` (PG + data volume), `vaultwarden`
  (data volume with sqlite), `infisical` (PG). Each LXC tagged
  `--host pve-lxc-<id>` for per-service retention scoping. Smoke-run
  produced 4 snapshots per repo, `restic check` clean on both,
  4.4× compression.
- ~~**Two-copy redundancy for LXC backups**~~ **(DONE 2026-05-17)** —
  authorized Proxmox `root` → Alienware `kenns` via SSH so Proxmox
  can sftp-mirror to Alienware's spinny disk (the reverse of the
  Alienware → Proxmox direction we set up earlier). LXC backups now
  land in both `/var/lib/vz/dump/restic-lxcs` (Proxmox local) and
  `sftp:kenns@192.168.1.45:/mnt/spinny/restic-lxcs-from-proxmox`
  (Alienware off-host). Each backup machine owns one primary repo on
  its own disk and mirrors to the other machine's spinny disk —
  losing either disk leaves a complete copy on the other.
- ~~**Quarterly DR drill**~~ **(DONE 2026-05-17)** —
  `scripts/backup/dr-drill.sh` restores the latest snapshot from
  every repo in `$BACKUP_REPOSITORIES`, runs `python3 -m
  apps._shared.audit verify` on every audit ledger in the restored
  tree, and exits non-zero if anything fails. Wired up as
  `systemd/alienware-backup-dr-drill.{service,timer}` (first Sunday
  of Jan/Apr/Jul/Oct at 03:00 ±30 min). Same script also runs on
  Proxmox against the LXC repo as `/usr/local/bin/proxmox-dr-drill`.
  First live run: 2028 files restored from both Alienware repos with
  11 audit ledgers clean each, all 4 LXC backups restored from the
  Proxmox repo.
- **Repo password durability**: restic password stored at
  `~/.config/homelab-control/restic-password` (chmod 600) on
  Alienware and `/etc/homelab-control/restic-password` on Proxmox,
  plus (per operator) in Vaultwarden + paper. Same passphrase secures
  all three repos. Losing it = losing every snapshot, irrecoverably.

## 0.14 Doc + plan layout (this file)

- Vision: [docs/VISION.md](../VISION.md).
- Per-phase build plans: [docs/plans/](.).
- Source of truth from Phase 0 onward is the in-repo copy; the Cursor plan in
  `~/.cursor/plans/` is the working draft only.
