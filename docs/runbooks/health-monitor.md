# Health monitor

15-minute pollers on Alienware that checks the things most likely to
silently break: audit-chain integrity, systemd timer last-run results,
always-on service liveness, HTTP health endpoints from
`inventory/services.yaml`, and restic snapshot freshness. **Alerts only
on state transitions** (healthy↔unhealthy) — steady-state silence.

## Setup

Install on Alienware (one-time):

```bash
mkdir -p ~/.config/homelab-control
cat > ~/.config/homelab-control/health-monitor.env <<EOF
HEALTH_MONITOR_DISCORD_WEBHOOK=https://discord.com/api/webhooks/<…/#ops-alerts>
HEALTH_MONITOR_RESTIC_REPOS=/path/to/repo-hot,/path/to/repo-full,sftp:root@proxmox:/path
EOF
chmod 600 ~/.config/homelab-control/health-monitor.env

mkdir -p ~/.config/systemd/user
ln -sf ~/git/homelab-control/systemd/alienware-health-monitor.service \
       ~/.config/systemd/user/
ln -sf ~/git/homelab-control/systemd/alienware-health-monitor.timer \
       ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now alienware-health-monitor.timer
```

Verify:

```bash
systemctl --user list-timers alienware-health-monitor.timer
.venv/bin/python -m apps.health_monitor --show-results   # one-shot, all rows
.venv/bin/python -m apps.health_monitor --dry-run        # one-shot, summary only
```

## Adding a new check

Write a function in `apps/health_monitor/checks.py` that returns
`list[CheckResult]`, add it to `ALL_CHECKS`, and pick a stable
`name=` (e.g. `"audit:<service>"`, `"health:<service>:<endpoint>"`).
The transition engine handles state, alerts, and audit automatically.

## Symptoms → likely causes

| Symptom | Likely cause | First check |
|---|---|---|
| #ops-alerts pings every run with the same alert | a check is flapping (returns unhealthy then healthy alternately) | look at the run log: `journalctl --user -u alienware-health-monitor.service -n 100`; the check name in the alert is the failing one |
| "timer:X has never fired and is not scheduled" alert | unit not enabled OR config broken (`OnCalendar` invalid) | `systemctl --user list-timers \| grep <X>`; if missing, `systemctl --user enable --now <X>.timer` |
| "audit:X chain broken at seq N" alert | someone (or some bug) edited the JSONL ledger; OR a partial write happened | **DO NOT** modify the file; snapshot it first (`cp <file> <file>.snap`) then `python -m apps._shared.audit verify <file>` to find first break |
| "health:<svc>:* HTTP 4xx/5xx" alert | service URL changed (e.g., new path); OR service is actually down | `curl -fsS <url>`; if 404, fix `inventory/services.yaml`; if 5xx, look at that service's runbook |
| "restic:<repo> latest snapshot Nh ago (limit Mh)" alert | backup timer skipped; OR repo unreachable (SFTP) | see [backup.md](backup.md) §Symptoms |
| `service:X` alert when X is actually running | check ran while X was restarting (transient) | wait one cycle; if still alerted, `systemctl --user status X` |
| Nothing fires when something IS broken | the check for that thing doesn't exist yet | add a check (see "Adding a new check" above) |

## Investigation steps

1. `systemctl --user status alienware-health-monitor.timer` — timer firing?
2. `journalctl --user -u alienware-health-monitor.service -n 200` — what did the last few runs say?
3. `.venv/bin/python -m apps.health_monitor --show-results` — every check + status right now, no state writes
4. `cat ~/.local/state/homelab-control/health-monitor/state.json | jq .` — what does the state store think?
5. `tail ~/.local/state/homelab-control/health-monitor/audit.jsonl | jq .` — recent transitions on record
6. If `unknown_streak` is growing on a check, the source is timing out — investigate the underlying service

## Recovery

- **Flapping check**: tighten the check's threshold (e.g., raise `unknown_alert_after`); or short-circuit a known-transient failure mode in the check itself.
- **Stale state file (corrupt JSON)**: `mv ~/.local/state/homelab-control/health-monitor/state.json{,.bad} && systemctl --user start alienware-health-monitor.service`. First run after will emit "initial" alerts for anything currently unhealthy.
- **Wrong webhook**: edit `~/.config/homelab-control/health-monitor.env`, then `systemctl --user start alienware-health-monitor.service` (service re-reads env each start).

## Past incidents

### 2026-05-17 — initial deploy: false-positive "timer never fired" alerts

- **Symptom:** first health-monitor run alerted on 3 timers that had been enabled minutes prior
- **Root cause:** the check treated empty `LastTriggerUSec` as failure, even when `NextElapseUSecRealtime` was set and the unit was `active`
- **Fix:** added a "never fired but scheduled and active" path → healthy with detail showing the next run time
- **Followup:** test added (`test_systemd_timers.py` if/when we cover this); documented here

### 2026-05-17 — `khoj.dev-path.org/health` returned 404 on first run

- **Symptom:** health monitor flagged khoj as unhealthy
- **Root cause:** `inventory/services.yaml` had the wrong path; real khoj health endpoint is `/api/health`
- **Fix:** updated services.yaml; the inventory is now the source of truth (no hardcoded URLs in the checker)
- **Followup:** anytime a new endpoint goes into services.yaml, run `python -m apps.health_monitor --show-results` once to confirm it responds 2xx

## Configuration

| Var | Default | Purpose |
|---|---|---|
| `HEALTH_MONITOR_DISCORD_WEBHOOK` | unset | Discord webhook for #ops-alerts |
| `HEALTH_MONITOR_RESTIC_REPOS`    | unset (skip) | Comma-separated restic repos to check freshness on |
| `HEALTH_MONITOR_RESTIC_FRESH_HOURS` | `30` | Max acceptable age of latest snapshot |
| `HEALTH_MONITOR_AUDIT_DIR`       | `~/.local/state/homelab-control` | Tree of `audit.jsonl` files to verify |

## Future work

- **Remote audit + timer checks**: today checks only Alienware-local
  units and ledgers. SSH probes for Proxmox-side ledgers and timers
  would close the loop on the LXC backups.
- **Per-check rate limit**: if a check truly is flapping (e.g., a flaky
  service), we'd want to suppress repeated alerts. Today it alerts on
  every flip; future: a "max N alerts per check per day" cap.
- **Heartbeat liveness for the monitor itself**: if the monitor crashes,
  there is nothing watching the watcher. Easiest fix: have the
  dashboard's Phase 0.12 audit tile flag "no health_run row in 30 min".
