# Backup + restore (Phase 0.13)

**Owner:** `agent:homelab-maintainer` (operational), Kevin (target
provisioning).

## What this protects

- **Hot tier** (hourly): everything under
  `~/.local/state/homelab-control/` on Alienware. That's the per-agent
  hash-chained audit ledgers, audit anchors, llm-calls JSONL, and
  relay offsets. Tiny, append-only, irreplaceable.
- **Full tier** (daily 03:30): hot + `~/.config/homelab-control/` (all
  bot tokens and env files), `~/.config/systemd/user/`, and the working
  trees of `~/git/homelab-control/` and `~/git/memory-engine/` (minus
  venvs, caches, git internals, node_modules).

What this does **not** protect (yet):

- **Forgejo, Planka, memory-engine Postgres, Qdrant, Vaultwarden,
  Infisical**: these live on the Proxmox host
  (`proxmox.dev-path.org`) as Docker volumes inside LXCs. The
  Alienware-side runner snapshots only Alienware-resident state.
  Two follow-up paths:
  1. **Run restic on Proxmox** with its own systemd timer covering
     `/var/lib/lxc/*/rootfs/opt/<service>/data` (or the equivalent
     bind-mounts), targeting the same off-host repo prefix
     (different sub-path). Preferred for PG: use
     `pg_dump`-then-snapshot rather than raw volume copy.
  2. **Cross-host restic from Alienware**: mount the relevant
     volumes read-only over NFS and add them to
     `config/backup/sources.yaml`. Simpler operationally, less
     robust if NFS hiccups.

## Targets

`BACKUP_REPOSITORIES` is a comma-separated list of restic repo URIs.
Today it points at two targets:

```
BACKUP_REPOSITORIES=/mnt/spinny/restic-homelab,sftp:root@proxmox.dev-path.org:/var/lib/vz/dump/restic-homelab-alienware
```

- **Local**: `/mnt/spinny/restic-homelab` on Alienware's spinning
  drive (916G volume, ~5M used today). Protects against operator
  error, ransomware on the SSD, NVMe failure.
- **Off-host**: SFTP to Proxmox host (94G root volume, 81G free at
  setup). Protects against full Alienware loss (theft, fire, dead
  hardware). Restic over SFTP needs only an SSH key — no daemon on
  Proxmox.

Both targets receive every snapshot; the runner iterates the list
serially. If one target is unreachable the run still attempts the
other (success per-target is reported individually) but the
service exit code is non-zero so journal shows red.

## Setup on Alienware

1. Install restic (one-time):

   ```bash
   ver=0.17.3
   curl -fL "https://github.com/restic/restic/releases/download/v${ver}/restic_${ver}_linux_amd64.bz2" \
     -o /tmp/restic.bz2 && bunzip2 -f /tmp/restic.bz2 && chmod +x /tmp/restic \
     && mv /tmp/restic ~/.local/bin/
   restic version
   ```

2. Create the password file (chmod 0600). The password protects the
   restic repo against an attacker who gets read access to the disk;
   keep it somewhere you can recover (Bitwarden / Vaultwarden):

   ```bash
   mkdir -p ~/.config/homelab-control
   umask 077
   echo "<long random passphrase>" > ~/.config/homelab-control/restic-password
   chmod 600 ~/.config/homelab-control/restic-password
   ```

3. Create the env file (`~/.config/homelab-control/backup.env`):

   ```
   BACKUP_REPOSITORIES=/mnt/spinny/restic-homelab
   RESTIC_PASSWORD_FILE=/home/kenns/.config/homelab-control/restic-password
   ```

4. Initialize the local repo (one-time):

   ```bash
   set -a; source ~/.config/homelab-control/backup.env; set +a
   RESTIC_REPOSITORY=/mnt/spinny/restic-homelab restic init
   ```

   Repeat for any additional target listed in `BACKUP_REPOSITORIES`.

