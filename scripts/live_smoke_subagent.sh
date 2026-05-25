#!/usr/bin/env bash
# Live smoke: sub-agent wiring on executive + maintainer (Alienware or any host
# with MODEL_GATEWAY_* and Planka credentials).
#
# Creates smoke-* Planka cards, verifies subagent_spawn/complete in trust ledgers,
# then deletes matched test cards unless --no-cleanup.
#
# Usage (on Alienware):
#   cd ~/git/homelab-control && ./scripts/live_smoke_subagent.sh
#
# Options:
#   --executive-only     Skip maintainer triage-intake
#   --maintainer-only    Skip executive handle-request
#   --no-cleanup         Leave Planka smoke cards for inspection
#   --skip-gateway-ping  Skip LiteLLM chat/completions preflight
#   --env-file PATH      Executive env file (default: ~/.config/.../agent-executive.env)
#   --timeout SEC        RLM_SUBCALL_TIMEOUT (default: 180)
#
# Environment:
#   HOMELAB_SUBAGENT_DISABLE=1  Forces skip (script exits 1 if set)
#   RLM_SUBCALL_TIMEOUT         Override subcall timeout without --timeout

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${HOME}/.config/homelab-control"
EXEC_ENV="${ENV_FILE:-${CONFIG_DIR}/agent-executive.env}"
MAINT_ENV="${CONFIG_DIR}/agent-homelab-maintainer.env"
EXEC_STATE="${HOME}/.local/state/homelab-control/agent-executive"
MAINT_QUEUE="${HOME}/.local/state/homelab-control/agent-homelab-maintainer"
SUBCALL_TIMEOUT="${RLM_SUBCALL_TIMEOUT:-180}"

RUN_EXEC=1
RUN_MAINT=1
DO_CLEANUP=1
GATEWAY_PING=1

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --executive-only) RUN_MAINT=0; shift ;;
    --maintainer-only) RUN_EXEC=0; shift ;;
    --no-cleanup) DO_CLEANUP=0; shift ;;
    --skip-gateway-ping) GATEWAY_PING=0; shift ;;
    --env-file) EXEC_ENV="$2"; shift 2 ;;
    --timeout) SUBCALL_TIMEOUT="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

if [[ ! -f "${EXEC_ENV}" ]]; then
  echo "live_smoke_subagent: missing ${EXEC_ENV}" >&2
  exit 1
fi

log() { echo "live_smoke_subagent: $*"; }
fail() { echo "live_smoke_subagent: FAIL — $*" >&2; exit 1; }

load_env() {
  set -a
  # shellcheck disable=SC1090
  source "${EXEC_ENV}"
  set +a
  export PYTHONPATH="${HOMELAB_CONTROL_ROOT:-${ROOT_DIR}}"
  export RLM_SUBCALL_TIMEOUT="${SUBCALL_TIMEOUT}"
}

ensure_maintainer_gateway_env() {
  [[ -f "${MAINT_ENV}" ]] || return 0
  local key line
  for key in MODEL_GATEWAY_BASE_URL MODEL_GATEWAY_API_KEY; do
    if grep -q "^${key}=" "${MAINT_ENV}" 2>/dev/null; then
      continue
    fi
    line="$(grep "^${key}=" "${EXEC_ENV}" 2>/dev/null || true)"
    if [[ -n "${line}" ]]; then
      echo "${line}" >> "${MAINT_ENV}"
      log "appended ${key} to ${MAINT_ENV}"
    fi
  done
}

preflight() {
  if [[ "${HOMELAB_SUBAGENT_DISABLE:-}" =~ ^(1|true|yes|on)$ ]]; then
    fail "HOMELAB_SUBAGENT_DISABLE is set"
  fi
  python3 - <<'PY' || fail "subagent_enabled() is false — check MODEL_GATEWAY_BASE_URL in ${EXEC_ENV}"
import os
import sys
sys.path.insert(0, os.environ["PYTHONPATH"])
from apps._shared.subagent.wiring import subagent_enabled
raise SystemExit(0 if subagent_enabled() else 1)
PY

  if [[ "${GATEWAY_PING}" -eq 1 ]]; then
    log "gateway ping (homelab-strong-long)"
    python3 - <<'PY' || fail "gateway chat/completions preflight failed"
import json
import os
import urllib.error
import urllib.request

base = os.environ["MODEL_GATEWAY_BASE_URL"].rstrip("/")
key = os.environ.get("MODEL_GATEWAY_API_KEY", "")
body = json.dumps(
    {
        "model": "homelab-strong-long",
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 8,
        "temperature": 0,
    }
).encode()
req = urllib.request.Request(
    f"{base}/chat/completions",
    data=body,
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode())
except urllib.error.HTTPError as exc:
    raise SystemExit(f"HTTP {exc.code}: {exc.read()[:200]!r}") from exc
except Exception as exc:
    raise SystemExit(str(exc)) from exc
content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
if not content.strip():
    raise SystemExit("empty completion")
print("gateway_ok", content.strip()[:40])
PY
  fi
}

