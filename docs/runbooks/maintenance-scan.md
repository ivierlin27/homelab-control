# Weekly homelab maintenance scan

A weekly job (Mondays 09:00 on Alienware) that probes every running
container across all known hosts, asks each container's registry whether
a newer same-major tag exists, and publishes a verified report.

## What it does

1. **Probe.** SSH into Proxmox and shell into each LXC via `pct exec`;
   plus the local podman runtime on Alienware. Records image, tag,
   container, host for every running container (no dependency on
   `inventory/services.yaml` — catches images we forgot to declare).
2. **Registry check.** For every unique `image:tag` pair, query Docker
   Hub (`hub.docker.com/v2`) or the OCI Distribution Spec endpoint
   (ghcr.io, code.forgejo.org) and pick the highest numeric tag in the
   same major. Tags like `latest`, `main`, `16-alpine`, `pg16` are
   classified as **floating** — the operator gets a "re-pull to refresh"
   hint but no version bump suggestion.
3. **Verifier loop** ([Phase 0.4](../plans/phase-0-platform.md)). For
   every actionable upgrade, a separate verifier persona
   (`verifier:registry-recheck`) re-queries the registry and either
   confirms, asks for revision, or rejects the recommendation. Up to 2
   rounds before escalation; every round is appended to the
   hash-chained audit ledger.
4. **Report.** Markdown report committed to
   `docs/maintenance-reports/YYYY-MM-DD.md`, severity-sorted with the
   verifier verdict on each row.
5. **Notify.** If `MAINTENANCE_SCAN_DISCORD_WEBHOOK` is set, a tight
   summary is posted to `#homelab` (lists each verified upgrade with
   its host).
6. **Audit.** Every verifier round + the scan summary appended to
   `~/.local/state/homelab-control/agent-homelab-maintainer/audit.jsonl`
   (already covered by the existing Phase 0.13 backup tier).

## Severity legend

| Symbol | Meaning |
|---|---|
| 🔴 `major-upgrade` | A new major version is available (review breaking-change notes before bumping). |
| 🟠 `upgrade`       | A newer tag in the same major is available; should be safe to pin. |
| 🟡 `floating`      | Tag is a moving alias (`latest`, `main`, `16-alpine`); re-pull to pick up upstream fixes. |
| ⚪ `error` / `unmanaged` | Registry could not be queried or the image is locally built. |
| 🟢 `ok`            | Current tag is the newest in its major. |

## Setup

Install on Alienware (one-time):

```bash
mkdir -p ~/.config/homelab-control
# Optional but recommended:
echo 'MAINTENANCE_SCAN_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...' \
  > ~/.config/homelab-control/maintenance-scan.env
chmod 600 ~/.config/homelab-control/maintenance-scan.env

# Link the units
mkdir -p ~/.config/systemd/user
ln -sf ~/git/homelab-control/systemd/alienware-maintenance-scan.service \
       ~/.config/systemd/user/
ln -sf ~/git/homelab-control/systemd/alienware-maintenance-scan.timer \
       ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now alienware-maintenance-scan.timer
```

Verify:

```bash
systemctl --user list-timers alienware-maintenance-scan.timer
systemctl --user start alienware-maintenance-scan.service    # on-demand run
journalctl --user -u alienware-maintenance-scan.service -n 50
```

## Manual / on-demand run

```bash
cd ~/git/homelab-control
.venv/bin/python -m apps.maintenance_scan                 # writes report + audits
.venv/bin/python -m apps.maintenance_scan --dry-run -v    # no writes, full log
```

## Reading the audit chain

```bash
.venv/bin/python -m apps._shared.audit verify \
  ~/.local/state/homelab-control/agent-homelab-maintainer/audit.jsonl
tail ~/.local/state/homelab-control/agent-homelab-maintainer/audit.jsonl | jq .
```

## Configuration

All optional, set via env or `maintenance-scan.env`:

| Var | Default | Purpose |
|---|---|---|
| `MAINTENANCE_SCAN_PROXMOX_SSH` | `root@proxmox.dev-path.org` | SSH target used to reach LXCs. |
| `MAINTENANCE_SCAN_LXC_IDS`     | `200,201,202,203,204`       | LXC IDs to probe via `pct exec`. |
| `MAINTENANCE_SCAN_REPORT_DIR`  | `docs/maintenance-reports`  | Where weekly markdown lands. |
| `MAINTENANCE_SCAN_AUDIT_LOG`   | `~/.local/state/homelab-control/agent-homelab-maintainer/audit.jsonl` | Hash-chained audit. |
| `MAINTENANCE_SCAN_DISCORD_WEBHOOK` | unset | Optional Discord webhook for the weekly summary post. |

## Adding a new host or LXC

