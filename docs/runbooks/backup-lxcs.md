# LXC backup on Proxmox (Phase 0.13 follow-up)

**Owner:** `agent:homelab-maintainer` (operational), Kevin (LXC inventory).
**Pairs with:** [`backup.md`](backup.md) (Alienware-side restic for agent state).
**Runs on:** Proxmox host (`proxmox.dev-path.org`), `root`.

## What this covers

Daily 04:00 restic snapshots of every managed LXC's logical state:

| LXC | Hostname | Captured | Source of truth |
| --- | --- | --- | --- |
| 200 | memory-engine | `pg_dumpall` (memory + n8n DBs) + tarballs of khoj/mem0/planka/n8n/qdrant docker volumes + `/opt/memory-engine/.env` | PG dump |
| 201 | forgejo | `pg_dumpall` + `forgejo_data` volume tar + `.env` | PG dump |
| 202 | vaultwarden | `vaultwarden_data` volume tar + `.env` | sqlite in the data volume |
| 203 | infisical | `pg_dumpall` + `.env` | PG dump |

LXCs deliberately **not** covered (network/utility, configuration-only,
trivially rebuildable from compose files in this repo):

- 100 pihole, 102 nginxproxymanager, 103 homebridge, 105 searxng,
  204 homelab-operator.

## Architecture

```
                              proxmox-backup-lxcs.timer (04:00 daily)
                                       ↓
                  /usr/local/bin/proxmox-backup-lxcs (this script)
                                       ↓
   for each LXC:
     pct exec <id> -- <prep script> # pg_dumpall, docker volume tars → /tmp/lxc-backup/
     pct exec <id> -- tar -cf - -C /tmp/lxc-backup .
       | restic backup --stdin --stdin-filename "lxc-<id>-<name>.tar"
                       --tag lxc --tag lxc-<id> --tag <name>
                       --host pve-lxc-<id>
                       (env: BACKUP_REPOSITORIES, RESTIC_PASSWORD_FILE)
     pct exec <id> -- rm -rf /tmp/lxc-backup
```

Each snapshot is **one logical tar** (restic chunker still dedups across
snapshots — successive PG dumps share most chunks).

`--host pve-lxc-<id>` scopes retention per LXC. Tags `lxc-<id>` + the
service name make `restic snapshots --tag forgejo` and similar queries
trivial.

## Targets

`BACKUP_REPOSITORIES` (comma-separated) in
`/etc/homelab-control/backup-lxcs.env`. Today (two-copy redundancy):

```
BACKUP_REPOSITORIES=/var/lib/vz/dump/restic-lxcs,sftp:kenns@192.168.1.45:/mnt/spinny/restic-lxcs-from-proxmox
```

Symmetric to the Alienware side:

| Source | Local repo | Off-host repo |
| --- | --- | --- |
| Alienware agent state | `/mnt/spinny/restic-homelab` | `sftp:root@proxmox.dev-path.org:/var/lib/vz/dump/restic-homelab-alienware` |
| Proxmox LXC state | `/var/lib/vz/dump/restic-lxcs` | `sftp:kenns@192.168.1.45:/mnt/spinny/restic-lxcs-from-proxmox` |

Each host owns its own primary repo on its own disk and mirrors to
the other host's spinny disk over SSH. A loss of either machine's
disk still leaves a complete copy of all data on the other.

Same passphrase as the Alienware repos for now (one passphrase to
remember). Stored at `/etc/homelab-control/restic-password` (chmod 600).

Retention: `--keep-daily 30 --keep-weekly 8 --keep-monthly 12`,
per-LXC scoped (via `--host pve-lxc-<id>`).

## Setup

One-time install on Proxmox:

