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
MODEL_GATEWAY_BASE_URL=https://model-gateway.dev-path.org/v1
MODEL_GATEWAY_API_KEY=replace-me
FORGEJO_BASE_URL=https://forgejo.dev-path.org
FORGEJO_REPO_OWNER=kevin
AGENT_PRINCIPAL=agent:homelab
EOF
  chmod 600 "${CONFIG_DIR}/agent-homelab.env"
fi

if [[ ! -f "${CONFIG_DIR}/agent-review.env" ]]; then
  cat > "${CONFIG_DIR}/agent-review.env" <<'EOF'
MODEL_GATEWAY_BASE_URL=https://model-gateway.dev-path.org/v1
MODEL_GATEWAY_API_KEY=replace-me
FORGEJO_BASE_URL=https://forgejo.dev-path.org
FORGEJO_REPO_OWNER=kevin
AGENT_PRINCIPAL=agent:review
EOF
  chmod 600 "${CONFIG_DIR}/agent-review.env"
fi

cp "${ROOT_DIR}/systemd/alienware-author-agent.service" "${SYSTEMD_USER_DIR}/alienware-author-agent.service"
cp "${ROOT_DIR}/systemd/alienware-review-agent.service" "${SYSTEMD_USER_DIR}/alienware-review-agent.service"

systemctl --user daemon-reload
systemctl --user enable --now alienware-author-agent.service
systemctl --user enable --now alienware-review-agent.service

systemctl --user status alienware-author-agent.service --no-pager
systemctl --user status alienware-review-agent.service --no-pager
