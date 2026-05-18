# Maintenance mode

A small CLI + lock file that lets you take systems down for planned work
**without paging yourself**. The lock is time-bound (max 168 h, default
no default — you pass `--hours`), reason-required, and optionally
scoped to a prefix-matched subset of checks. While active, the health
monitor still runs and writes audit/state, but **suppresses Discord
alerts** for in-scope check transitions.

## What it does

| Layer | During maintenance |
|---|---|
| `apps/health_monitor` | Still polls every 15min. Still writes state. Still appends audit rows (with `alert_suppressed: true` on in-scope transitions). **Skips the Discord post** for in-scope transitions. Out-of-scope alerts still fire. |
| Discord `#ops-alerts` | Silent for the planned scope; loud for anything unrelated that breaks during the window. |
| Audit ledger | `maintenance_start` and `maintenance_end` events recorded; every transition during the window has `alert_suppressed` flag. |
| Lock file | `~/.config/homelab-control/maintenance.lock` — JSON with `started_at`, `until`, `reason`, `started_by`, `scope`. Auto-expires at `until` (no manual cleanup needed). |

## CLI

```bash
# Start a 2h window covering all Proxmox-side and memory-engine checks
python -m apps.maintenance start \
    --hours 2 \
    --reason "proxmox kernel update" \
    --scope "health:memory-engine,timer:proxmox,health:forgejo,health:vaultwarden"

# Check what's active
python -m apps.maintenance status         # human-readable
python -m apps.maintenance status --json  # also prints raw lock

# End early (otherwise just wait — auto-expires at `until`)
python -m apps.maintenance end
```

`--scope` is a comma-separated list of **check-name prefixes**. Every
check the health monitor emits has a stable name like
`health:<service>:<endpoint>`, `timer:<unit>`, `service:<unit>`,
`audit:<ledger>`, or `restic:<repo>`. The scope matches by prefix, so
`health:memory-engine` covers `health:memory-engine:khoj`,
`health:memory-engine:n8n`, and `health:memory-engine:planka`.
**Empty scope = global** (everything suppressed).

If you forget to end the window, no harm done — the lock auto-expires
at `until` (capped at 168h / 1 week from start). Worst case you wasted
a few hours of alert coverage.

## Typical workflows

### Reboot Alienware

```bash
python -m apps.maintenance start --hours 1 \
    --reason "alienware reboot for kernel update" \
    --scope ""    # empty = global; the monitor and all services on Alienware go down with it
sudo systemctl reboot
# come back up, services auto-start (Restart=always)
python -m apps.maintenance end
```

After reboot, the monitor's first run sees everything healthy → no
"recovery" alerts because the window suppressed the original
healthy→unhealthy.

### Take Proxmox / a single LXC down

```bash
python -m apps.maintenance start --hours 1 \
    --reason "memory-engine n8n upgrade" \
    --scope "health:memory-engine"
# do the upgrade
python -m apps.maintenance end
```

Service alerts on Alienware (gateway, dashboard, cost relay) still fire
normally.

### Long-running migration (multiple days)

```bash
python -m apps.maintenance start --hours 72 \
    --reason "postgres major upgrade in stages" \
    --scope "health:memory-engine,timer:proxmox-backup"
```

The 168h cap means you literally cannot leave maintenance mode active
forever; pick a window and commit to checking on it.

## Symptoms → likely causes

| Symptom | Likely cause | First check |
|---|---|---|
| Discord still pinged during a planned outage | scope didn't cover the alerting check | `python -m apps.maintenance status` — what scope did you set? Confirm with the check name in the Discord post |
| Maintenance "ends itself" mid-window | the lock has a hard `until`; you set a shorter duration than you needed | `tail audit.jsonl | grep maintenance_end` — if no `maintenance_end` event, the lock auto-expired by reaching `until` |
| `python -m apps.maintenance start` errors "duration_hours must be in (0, 168]" | you asked for more than 1 week | break the work into multiple windows; or override `MAINTENANCE_MAX_HOURS` if you're really sure (don't) |
| Health monitor never goes quiet even though I started a window | the monitor is reading a different lock file than the CLI wrote | both default to `~/.config/homelab-control/maintenance.lock`; if you ran with `MAINTENANCE_LOCK_FILE` env set, that wins |
| I rebooted; window is still active and I want it gone | safe to delete the lock file by hand | `rm ~/.config/homelab-control/maintenance.lock` then `python -m apps.maintenance status` to confirm |

## Investigation steps

1. `python -m apps.maintenance status` — is anything active?
2. `cat ~/.config/homelab-control/maintenance.lock | jq .` — raw lock contents
3. `tail ~/.local/state/homelab-control/health-monitor/audit.jsonl | jq 'select(.event | startswith("maintenance"))'` — history of start/end events

## Recovery

The lock can always be removed by hand: `rm
~/.config/homelab-control/maintenance.lock`. The next health-monitor
run treats whatever it finds as the source of truth.

## Past incidents

_None yet._

## Configuration

| Var | Default | Purpose |
|---|---|---|
| `MAINTENANCE_LOCK_FILE` | `~/.config/homelab-control/maintenance.lock` | Where the lock lives. Override if you want per-host lock files. |

## Future work

- **Dashboard banner**: surface "MAINTENANCE: <reason> until HH:MM" prominently on the master dashboard so it's hard to miss when you walk past the monitor.
- **Agent-side awareness**: long-running agents (homelab-maintainer, executive) could read the lock at startup and decide to defer scheduled work. Today only the health monitor consults it.
- **Slack/email backup channel** if Discord itself goes down during a window (chicken-and-egg). Likely not worth solving until it actually bites.
