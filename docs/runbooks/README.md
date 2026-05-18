# Runbooks

This directory holds operational runbooks for everything in the homelab.
**It is the first place an agent (or a tired human) should look when
something breaks.**

Every runbook follows the standard structure in [`_template.md`](_template.md):

1. **What it is** — the happy path; one paragraph
2. **Setup** — install / deploy steps
3. **Symptoms → likely causes** — the table you grep first when paged
4. **Investigation steps** — concrete commands to confirm a hypothesis
5. **Recovery** — minimal-blast-radius fix for each cause
6. **Past incidents** — append-only history of what we hit + what worked
7. **Configuration / future work** — env vars, follow-ups

When you fix something, **append to the Past incidents section of the
relevant runbook** before you close the loop. Future-you (and the
on-call agent) will thank present-you.

## Router: which runbook?

### By service

| Service | Runbook | What it does |
|---|---|---|
| Restic backups (Alienware → NAS / Proxmox) | [backup.md](backup.md) | Daily restic snapshots + DR drill |
| Restic backups (Proxmox LXCs → Alienware) | [backup-lxcs.md](backup-lxcs.md) | LXC-side restic with two-copy redundancy |
| LiteLLM cost relay (n8n shipper) | [litellm-cost-relay.md](litellm-cost-relay.md) | Tails JSONL → POSTs to n8n → Postgres |
| Maintenance scan (weekly upgrades) | [maintenance-scan.md](maintenance-scan.md) | Probes containers, picks tags, runs verifier |
| Master dashboard | _todo_ | FastAPI + HTMX status board (Phase 0.12) |
| Discord agent bridges | [discord-bridge.md](discord-bridge.md) | Per-agent inbound Discord bots; manifest-driven channel allowlist + read/write modes |
| Health monitor | [health-monitor.md](health-monitor.md) | 15-min poller; alerts on state flips |
| Maintenance mode | [maintenance-mode.md](maintenance-mode.md) | Time-bound, scoped alert suppression for planned outages |
| Author-agent sandbox | [author-sandbox.md](author-sandbox.md) | Routes job `checks` through rootless podman under `AUTHOR_AGENT_SANDBOX_CHECKS=1` |
| CI (GitHub + Forgejo Actions) | [ci.md](ci.md) | On-push test runs + nightly canary |
| Agent identity issuance | [executive](../identity-runbook-agent-executive.md) / [homelab](../identity-runbook-agent-homelab.md) / [homelab-maintainer](../identity-runbook-agent-homelab-maintainer.md) / [review](../identity-runbook-agent-review.md). Regenerate via `python -m apps._shared.identity plan --principal <p> --output docs/identity-runbook-<p>.md` (add `--ignore-state` for a fresh-provisioning view) | Per-principal checklist for SSH keys, Forgejo accounts/PATs, Discord bots, Infisical tokens, sandbox images |

### By symptom

| Symptom | First check | Then read |
|---|---|---|
| "No Monday Discord report from the maintenance scan" | `systemctl --user status alienware-maintenance-scan.timer` on Alienware | [maintenance-scan.md](maintenance-scan.md) §Symptoms |
| "Backup didn't run last night" | `systemctl --user list-timers \| grep backup` | [backup.md](backup.md) §Symptoms |
| "DR drill restore failed" | `journalctl --user -u alienware-backup-dr-drill.service -n 200` | [backup.md](backup.md) §Past incidents (PIPESTATUS, XDG_CACHE_HOME) |
| "Dashboard returns 500 / blank tiles" | `journalctl --user -u alienware-master-dashboard.service -n 100` | dashboard runbook (todo); known cause: Jinja2/Starlette TemplateResponse signature mismatch |
| "Discord shows 'agent offline' but the service is running" | `systemctl --user is-active <agent>-discord-bot.service` | Discord bridges runbook (todo) |
| "Cost numbers stopped updating on the dashboard" | `journalctl --user -u alienware-litellm-cost-relay.service -n 50` | [litellm-cost-relay.md](litellm-cost-relay.md) §Symptoms |
| "Health monitor pinged #ops-alerts" | open the alert; it cites the failing check by name | [health-monitor.md](health-monitor.md) §Symptoms |
| "CI is red on phase-0-platform" | open the Actions tab on github.com or forgejo.dev-path.org | [ci.md](ci.md) §Symptoms |
| "About to take something down for planned work — don't want to page myself" | `python -m apps.maintenance start --hours N --reason "..." --scope "..."` | [maintenance-mode.md](maintenance-mode.md) |
| "An audit chain verify failed" | `python -m apps._shared.audit verify <file>` and note the first broken seq | docs/AUDIT_RECOVERY.md (todo) — DO NOT touch the file before snapshotting it |

### By host

| Host | What lives here | First-touch shell |
|---|---|---|
| `alienware-r10-primary` | model gateway, vLLM, every agent's local code, dashboard, backups origin, maintenance scan timer, health monitor | `ssh kenns@192.168.1.45` |
| `beelink-s12` (Proxmox `proxmox.dev-path.org`) | All LXCs below | `ssh root@proxmox.dev-path.org` |
| LXC 200 `memory-engine` | n8n, Khoj, Planka, Postgres+pgvector, Qdrant, mem0 | `pct exec 200 -- bash` |
| LXC 201 `forgejo` | Forgejo + db + redis | `pct exec 201 -- bash` |
| LXC 202 `vaultwarden` | Vaultwarden | `pct exec 202 -- bash` |
| LXC 203 `infisical` | Infisical + db + redis | `pct exec 203 -- bash` |
| LXC 204 `homelab-operator` | homelab operator (Proxmox-side daemon) | `pct exec 204 -- bash` |

## Incident log

For incidents that span multiple runbooks or that we want to capture
chronologically (post-mortems), see [`incident-log.md`](incident-log.md).
Per-runbook incidents go in the runbook itself.

## Adding a new runbook

1. Copy `_template.md` to `<service>.md`.
2. Fill in every section. If a section is genuinely empty, write
   "_none yet_" — never delete the heading.
3. Add a row to both the "By service" and "By symptom" tables above.
4. If the new service has metrics that should alert, also add a check
   to `apps/health_monitor/checks.py` and reference it in the runbook.
