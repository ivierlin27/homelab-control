#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
CONFIG_DIR="${HOME}/.config/homelab-control"
STATE_DIR="${HOME}/.local/state/homelab-control"

mkdir -p "${SYSTEMD_USER_DIR}" "${CONFIG_DIR}" \
  "${STATE_DIR}/agent-homelab/inbox" \
  "${STATE_DIR}/agent-homelab/processing" \
  "${STATE_DIR}/agent-homelab/done" \
  "${STATE_DIR}/agent-homelab/failed" \
  "${STATE_DIR}/agent-review/inbox" \
  "${STATE_DIR}/agent-review/processing" \
  "${STATE_DIR}/agent-review/done" \
  "${STATE_DIR}/agent-review/failed"

if [[ ! -f "${CONFIG_DIR}/agent-homelab.env" ]]; then
  cat > "${CONFIG_DIR}/agent-homelab.env" <<'EOF'
HOMELAB_CONTROL_ROOT=${HOME}/homelab-control
MODEL_GATEWAY_BASE_URL=https://model-gateway.dev-path.org/v1
MODEL_GATEWAY_API_KEY=replace-me
FORGEJO_BASE_URL=https://forgejo.dev-path.org
FORGEJO_REPO_OWNER=kevin
FORGEJO_REPO_NAME=homelab-control
FORGEJO_API_TOKEN=replace-me
AGENT_GIT_REMOTE=forgejo
AGENT_GIT_AUTHOR_NAME=agent-homelab
AGENT_GIT_AUTHOR_EMAIL=agent-homelab@forgejo.dev-path.org
AGENT_GIT_SSH_COMMAND=ssh -i ${HOME}/.ssh/forgejo_agent_homelab -o IdentitiesOnly=yes -p 2222
AGENT_PRINCIPAL=agent:homelab
EOF
  chmod 600 "${CONFIG_DIR}/agent-homelab.env"
fi

if [[ ! -f "${CONFIG_DIR}/agent-review.env" ]]; then
  cat > "${CONFIG_DIR}/agent-review.env" <<'EOF'
HOMELAB_CONTROL_ROOT=${HOME}/homelab-control
MODEL_GATEWAY_BASE_URL=https://model-gateway.dev-path.org/v1
MODEL_GATEWAY_API_KEY=replace-me
FORGEJO_BASE_URL=https://forgejo.dev-path.org
FORGEJO_REPO_OWNER=kevin
FORGEJO_REPO_NAME=homelab-control
FORGEJO_API_TOKEN=replace-me
REVIEW_AGENT_ALLOW_AUTO_MERGE=false
AGENT_GIT_AUTHOR_NAME=agent-review
AGENT_GIT_AUTHOR_EMAIL=agent-review@forgejo.dev-path.org
AGENT_PRINCIPAL=agent:review
EOF
  chmod 600 "${CONFIG_DIR}/agent-review.env"
fi

if [[ ! -f "${CONFIG_DIR}/agent-dispatcher.env" ]]; then
  cat > "${CONFIG_DIR}/agent-dispatcher.env" <<'EOF'
AGENT_DISPATCH_HOST=0.0.0.0
AGENT_DISPATCH_PORT=8765
AGENT_DISPATCH_TOKEN=replace-me
PLANKA_BASE_URL=https://planka.dev-path.org
PLANKA_EMAIL_OR_USERNAME=admin
PLANKA_PASSWORD=replace-me
PLANKA_BOARD_ID=replace-me
PLANKA_PLAN_READY_LIST_ID=replace-me
PLANKA_APPROVED_LIST_ID=replace-me
PLANKA_IN_PROGRESS_LIST_ID=replace-me
PLANKA_NEEDS_HUMAN_LIST_ID=replace-me
PLANKA_DONE_LIST_ID=replace-me
EOF
  chmod 600 "${CONFIG_DIR}/agent-dispatcher.env"
fi

if [[ ! -f "${CONFIG_DIR}/agent-activity.env" ]]; then
  cat > "${CONFIG_DIR}/agent-activity.env" <<'EOF'
AGENT_ACTIVITY_HOST=0.0.0.0
AGENT_ACTIVITY_PORT=8766
AGENT_ACTIVITY_TOKEN=replace-me
EOF
  chmod 600 "${CONFIG_DIR}/agent-activity.env"
fi

cp "${ROOT_DIR}/systemd/alienware-author-agent.service" "${SYSTEMD_USER_DIR}/alienware-author-agent.service"
cp "${ROOT_DIR}/systemd/alienware-review-agent.service" "${SYSTEMD_USER_DIR}/alienware-review-agent.service"
cp "${ROOT_DIR}/systemd/alienware-agent-platform-report.service" "${SYSTEMD_USER_DIR}/alienware-agent-platform-report.service"
cp "${ROOT_DIR}/systemd/alienware-agent-platform-report.timer" "${SYSTEMD_USER_DIR}/alienware-agent-platform-report.timer"
cp "${ROOT_DIR}/systemd/alienware-agent-event-dispatcher.service" "${SYSTEMD_USER_DIR}/alienware-agent-event-dispatcher.service"
cp "${ROOT_DIR}/systemd/alienware-agent-activity.service" "${SYSTEMD_USER_DIR}/alienware-agent-activity.service"

systemctl --user daemon-reload
systemctl --user enable --now alienware-author-agent.service
systemctl --user enable --now alienware-review-agent.service
systemctl --user enable --now alienware-agent-platform-report.timer
systemctl --user enable --now alienware-agent-event-dispatcher.service
systemctl --user enable --now alienware-agent-activity.service
systemctl --user start alienware-agent-platform-report.service

systemctl --user status alienware-author-agent.service --no-pager
systemctl --user status alienware-review-agent.service --no-pager
systemctl --user status alienware-agent-event-dispatcher.service --no-pager
systemctl --user status alienware-agent-activity.service --no-pager
systemctl --user status alienware-agent-platform-report.service --no-pager || true
systemctl --user status alienware-agent-platform-report.timer --no-pager
