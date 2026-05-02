# Executive Assistant Agent

The executive assistant is the persistent coordinator for Kevin's local agent
system. It is intentionally a thin, Pi-compatible assistant layer that delegates
work into the existing Planka, author-agent, review-agent, and memory flows
instead of replacing them.

## Mission

The assistant helps Kevin turn intent into safe, traceable work:

- understand requests using relevant memory context
- intake raw discoveries such as URLs, notes, screenshots, or voice transcripts
- create Planka cards with clear metadata, risk labels, and provenance
- move low-risk planning work to `Plan Ready` when policy allows
- route matched work into project-scoped agents such as `agent:homelab-maintainer`
- summarize current agent activity, trust levels, and weekly trends
- escalate anything sensitive, ambiguous, novel, or outside policy

The assistant is an executive coordinator, not a universal superuser.

## Runtime Shape

The first implementation should stay small enough for the current local model
routes:

- `homelab-fast` for classification, status, and short summaries
- `homelab-strong` for harder planning and policy synthesis
- compact memory packets instead of broad long-term memory injection
- explicit tools/skills instead of large always-on prompt sections

Pi is the preferred harness direction because it supports a minimal prompt,
custom tools, lifecycle hooks, compaction, dynamic context injection, and
OpenAI-compatible local providers. A native Python service may still own the
durable homelab integrations while a Pi adapter supplies the agent loop.

## Authority Boundaries

The assistant acts as `agent:executive`, never as `human:kevin`.

It may:

- create Planka cards in approved domains
- add labels and structured descriptions
- move low-risk work to `Plan Ready`
- classify raw intake into existing projects, new-project candidates, or scratch
- create draft project proposals from promoted intake artifacts
- write assistant decisions and summaries to memory with provenance
- record trust, Shield, and lifecycle events for audit

It must not:

- use Kevin's human credentials for autonomous actions
- approve execution for sensitive work
- bypass author/review agents for repo changes
- read or write outside its memory/tool grants
- persist untrusted external content as durable memory without review

## Shield Requirements

Every assistant action runs behind a Shield-style gate:

- inbound prompts and retrieved content are scanned for obvious prompt-injection
  and jailbreak language
- retrieved/web content is treated as data, not instructions
- outbound memory writes, Planka descriptions, and responses are scanned for
  likely secrets
- each tool call is checked against `config/policies/executive-assistant-policy.yaml`
- blocked or escalated actions are written to the trust ledger

This is deliberately deterministic and conservative in the first slice.

## Interaction Surfaces

The lowest-level interface is a local CLI:

```bash
python3 apps/executive_agent/main.py handle-request \
  --request "Research better family calendar options" \
  --dry-run
```

Discovery intake uses the same service:

```bash
python3 apps/executive_agent/main.py intake-raw \
  --source-kind url \
  --content "https://example.com/interesting-homelab-idea" \
  --hint "backup idea" \
  --dry-run
```

Promote a stored intake artifact into a draft project stub:

```bash
python3 apps/executive_agent/main.py promote-project \
  --intake-id intake-20260501-backup-idea \
  --project-slug backup-lab \
  --title "Backup Lab" \
  --dry-run
```

The CLI produces the same structured decision that the local chat UI, Discord
bridge, future Pi plugin, mobile shortcut, or other channel adapter can call.

### Local Web Chat

The local-network chat UI runs as `alienware-executive-chat.service` and stores
named conversations in:

`~/.local/state/homelab-control/agent-executive/conversations.sqlite3`

Open it from the home network at:

`http://192.168.1.45:8767/?token=<executive-chat-token>`

Get the token on Alienware:

```bash
grep EXECUTIVE_CHAT_TOKEN ~/.config/homelab-control/agent-executive-chat.env
```

Each conversation has domain, task type, memory, and Plan Ready defaults. Every
turn is still evaluated through Shield and trust policy before action tools run.
Discovery intake and project routing are currently CLI/queue-first features; the
chat layer remains compatible with them through the same backend.

### Discord Bridge

The Discord bridge runs as `alienware-executive-discord.service` after a bot
token and allowed user/server/channel IDs are configured. Direct messages map to
private conversations. Server channels map to conversations by guild and channel
ID. Discord never gets stronger authority than `agent:executive`.

Configure it on Alienware:

```bash
python3 -m pip install --user -r apps/executive_agent/requirements.txt
${EDITOR:-vi} ~/.config/homelab-control/agent-executive-discord.env
systemctl --user enable --now alienware-executive-discord.service
```

Required configuration:

- `DISCORD_BOT_TOKEN`: Discord bot token
- `DISCORD_ALLOWED_USER_IDS`: comma-separated Discord user IDs allowed to talk to the bot
- `DISCORD_ALLOWED_GUILD_IDS`: optional comma-separated server IDs
- `DISCORD_ALLOWED_CHANNEL_IDS`: optional comma-separated channel IDs

The policy file must also enable `discord-dm` or `discord-channel` before the
bridge will allow those sources.

### Agent Dashboard

Open the dashboard at:

`https://agents.dev-path.org/?token=<agent-activity-token>`

It shows executive assistant weekly review output, recent trust decisions, and
recent interaction sources once the latest dashboard service has been deployed.

## Memory Behavior

The assistant should use `memory-engine` through existing ingest/session
surfaces. It writes records with:

- `principal=agent:executive`
- `source=executive-assistant`
- `command_or_api=executive_agent:<command>`
- `artifact_url` pointing at the Planka card or dashboard when available

Long-term claims should be recorded as proposals or summaries with provenance,
not as hidden prompt state.

## Weekly Operating Review

The assistant should generate a short weekly review from dashboard data, not a
large free-form transcript. The review should highlight:

- completed and delegated work
- trust-level changes and blocked actions
- Shield events and risky inputs
- failed/stale queues or review backlog
- memory proposals accepted, rejected, or superseded
- trends worth Kevin's attention

The dashboard remains the detail surface; the assistant brings up the trend.
