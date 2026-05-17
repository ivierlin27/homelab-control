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

- **Forgejo, Planka, memory-engine Postgres, Qdrant**: these live on
  the beelink/Proxmox host (`proxmox.dev-path.org`, 192.168.1.23). The
  Alienware-side runner cannot reach that host today — no SSH, no NFS
  export, all probed ports refused. To extend coverage:
  1. Open SSH (and optionally NFS) from the Proxmox firewall to
     Alienware's IP.
  2. Either mount an NFS share at `/mnt/proxmox-backup` and add
     `/mnt/proxmox-backup/restic-homelab` to `BACKUP_REPOSITORIES`,
     **or** run a parallel restic backup on the Proxmox host itself
     using the per-LXC volume snapshots (preferred for PG and Qdrant).
- Self-hosted services that Proxmox manages (vaultwarden, infisical).
  Same blocker as above.

## Targets

`BACKUP_REPOSITORIES` is a comma-separated list of restic repo URIs.
Today it points at one local repo on Alienware's spinning drive:

```
BACKUP_REPOSITORIES=/mnt/spinny/restic-homelab
```

When the Proxmox path opens up, add the off-host target:

```
BACKUP_REPOSITORIES=/mnt/spinny/restic-homelab,/mnt/proxmox-backup/restic-homelab
```

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