5. Install + enable the units:

   ```bash
   cp ~/git/homelab-control/systemd/alienware-backup-*.{service,timer} \
      ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now alienware-backup-hot.timer
   systemctl --user enable --now alienware-backup-full.timer
   ```

6. Smoke-test a hot run:

   ```bash
   systemctl --user start alienware-backup-hot.service
   journalctl --user -u alienware-backup-hot.service -n 40 --no-pager
   RESTIC_REPOSITORY=/mnt/spinny/restic-homelab \
     RESTIC_PASSWORD_FILE=~/.config/homelab-control/restic-password \
     restic snapshots --tag hot | tail -10
   ```

## Restore

Restore is just `restic restore`. Examples:

```bash
set -a; source ~/.config/homelab-control/backup.env; set +a
export RESTIC_REPOSITORY=/mnt/spinny/restic-homelab

# Latest hot snapshot, full tree:
restic snapshots --tag hot
restic restore latest --tag hot --target /tmp/restore-hot

# A specific agent's ledger as of yesterday morning:
restic restore <snap-id> --target /tmp/restore \
  --include /home/kenns/.local/state/homelab-control/agent-executive

# Inspect without restoring:
restic ls latest --tag hot | grep agent-executive
```

The `latest --tag <tier>` filter is important — without `--tag`, restic
returns the most recent snapshot regardless of tier, which is usually
the hot one (smaller, more frequent) rather than the more inclusive
full one.

## Disaster-recovery drill

`scripts/backup/dr-drill.sh` restores the most recent snapshot from
every configured repository into a scratch directory, verifies the
restic restore succeeded (non-zero files), and runs `python3 -m
apps._shared.audit verify` against any audit ledgers in the restored
tree. Proves the chain survives a full round-trip.

Wired up as a quarterly systemd timer:

- `systemd/alienware-backup-dr-drill.service`
- `systemd/alienware-backup-dr-drill.timer` — first Sunday of
  Jan/Apr/Jul/Oct at 03:00 ± 30 min

Run manually anytime:

```bash
export BACKUP_ENV_FILE=$HOME/.config/homelab-control/backup.env
bash ~/git/homelab-control/scripts/backup/dr-drill.sh
```

Expected good output ends with `drill complete; scratch retained at
…` and `exit 0`. On failure the scratch dir is preserved and the
restic restore log is at `<scratch>/<repo>/<host>/.restic.log`.

The same script can run on Proxmox against the LXC-side repo:

```bash
BACKUP_ENV_FILE=/etc/homelab-control/backup-lxcs.env \
  bash /usr/local/bin/proxmox-dr-drill
```

## Health checks

Run weekly (or wire into a future weekly-review report):

```bash
# Verify on-disk integrity of repo metadata (cheap):
restic check

# Spot-check data integrity by re-reading 5% of data (slower):
restic check --read-data-subset=5%

# Disk usage:
restic stats latest --tag hot --mode raw-data
restic stats latest --tag full --mode raw-data
```

If `restic check` ever errors, do not panic — but do NOT prune until
the error is understood. The most common cause is partial writes
during a power loss; `restic rebuild-index` typically resolves it.

## Operating tips

- **Idempotent retries**: timers use `Persistent=true`, so if Alienware
  was off when the timer fired, the run catches up on next boot.
- **Concurrent safety**: restic uses repo-side locks. If a hot run and
  a full run race, the latter waits.
- **Skipped paths are silently OK**: the runner logs `skip (does not
  exist)` for missing optional sources — useful when `memory-engine` is
  cloned in a non-standard location.
- **Adding the off-host target** is a 2-step change once the Proxmox
  firewall opens up: `restic init` against the new repo, then update
  `BACKUP_REPOSITORIES` in `backup.env`. No code changes.

## Symptoms → likely causes

