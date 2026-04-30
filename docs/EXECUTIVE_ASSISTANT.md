# Executive Assistant Agent

The executive assistant is the persistent coordinator for Kevin's local agent
system. It is intentionally a thin, Pi-compatible assistant layer that delegates
work into the existing Planka, author-agent, review-agent, and memory flows
instead of replacing them.

## Mission

The assistant helps Kevin turn intent into safe, traceable work:

- understand requests using relevant memory context
- create Planka cards with clear metadata, risk labels, and provenance
- move low-risk planning work to `Plan Ready` when policy allows
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

## First Interaction Surface

The first direct interface is a local CLI:

```bash
python3 apps/executive_agent/main.py handle-request \
  --request "Research better family calendar options" \
  --dry-run
```

The CLI produces the same structured decision that a future Pi plugin, local
HTTP endpoint, mobile shortcut, or chat bridge can call. This keeps the first
slice testable without coupling the core safety model to a specific chat UI.

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
