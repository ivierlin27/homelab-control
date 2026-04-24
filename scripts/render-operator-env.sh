#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${RUNTIME_SECRET_DIR:-/run/homelab-control}"
TARGET_FILE="${TARGET_DIR}/operator-homelab.env"
DEFAULT_INGEST_URL="https://n8n.dev-path.org/webhook/ingest"

mkdir -p "${TARGET_DIR}"
umask 077

cat > "${TARGET_FILE}" <<EOF
MEMORY_ENGINE_INGEST_URL=${MEMORY_ENGINE_INGEST_URL:-${DEFAULT_INGEST_URL}}
EOF

chmod 600 "${TARGET_FILE}"
echo "Wrote ${TARGET_FILE}"