Either set `MAINTENANCE_SCAN_LXC_IDS` to include the new ID, or — for a
brand-new docker host — extend `default_targets()` in
`apps/maintenance_scan/probe.py` with a new `ProbeTarget` entry.

## Symptoms → likely causes

| Symptom | Likely cause | First check |
|---|---|---|
| No Monday Discord post (and no markdown report) | timer skipped (Alienware off when it fired AND not yet caught up); OR `MAINTENANCE_SCAN_DISCORD_WEBHOOK` unset | `systemctl --user list-timers \| grep maintenance`; `cat ~/.config/homelab-control/maintenance-scan.env` |
| Report wrote but Discord post didn't | webhook 4xx (channel deleted, webhook revoked) | `journalctl --user -u alienware-maintenance-scan.service \| grep discord` |
| Report shows 0 containers | SSH to Proxmox failed OR podman missing on Alienware | run `apps.maintenance_scan.probe.probe_all` interactively (see Investigation §3) |
| Every actionable upgrade shows "verifier rejected" | rate-limited by Docker Hub or ghcr; OR registry briefly down between probe and verifier rounds | check `journalctl` for HTTP 429; retry after 60s |
| Upgrade missed even though I know there's a new version | new version is a non-numeric tag (e.g., `pg17` from `pg16`); we treat those as `floating` by design | see severity legend; v2 will join with Trivy for richer signal |
| `bash: line 1: {{.Names}}: command not found` in journal | the probe is reaching into a host that doesn't have `docker` JSON output | the probe auto-falls-back to podman; if both are absent, the host is skipped — verify with `pct exec <id> -- command -v docker podman` |

## Investigation steps

1. `systemctl --user status alienware-maintenance-scan.service` — last exit code
2. `journalctl --user -u alienware-maintenance-scan.service -n 200 --no-pager` — full last-run log; look for `probe <host>: N container(s)` lines and any `verifier_round` audit entries
3. Interactively re-run the probe alone:
   ```bash
   cd ~/git/homelab-control && .venv/bin/python -c "
   import asyncio, logging
   logging.basicConfig(level='INFO')
   from apps.maintenance_scan.probe import probe_all
   rows = asyncio.run(probe_all())
   for r in rows: print(r.host, r.container, r.image, ':' + r.tag)
   "
   ```
4. `tail ~/.local/state/homelab-control/agent-homelab-maintainer/audit.jsonl | jq .` — verifier verdicts captured?
5. `.venv/bin/python -m apps._shared.audit verify ~/.local/state/homelab-control/agent-homelab-maintainer/audit.jsonl` — chain still intact?
6. Manual dry-run end-to-end: `.venv/bin/python -m apps.maintenance_scan --dry-run -v` (writes nothing, full log)

## Recovery

- **Skipped run**: `systemctl --user start alienware-maintenance-scan.service` — idempotent (the report file is named by date, overwrites only same-day).
- **Wrong Discord webhook**: edit `~/.config/homelab-control/maintenance-scan.env`, then `systemctl --user daemon-reload` is NOT needed (the service re-reads env on next start).
- **Rate limited**: do nothing; the verifier will succeed next week. If you need it now, wait 1h then re-trigger manually.

## Past incidents

### 2026-05-17 — `pgvector/pgvector:pg16` reported as actionable upgrade (incorrectly)

- **Symptom:** initial smoke runs suggested upgrading `pgvector:pg16` to `pgvector:17` — that would have been a disastrous Postgres major-version upgrade
- **Root cause:** original regex `^v?(\d+(?:\.\d+){0,3})([\-+].*)?$` matched `16-alpine` and `pg16` as `(16,)`, then proposed any higher numeric tag as an upgrade
- **Fix:** tightened to `^v?(\d+(?:\.\d+){0,3})(\+.*)?$` — reject hyphen suffixes; classify them as `floating`
- **Followup:** added regression tests `test_parse_numeric[16-alpine]`, `test_assess_image_marks_non_numeric_tag_as_floating`; documented severity legend in this runbook

### 2026-05-17 — probe returned 0 containers from every LXC

- **Symptom:** end-to-end test reported `=== 0 containers ===` despite 12+ running on the hosts
- **Root cause:** the `--format '{{.Names}}'` template was eaten by remote bash when ssh stripped the quoting
- **Fix:** replaced template format with `docker ps -q` + `docker inspect <ids>` parsing JSON
- **Followup:** general gotcha documented in `backup-lxcs.md` §Past incidents; same pattern used in any future docker-over-ssh code

## Future enhancements (v2)

- **CVE feed join** (Trivy as a sidecar): scan each image for known CVEs
  and bump severity for any image with criticals.
- **Planka card creation**: once we have a real Planka HTTP client, open
  one card per accepted upgrade so the operator can track the rollout.
- **Auto-PR**: for upgrades the verifier accepted, draft a PR against
  the appropriate compose file with the tag bump — gated behind explicit
  approval before merge.
