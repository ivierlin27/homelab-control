#!/usr/bin/env bash
# Live smoke: finance categorize defer → Planka card (create + delete).
#
# Usage (on Alienware after agent-finance.env is configured):
#   cd ~/git/homelab-control && ./scripts/live_smoke_finance_planka.sh
#
# Options:
#   --no-cleanup   Leave the smoke card on the board (for inspection)
#   --env-file PATH   Default: ~/.config/homelab-control/agent-finance.env
#
# Exit 0 when card is created; also requires successful delete unless --no-cleanup.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${HOME}/.config/homelab-control"
FINANCE_ENV="${ENV_FILE:-${CONFIG_DIR}/agent-finance.env}"
DO_CLEANUP=1

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --no-cleanup) DO_CLEANUP=0; shift ;;
    --env-file) FINANCE_ENV="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

if [[ ! -f "${FINANCE_ENV}" ]]; then
  echo "live_smoke_finance_planka: missing ${FINANCE_ENV}" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${FINANCE_ENV}"
set +a
export PYTHONPATH="${HOMELAB_CONTROL_ROOT:-${ROOT_DIR}}"

log() { echo "live_smoke_finance_planka: $*"; }
fail() { echo "live_smoke_finance_planka: FAIL — $*" >&2; exit 1; }

log "preflight planka_defer_configured"
python3 -c "
from apps.finance_agent.categorize.planka import planka_defer_configured
import sys
sys.exit(0 if planka_defer_configured() else 1)
" || fail "Planka not ready — set PLANKA_BASE_URL, PLANKA_API_KEY, PLANKA_FINANCE_DEFER_LIST_ID"

CLEANUP_PY="True"
if [[ "${DO_CLEANUP}" -eq 0 ]]; then
  CLEANUP_PY="False"
fi

log "create smoke defer card (cleanup=${CLEANUP_PY})"
RESULT="$(python3 -c "
from apps.finance_agent.categorize.planka_smoke import live_smoke_finance_planka
import json
print(json.dumps(live_smoke_finance_planka(cleanup=${CLEANUP_PY})))
")"
echo "${RESULT}"

if [[ "${DO_CLEANUP}" -eq 1 ]]; then
  DELETED="$(python3 -c "import json,sys; print('true' if json.loads(sys.argv[1]).get('deleted') else 'false')" "${RESULT}")"
  if [[ "${DELETED}" != "true" ]]; then
    fail "card created but delete failed — check finance board for smoke-finance-defer"
  fi
  log "OK — Planka finance defer path verified"
else
  log "OK — card left on board (--no-cleanup)"
fi