```bash
# 1. Restic
cd /tmp
ver=0.17.3
curl -fL https://github.com/restic/restic/releases/download/v${ver}/restic_${ver}_linux_amd64.bz2 \
  -o restic.bz2 && bunzip2 -f restic.bz2 && chmod +x restic && mv restic /usr/local/bin/
restic version

# 2. Password (copy from Alienware, or generate fresh and store both)
mkdir -p /etc/homelab-control
# from Mac: ssh kenns@alienware 'cat ~/.config/homelab-control/restic-password' \
#   | ssh root@proxmox 'cat > /etc/homelab-control/restic-password'
chmod 600 /etc/homelab-control/restic-password

# 3. Authorize Proxmox root → Alienware kenns (one-time, for the sftp mirror)
ssh-copy-id -i /root/.ssh/id_rsa.pub kenns@192.168.1.45
ssh kenns@192.168.1.45 'mkdir -p /mnt/spinny/restic-lxcs-from-proxmox'

# 4. Env (both targets)
cat > /etc/homelab-control/backup-lxcs.env <<'EOF'
BACKUP_REPOSITORIES=/var/lib/vz/dump/restic-lxcs,sftp:kenns@192.168.1.45:/mnt/spinny/restic-lxcs-from-proxmox
RESTIC_PASSWORD_FILE=/etc/homelab-control/restic-password
EOF
chmod 600 /etc/homelab-control/backup-lxcs.env

# 5. Init both repos (same passphrase secures both)
mkdir -p /var/lib/vz/dump/restic-lxcs
for repo in /var/lib/vz/dump/restic-lxcs \
            sftp:kenns@192.168.1.45:/mnt/spinny/restic-lxcs-from-proxmox; do
  RESTIC_REPOSITORY="$repo" \
    RESTIC_PASSWORD_FILE=/etc/homelab-control/restic-password \
    XDG_CACHE_HOME=/var/cache \
    restic init || true   # idempotent
done

# 6. Script + units (committed to repo)
cp /root/homelab-control-git/scripts/proxmox-backup/backup-lxcs.sh \
   /usr/local/bin/proxmox-backup-lxcs
chmod +x /usr/local/bin/proxmox-backup-lxcs
cp /root/homelab-control-git/systemd/proxmox-backup-lxcs.{service,timer} \
   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now proxmox-backup-lxcs.timer

# 7. Smoke
systemctl start proxmox-backup-lxcs.service
journalctl -u proxmox-backup-lxcs.service -n 60 --no-pager
# Expect: each `lxc <id>` line appears twice (once per repo), then
# "all LXC backups OK".
```

## Restore

```bash
export RESTIC_REPOSITORY=/var/lib/vz/dump/restic-lxcs
export RESTIC_PASSWORD_FILE=/etc/homelab-control/restic-password
export XDG_CACHE_HOME=/var/cache    # systemd no-HOME quirk

# What do we have?
restic snapshots --tag forgejo

# Pull the latest forgejo snapshot to a working dir
restic restore latest --tag forgejo --target /tmp/restore-forgejo

# Inside /tmp/restore-forgejo/ is the tar we wrote on backup; extract:
mkdir /tmp/forgejo && tar -xf /tmp/restore-forgejo/lxc-201-forgejo.tar -C /tmp/forgejo
ls /tmp/forgejo
# → forgejo_data.tar  postgres.sql  forgejo.env  timestamp

# Restore PG into the live LXC's postgres:
cat /tmp/forgejo/postgres.sql | pct exec 201 -- docker exec -i forgejo-forgejo-db-1 psql -U forgejo -d postgres

# Restore the data volume (destructive — stop the service first):
pct exec 201 -- docker compose -f /opt/homelab-control/compose/forgejo/docker-compose.yml down
pct push 201 /tmp/forgejo/forgejo_data.tar /tmp/forgejo_data.tar
pct exec 201 -- docker run --rm -v forgejo_forgejo_data:/data -v /tmp:/in alpine \
  sh -c 'rm -rf /data/* /data/..?* /data/.[!.]* 2>/dev/null; tar -xf /in/forgejo_data.tar -C /data'
pct exec 201 -- docker compose -f /opt/homelab-control/compose/forgejo/docker-compose.yml up -d
```

Pattern repeats per-LXC: stop service → import dumps → start service.

## Health

```bash
export RESTIC_REPOSITORY=/var/lib/vz/dump/restic-lxcs
export RESTIC_PASSWORD_FILE=/etc/homelab-control/restic-password
export XDG_CACHE_HOME=/var/cache

restic check                                # cheap metadata integrity
restic check --read-data-subset=10%         # weekly: data integrity sample
restic snapshots --group-by host,tags       # cleanly grouped per-LXC view
```

## Adding a new service

1. Bring up the LXC and its docker stack.
2. In `scripts/proxmox-backup/backup-lxcs.sh`:
   - Add a `prep_<service>` function (same shape as the existing ones —
     produce dumps + tars in `/tmp/lxc-backup/`).
   - Add `[<id>]=prep_<service>` to the `LXC_PREPS` map.
3. Redeploy: `scp scripts/proxmox-backup/backup-lxcs.sh
   root@proxmox.dev-path.org:/usr/local/bin/proxmox-backup-lxcs`.
4. Smoke: `systemctl start proxmox-backup-lxcs.service && journalctl
   -u proxmox-backup-lxcs.service -f`.

## Operating tips

- The script **continues on failure**: one LXC's prep failure doesn't
  skip the others. Service exit code is non-zero if any LXC failed, so
  `systemctl status` shows red.
- Each per-LXC backup is **independent**: tagging by `--host
  pve-lxc-<id>` means `forget --prune` for LXC 201 doesn't touch 200's
  snapshots, so an obsolete service can be removed without poisoning
  other retention windows.
- `/tmp/lxc-backup/` inside each LXC is cleaned up between runs.
  Nothing persistent leaks.
