#!/usr/bin/env bash
# Proxmox-side restic backup for the LXC service stack (Phase 0.13 follow-up).
#
# Runs on the Proxmox host (root). For each managed LXC, executes a
# per-service "prep" that produces logical dumps (pg_dump, volume tarballs,
# config exports) into /tmp/lxc-backup/ inside the LXC, then streams the
# resulting tar into restic via stdin — no host-side staging directory,
# no rootfs mounts.
#
# Targets (BACKUP_REPOSITORIES env, comma-separated):
#   - /var/lib/vz/dump/restic-lxcs        (local on Proxmox)
#   - sftp://... or s3://... etc          (off-host, optional)
#
# Restic password: $RESTIC_PASSWORD_FILE (chmod 600).
#
# Snapshot tagging: every snapshot gets tags `lxc` + `lxc-<id>` so policies
# and queries can scope by service.
#
# Retention: --keep-daily 30 --keep-weekly 8 --keep-monthly 12.
#
# Exit codes:
#   0  every (lxc x target) pair succeeded
#   1  one or more failures (per-failure logged; other targets still attempted)
#   2  no targets configured / restic missing / config error

set -o pipefail
# Intentionally NOT using `set -u`: PIPESTATUS array indexing trips it.

RESTIC=${RESTIC_BIN:-/usr/local/bin/restic}
ENV_FILE=${BACKUP_ENV_FILE:-/etc/homelab-control/backup-lxcs.env}

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

log() { printf '%(%Y-%m-%dT%H:%M:%S%z)T %s\n' -1 "$*" >&2; }
fail() { log "FAIL: $*"; exit_code=1; }

exit_code=0

if [[ -z "${BACKUP_REPOSITORIES:-}" ]]; then
  log "BACKUP_REPOSITORIES unset; nothing to do"
  exit 2
fi
if [[ ! -x "$RESTIC" ]]; then
  log "restic not found at $RESTIC"
  exit 2
fi
if [[ -z "${RESTIC_PASSWORD_FILE:-}" || ! -r "$RESTIC_PASSWORD_FILE" ]]; then
  log "RESTIC_PASSWORD_FILE missing or unreadable"
  exit 2
fi

IFS=',' read -ra REPOSITORIES <<< "$BACKUP_REPOSITORIES"

# ----- per-LXC prep scripts -----------------------------------------------
# Each function emits bash that runs INSIDE the LXC and produces dump files
# in /tmp/lxc-backup/. Keep these idempotent and side-effect-free elsewhere.

prep_memory_engine() {
cat <<'INNER'
set -euo pipefail
out=/tmp/lxc-backup; rm -rf "$out"; mkdir -p "$out"
date -Iseconds > "$out/timestamp"
# pg_dumpall covers all dbs (memory, n8n) + roles + privileges
docker exec memory-postgres pg_dumpall -U memory \
  --clean --if-exists --quote-all-identifiers \
  > "$out/postgres.sql"
# Docker-volume captures (each volume → one tar inside the LXC)
for vol in memory-engine_qdrant_data memory-engine_mem0_history \
           memory-engine_planka_data memory-engine_khoj_data \
           memory-engine_n8n_data; do
  docker run --rm -v "${vol}:/data" -v "${out}:/out" alpine \
    tar -cf "/out/${vol}.tar" -C /data . 2>/dev/null
done
cp /opt/memory-engine/.env "$out/memory-engine.env" 2>/dev/null || true
ls -la "$out"
INNER
}

prep_forgejo() {
cat <<'INNER'
set -euo pipefail
out=/tmp/lxc-backup; rm -rf "$out"; mkdir -p "$out"
date -Iseconds > "$out/timestamp"
docker exec forgejo-forgejo-db-1 pg_dumpall -U forgejo \
  --clean --if-exists --quote-all-identifiers \
  > "$out/postgres.sql" 2>/dev/null \
  || docker exec forgejo-forgejo-db-1 pg_dumpall -U postgres \
       --clean --if-exists --quote-all-identifiers > "$out/postgres.sql"
docker run --rm -v forgejo_forgejo_data:/data -v "${out}:/out" alpine \
  tar -cf /out/forgejo_data.tar -C /data .
cp /opt/homelab-control/compose/forgejo/.env "$out/forgejo.env" 2>/dev/null || true
ls -la "$out"
INNER
}

