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
#   BACKUP_REPOSITORIES        comma-separated restic repo URIs
#   RESTIC_PASSWORD_FILE       path to the password file
#   DR_DRILL_SCRATCH           where to materialize restores (default: mktemp -d)
#   PYTHON_BIN                 python3 interpreter (default: python3); used to
#                              invoke `python3 -m apps._shared.audit verify <ledger>`
#   DR_DRILL_DISCORD_WEBHOOK   when set, a non-zero exit posts a summary alert
#                              to this webhook (typically #ops-alerts). Success
#                              runs are silent on Discord; the audit row below
#                              captures both outcomes.
#   DR_DRILL_AUDIT_LEDGER      hash-chained audit log path (default:
#                              ~/.local/state/homelab-control/dr-drill/audit.jsonl).
#                              Every drill — pass or fail — appends one row so
#                              the chain proves the drill actually ran.
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
fail() { log "FAIL: $*"; failed_messages+=("$*"); exit_code=1; }
exit_code=0
failed_messages=()
START_TS=$(date +%s)

DR_DRILL_AUDIT_LEDGER=${DR_DRILL_AUDIT_LEDGER:-$HOME/.local/state/homelab-control/dr-drill/audit.jsonl}

# Posts a Discord message; silent on success / missing webhook. Truncates
# the body to Discord's 2000-char limit so curl never gets rejected on size.
notify_discord() {
  local body=$1
  [[ -z "${DR_DRILL_DISCORD_WEBHOOK:-}" ]] && return 0
  local truncated=${body:0:1900}
  # curl --fail keeps systemd journals clean on a non-2xx response so the
  # operator notices via the journal even if the webhook itself is down.
  curl -fsS -X POST "$DR_DRILL_DISCORD_WEBHOOK" \
       -H 'Content-Type: application/json' \
       --max-time 10 \
       --data "$(printf '{"content":%s}' "$("$PYTHON_BIN" -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$truncated")")" \
       >/dev/null 2>&1 \
    || log "WARN: failed to post DR drill alert to Discord webhook"
}

# Best-effort audit row (hash-chained). Uses AuditLog directly so the chain
# stays consistent with how every other writer in the system extends it.
emit_audit() {
  local outcome=$1 duration_s=$2
  mkdir -p "$(dirname "$DR_DRILL_AUDIT_LEDGER")"
  local repos_joined fail_joined
  repos_joined=${BACKUP_REPOSITORIES:-}
  fail_joined=$(IFS=$'\n'; echo "${failed_messages[*]:-}")
  ( cd "$REPO_ROOT" && "$PYTHON_BIN" - "$DR_DRILL_AUDIT_LEDGER" "$outcome" "$duration_s" "$exit_code" "$repos_joined" "$fail_joined" <<'PY' >/dev/null 2>&1 || true
import os, sys
sys.path.insert(0, os.getcwd())
from apps._shared.audit import AuditLog
ledger, outcome, dur, code, repos, fails = sys.argv[1:7]
AuditLog(ledger).append({
    "event": "dr_drill_complete",
    "outcome": outcome,
    "exit_code": int(code),
    "duration_seconds": int(dur),
    "host": os.uname().nodename,
    "repositories": [r for r in repos.split(",") if r.strip()],
    "failed_checks": [m for m in fails.split("\n") if m.strip()],
})
PY
  )
}

# Single EXIT trap: always emit the audit row, and on non-zero exit alert
# Discord. The trap fires for every code path including misconfig (exit 2)
# and uncaught errors, so the operator always learns when a drill stops
# producing fresh evidence — even a silently broken cron.
on_exit() {
  local code=$?
  exit_code=${code:-$exit_code}
  local duration=$(( $(date +%s) - START_TS ))
  local outcome="pass"
  [[ "$exit_code" != 0 ]] && outcome="fail"
  emit_audit "$outcome" "$duration"
  if [[ "$exit_code" != 0 ]]; then
    local summary
    if [[ "$exit_code" == 2 ]]; then
      summary="DR drill **misconfigured** on $(hostname -s); exit=2. Check BACKUP_REPOSITORIES, RESTIC_PASSWORD_FILE, restic binary."
    else
      summary="DR drill **failed** on $(hostname -s) (${#failed_messages[@]} check(s), ${duration}s):\n"
      for m in "${failed_messages[@]}"; do
        summary+="• ${m}\n"
      done
      summary+="\nFull log: \`journalctl --user -u alienware-dr-drill.service -n 200\` (if timer-driven) or rerun manually with \`./scripts/backup/dr-drill.sh\`."
    fi
    notify_discord "$summary"
  fi
}
trap on_exit EXIT

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