assert_subagent_json() {
  local label="$1"
  local json_blob="$2"
  python3 -c "
import json, sys
label = sys.argv[1]
raw = sys.argv[2]
d = json.loads(raw)
sub = d.get(\"subagent\")
if not sub or not (sub.get(\"summary\") or \"\").strip():
    raise SystemExit(f\"{label}: missing subagent.summary\")
card = d.get(\"card\") or {}
if not card.get(\"created\"):
    raise SystemExit(f\"{label}: Planka card not created\")
print(f\"{label}: subagent ok ({len(sub.get('summary',''))} chars), card_id={card.get('card_id','')}\")
" "${label}" "${json_blob}"
}

assert_ledger_events() {
  local label="$1"
  local corr="$2"
  local ledger="$3"
  python3 -c "
import json, sys
from pathlib import Path
label, corr, path = sys.argv[1], sys.argv[2], Path(sys.argv[3])
events = []
for line in path.read_text(encoding=\"utf-8\").splitlines():
    if corr not in line:
        continue
    try:
        events.append(json.loads(line).get(\"event\"))
    except json.JSONDecodeError:
        pass
need = {\"subagent_spawn\", \"subagent_complete\"}
missing = need - set(events)
if missing:
    raise SystemExit(f\"{label}: ledger missing {sorted(missing)} for {corr} (saw {events})\")
print(f\"{label}: ledger ok ({', '.join(sorted(set(events) & need))})\")
" "${label}" "${corr}" "${ledger}"
}

smoke_executive() {
  local corr="live.smoke.subagent-$(date +%s)"
  log "executive handle-request (${corr})"
  local out
  out="$(
    python3 "${ROOT_DIR}/apps/executive_agent/main.py" handle-request \
      --title smoke-executive \
      --request "live.smoke subagent wiring verification (${corr})." \
      --domain homelab \
      --task-type research \
      --plan-ready \
      --conversation-id "${corr}" \
      --state-dir "${EXEC_STATE}"
  )"
  assert_subagent_json "executive" "${out}"
  assert_ledger_events "executive" "${corr}" "${EXEC_STATE}/trust-ledger.jsonl"
}

smoke_maintainer() {
  ensure_maintainer_gateway_env
  if [[ -f "${MAINT_ENV}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${MAINT_ENV}"
    set +a
  fi
  export PYTHONPATH="${HOMELAB_CONTROL_ROOT:-${ROOT_DIR}}"

  local intake="live.smoke.maintainer-$(date +%s)"
  log "maintainer triage-intake (${intake})"
  local out
  out="$(
    python3 "${ROOT_DIR}/apps/homelab_maintainer_agent/main.py" triage-intake \
      --intake-id "${intake}" \
      --title smoke-homelab-maintainer \
      --content "live.smoke maintainer subagent verification (${intake})." \
      --task-class summarize \
      --route local \
      --queue-dir "${MAINT_QUEUE}"
  )"
  assert_subagent_json "maintainer" "${out}"
  assert_ledger_events "maintainer" "${intake}" "${MAINT_QUEUE}/trust-ledger.jsonl"
}

cleanup_planka() {
  if [[ "${DO_CLEANUP}" -ne 1 ]]; then
    log "skip Planka cleanup (--no-cleanup)"
    return 0
  fi
  load_env
  log "Planka cleanup (--execute)"
  python3 "${ROOT_DIR}/scripts/planka_cleanup_test_cards.py" --execute
}

main() {
  load_env
  preflight
  [[ "${RUN_EXEC}" -eq 1 ]] && smoke_executive
  [[ "${RUN_MAINT}" -eq 1 ]] && smoke_maintainer
  cleanup_planka
  log "PASS"
}

main "$@"
