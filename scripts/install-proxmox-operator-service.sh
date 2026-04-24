#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_DIR="/etc/systemd/system"
DEFAULTS_DIR="/etc/default"
DEFAULTS_FILE="${DEFAULTS_DIR}/homelab-operator"
RUNTIME_SECRET_DIR="${RUNTIME_SECRET_DIR:-/run/homelab-control}"
REPO_ROOT="${1:-/opt/homelab-control}"
INGEST_URL="${MEMORY_ENGINE_INGEST_URL:-https://n8n.dev-path.org/webhook/ingest}"

mkdir -p "${DEFAULTS_DIR}" "${RUNTIME_SECRET_DIR}"
chmod 700 "${RUNTIME_SECRET_DIR}"

cat > "${DEFAULTS_FILE}" <<EOF
HOMELAB_CONTROL_ROOT=${REPO_ROOT}
MEMORY_ENGINE_INGEST_URL=${INGEST_URL}
EOF
chmod 600 "${DEFAULTS_FILE}"

install -m 0644 "${ROOT_DIR}/systemd/homelab-operator.service" "${SYSTEMD_DIR}/homelab-operator.service"
install -m 0644 "${ROOT_DIR}/systemd/homelab-operator.timer" "${SYSTEMD_DIR}/homelab-operator.timer"

"${ROOT_DIR}/scripts/render-operator-env.sh"

systemctl daemon-reload
systemctl enable --now homelab-operator.timer
systemctl start homelab-operator.service

systemctl status homelab-operator.service --no-pager || true
systemctl status homelab-operator.timer --no-pager || true
