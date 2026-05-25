#!/usr/bin/env bash
# Post-deploy hooks for the Alienware agent stack (run after git pull / install).
#
# - Removes agent smoke / test Planka cards (verify-*, A2A help:, live.smoke*, …)
# - Live sub-agent smoke (executive + maintainer) when gateway is configured
# - Optionally restarts core agent units when RESTART_AGENT_SERVICES=1
#
# Usage (on Alienware):
#   cd ~/git/homelab-control && ./scripts/post_deploy_agent_stack.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PLANKA_CLEANUP_ENV:-${HOME}/.config/homelab-control/agent-executive.env}"

cleanup_planka() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    echo "post_deploy: skip Planka cleanup (${ENV_FILE} missing)" >&2
    return 0
  fi
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
  export PYTHONPATH="${HOMELAB_CONTROL_ROOT:-${ROOT_DIR}}"
  echo "post_deploy: Planka test-card cleanup (--execute)"
  if python3 "${ROOT_DIR}/scripts/planka_cleanup_test_cards.py" --execute --json; then
    echo "post_deploy: Planka cleanup finished"
  else
    echo "post_deploy: Planka cleanup failed (non-fatal)" >&2
  fi
}

smoke_subagent() {
  if [[ "${SKIP_SUBAGENT_SMOKE:-0}" == "1" ]]; then
    echo "post_deploy: skip sub-agent smoke (SKIP_SUBAGENT_SMOKE=1)" >&2
    return 0
  fi
  if [[ ! -f "${ENV_FILE}" ]]; then
    echo "post_deploy: skip sub-agent smoke (${ENV_FILE} missing)" >&2
    return 0
  fi
  if [[ ! -x "${ROOT_DIR}/scripts/live_smoke_subagent.sh" ]]; then
    echo "post_deploy: skip sub-agent smoke (live_smoke_subagent.sh missing)" >&2
    return 0
  fi
  echo "post_deploy: sub-agent live smoke"
  if "${ROOT_DIR}/scripts/live_smoke_subagent.sh"; then
    echo "post_deploy: sub-agent smoke finished"
  else
    echo "post_deploy: sub-agent smoke failed (non-fatal)" >&2
  fi
}

restart_agents() {
  if [[ "${RESTART_AGENT_SERVICES:-0}" != "1" ]]; then
    return 0
  fi
  echo "post_deploy: restarting agent systemd units"
  systemctl --user restart \
    alienware-executive-agent.service \
    alienware-homelab-maintainer-agent.service \
    alienware-agent-event-dispatcher.service
}

cleanup_planka
smoke_subagent
restart_agents
