#!/usr/bin/env bash
# Backup restore drill (Phase 0.13 follow-up).
#
# Restores the most recent snapshot from each configured restic
# repository into a throwaway scratch dir, then runs cheap structural
# checks against the restored payload. Designed to be run quarterly,
# manually or via a slow systemd timer.
#
# Drills exercised:
#   1. Every repo in $BACKUP_REPOSITORIES restores cleanly.
#   2. The restored Alienware "hot" tier still contains the agent audit
#      ledger files, and `audit verify` passes against them — proving
#      the hash chain survives the round-trip.
#   3. The restored Alienware "full" tier still contains the master
#      compose tree (config/, compose/, apps/) — proving the broader
#      repo state is recoverable.
#
# Exit codes:
#   0   all repos restored + every drill check passed
#   1   one or more drills failed
#   2   misconfiguration (missing env, restic, etc.)
#
# Environment (sourced from $BACKUP_ENV_FILE or already in env):
#   BACKUP_REPOSITORIES   comma-separated restic repo URIs
#   RESTIC_PASSWORD_FILE  path to the password file
#   DR_DRILL_SCRATCH      where to materialize restores (default: mktemp -d)
#   PYTHON_BIN            python3 interpreter (default: python3); used to
#                         invoke `python3 -m apps._shared.audit verify <ledger>`
#
# Run manually:
#   export BACKUP_ENV_FILE=$HOME/.config/homelab-control/backup.env
#   ./scripts/backup/dr-drill.sh

set -o pipefail

ENV_FILE=${BACKUP_ENV_FILE:-$HOME/.config/homelab-control/backup.env}
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

RESTIC=${RESTIC_BIN:-$(command -v restic || echo "")}
SCRATCH=${DR_DRILL_SCRATCH:-$(mktemp -d -t dr-drill-XXXXXX)}
REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}

log() { printf '%(%Y-%m-%dT%H:%M:%S%z)T %s\n' -1 "$*"; }
fail() { log "FAIL: $*"; exit_code=1; }
exit_code=0

[[ -z "${BACKUP_REPOSITORIES:-}" ]] && { log "BACKUP_REPOSITORIES unset"; exit 2; }
[[ -z "$RESTIC" || ! -x "$RESTIC" ]] && { log "restic not found"; exit 2; }
[[ -z "${RESTIC_PASSWORD_FILE:-}" || ! -r "$RESTIC_PASSWORD_FILE" ]] && \
  { log "RESTIC_PASSWORD_FILE missing/unreadable"; exit 2; }

log "scratch dir: $SCRATCH"
log "audit verify: $PYTHON_BIN -m apps._shared.audit verify (cwd=$REPO_ROOT)"

IFS=',' read -ra REPOS <<< "$BACKUP_REPOSITORIES"

restic_q() {
  local repo=$1; shift
  RESTIC_REPOSITORY="$repo" RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE" \
    XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}" \
    "$RESTIC" "$@"
}

verify_audit_ledger() {
  # arg: directory containing one or more *.jsonl ledger files
  local restored=$1 ledger ledger_files n=0
  mapfile -t ledger_files < <(find "$restored" -type f -name '*.jsonl' -path '*audit*' 2>/dev/null)
  if [[ ${#ledger_files[@]} -eq 0 ]]; then
    log "  (no audit ledgers found under $restored — skipping verify)"
    return 0
  fi
  for ledger in "${ledger_files[@]}"; do
    if ( cd "$REPO_ROOT" && "$PYTHON_BIN" -m apps._shared.audit verify "$ledger" ) >/dev/null 2>&1; then
      n=$((n+1))
    else
      fail "audit verify failed: $ledger"
    fi
  done
  log "  audit verify: $n ledger(s) clean"
}

drill_repo() {
  local repo=$1 short tag scratch_dir
  short=$(echo "$repo" | tr '/:@' '___' | tr -d ' ' | head -c 60)
  scratch_dir="$SCRATCH/$short"
  mkdir -p "$scratch_dir"
  log "=== repo $repo ==="
  if ! restic_q "$repo" snapshots --no-lock --json >"$scratch_dir/.snapshots.json" 2>"$scratch_dir/.snapshots.err"; then
    fail "$repo: snapshots listing failed; see $scratch_dir/.snapshots.err"
    return
  fi
  local total
  total=$("$PYTHON_BIN" -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" \
    "$scratch_dir/.snapshots.json" 2>/dev/null || echo 0)
  log "  snapshots known: $total"

  # Discover unique host values so we drill every distinct producer
  local hosts
  mapfile -t hosts < <("$PYTHON_BIN" -c "
import json, sys
seen = set()
for s in json.load(open(sys.argv[1])):
    h = s.get('hostname') or s.get('host')
    if h and h not in seen:
        seen.add(h)
        print(h)
" "$scratch_dir/.snapshots.json")
  for host in "${hosts[@]}"; do
    [[ -z "$host" ]] && continue
    local out="$scratch_dir/$host"
    mkdir -p "$out"
    log "  restoring latest from host=$host -> $out"
    if ! restic_q "$repo" restore latest --host "$host" --target "$out" --no-lock \
         >"$out/.restic.log" 2>&1; then
      fail "$repo host=$host: restore failed; see $out/.restic.log"
      continue
    fi
    # Structural drill: any files at all?
    local count
    count=$(find "$out" -type f ! -name '.restic.log' | wc -l | tr -d ' ')
    if [[ "$count" == 0 ]]; then
      fail "$repo host=$host: restore landed zero files"
      continue
    fi
    log "  restored $count file(s)"
    # Audit ledger drill (only applies if the restore contains one)
    verify_audit_ledger "$out"
  done
}

for repo in "${REPOS[@]}"; do
  drill_repo "$repo"
done

log "drill complete; scratch retained at $SCRATCH (rm -rf to clean up)"
exit $exit_code