| Symptom | Likely cause | First check |
|---|---|---|
| "Backup didn't run last night" | host was off when the timer fired AND `Persistent=true` hasn't caught up yet; OR the unit failed and the timer skipped a cycle | `systemctl --user list-timers \| grep backup`; `systemctl --user status alienware-backup-hot.service` |
| "Restic complains about cache directory" | systemd-run env has no `XDG_CACHE_HOME` and no `HOME` | service should set `XDG_CACHE_HOME=/var/cache` — see Past incidents 2026-05-15 |
| "`unbound variable PIPESTATUS`" in journal | `set -u` + pipe error path — common when restic is missing | see Past incidents 2026-05-15; fix is `statuses=(\"${PIPESTATUS[@]}\")` then `${statuses[0]:-0}` |
| "DR drill couldn't parse `restic snapshots` host" | text output of `restic snapshots` has spaces in the timestamp column | DR drill must use `--json`; see `scripts/backup/dr-drill.sh` Past incidents 2026-05-16 |
| "SSH push from Proxmox to Alienware: permission denied" | Proxmox root pubkey not in Alienware `~kenns/.ssh/authorized_keys` | fix by appending the key on Alienware once; verify with `ssh -o BatchMode=yes kenns@alienware true` from Proxmox |
| "Repo is locked, won't run" | a previous run crashed leaving a lock; OR a long-running prune is still going | `restic -r <repo> list locks`; if confirmed stale, `restic -r <repo> unlock` |

## Investigation steps

1. `systemctl --user list-timers \| grep backup` — when did the timer last fire, when's next?
2. `systemctl --user status alienware-backup-{hot,full}.service` — exit code of the most recent run
3. `journalctl --user -u alienware-backup-hot.service -n 200 --no-pager` — full last-run log
4. `restic -r <repo> snapshots --json \| jq '.[-3:]'` — last 3 snapshots, with `time` and `paths`
5. `restic -r <repo> check` — repo metadata integrity (cheap; run after any suspected corruption)
6. `df -h /backup` (or wherever the local repo lives) — out of disk?

## Recovery

- **Timer skipped**: `systemctl --user start alienware-backup-hot.service` — runs immediately, idempotent.
- **Stale lock**: `restic -r <repo> unlock`. NEVER do this if you suspect a real run is in progress; check `ps aux | grep restic` first.
- **Snapshot integrity break**: `restic -r <repo> check --read-data-subset=5%` to confirm; if data is unrecoverable, restore from the OTHER repo (two-copy redundancy is the whole point).
- **Off-host target unreachable**: backups still write to the local repo; reachable target catches up on next run with `--no-cache`.

## Past incidents

### 2026-05-16 — DR drill could not parse `restic snapshots` host column

- **Symptom:** drill script crashed inside the host-name extraction loop
- **Root cause:** plain-text `restic snapshots` puts spaces in the timestamp, breaking awk-based field splitting
- **Fix:** switched to `restic snapshots --json` and parsed with Python `json.load`
- **Followup:** committed in `scripts/backup/dr-drill.sh`; documented in this runbook; quarterly drill added to alienware-backup-dr-drill.timer

### 2026-05-15 — `PIPESTATUS[1]: unbound variable` killed LXC backup

- **Symptom:** `backup-lxcs.sh` exited 1 with the message above
- **Root cause:** `set -u` + reading `PIPESTATUS[1]` when the second command in the pipe never started (e.g., restic missing)
- **Fix:** snapshot `statuses=("${PIPESTATUS[@]}")` immediately, then dereference as `${statuses[0]:-0}` / `${statuses[1]:-0}`
- **Followup:** same pattern applied to all `set -u` scripts going forward; covered in this runbook

### 2026-05-15 — restic couldn't find cache dir under systemd

- **Symptom:** `unable to locate cache directory: neither $XDG_CACHE_HOME nor $HOME are defined`
- **Root cause:** systemd `User=` services don't inherit `HOME`; restic's cache lookup chain failed
- **Fix:** set `XDG_CACHE_HOME=/var/cache` explicitly in the service unit and in the script
- **Followup:** every restic-using service now has this env line; documented in §Setup