prep_vaultwarden() {
cat <<'INNER'
set -euo pipefail
out=/tmp/lxc-backup; rm -rf "$out"; mkdir -p "$out"
date -Iseconds > "$out/timestamp"
docker run --rm -v vaultwarden_vaultwarden_data:/data -v "${out}:/out" alpine \
  tar -cf /out/vaultwarden_data.tar -C /data .
cp /opt/homelab-control/compose/vaultwarden/.env "$out/vaultwarden.env" 2>/dev/null || true
ls -la "$out"
INNER
}

prep_infisical() {
cat <<'INNER'
set -euo pipefail
out=/tmp/lxc-backup; rm -rf "$out"; mkdir -p "$out"
date -Iseconds > "$out/timestamp"
docker exec infisical-infisical-db-1 pg_dumpall -U infisical \
  --clean --if-exists --quote-all-identifiers \
  > "$out/postgres.sql" 2>/dev/null \
  || docker exec infisical-infisical-db-1 pg_dumpall -U postgres \
       --clean --if-exists --quote-all-identifiers > "$out/postgres.sql"
cp /opt/homelab-control/compose/infisical/.env "$out/infisical.env" 2>/dev/null || true
ls -la "$out"
INNER
}

# Map LXC id → prep function name. Add new services here.
declare -A LXC_PREPS=(
  [200]=prep_memory_engine
  [201]=prep_forgejo
  [202]=prep_vaultwarden
  [203]=prep_infisical
)

backup_one_lxc() {
  local id=$1 prep_fn=$2 name script tar_status restic_status
  name=$(pct config "$id" 2>/dev/null | awk -F': ' '/^hostname:/ {print $2}')
  [[ -z "$name" ]] && name="lxc-$id"

  log "lxc $id ($name): prep starting"
  script=$($prep_fn)
  # Run the prep INSIDE the LXC
  if ! pct exec "$id" -- bash -c "$script" >&2; then
    fail "lxc $id ($name): prep failed"
    return 1
  fi

  for repo in "${REPOSITORIES[@]}"; do
    log "lxc $id ($name): backup -> $repo"
    # Stream the prep output tar from inside the LXC straight into restic
    pct exec "$id" -- tar -cf - -C /tmp/lxc-backup . \
      | RESTIC_REPOSITORY="$repo" \
        RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE" \
        XDG_CACHE_HOME="${XDG_CACHE_HOME:-/var/cache}" \
        "$RESTIC" backup \
          --stdin --stdin-filename "lxc-${id}-${name}.tar" \
          --tag lxc --tag "lxc-${id}" --tag "$name" \
          --host "$(hostname)-lxc-${id}" \
          --no-scan
    local statuses=("${PIPESTATUS[@]}")
    tar_status=${statuses[0]:-0}
    restic_status=${statuses[1]:-0}
    if [[ $tar_status -ne 0 || $restic_status -ne 0 ]]; then
      fail "lxc $id ($name) -> $repo: tar=$tar_status restic=$restic_status"
      continue
    fi

    # Forget + prune for this tag in this repo (per-LXC retention scope)
    RESTIC_REPOSITORY="$repo" \
    RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE" \
    XDG_CACHE_HOME="${XDG_CACHE_HOME:-/var/cache}" \
    "$RESTIC" forget --prune \
      --tag "lxc-${id}" --host "$(hostname)-lxc-${id}" \
      --keep-daily 30 --keep-weekly 8 --keep-monthly 12 \
      >&2 \
      || fail "lxc $id ($name) -> $repo: forget/prune failed"
  done

  # Clean up the prep dir inside the LXC so we don't leave dumps lying around
  pct exec "$id" -- rm -rf /tmp/lxc-backup >/dev/null 2>&1 || true
}

# ----- main loop ----------------------------------------------------------

for id in "${!LXC_PREPS[@]}"; do
  if ! pct status "$id" 2>/dev/null | grep -q running; then
    log "lxc $id: not running, skipping"
    continue
  fi
  backup_one_lxc "$id" "${LXC_PREPS[$id]}"
done

if [[ $exit_code -eq 0 ]]; then
  log "all LXC backups OK"
else
  log "completed with failures (exit $exit_code)"
fi
exit $exit_code
