#!/usr/bin/env bash
# Render a runtime env file from Infisical into tmpfs-backed /run.

set -euo pipefail

TARGET_NAME="${1:-}"
SECRET_PATH="${2:-/}"
TARGET_DIR="${RUNTIME_SECRET_DIR:-/run/homelab-control}"

if [[ -z "${TARGET_NAME}" ]]; then
  echo "usage: $0 <target-name> [secret-path]" >&2
  exit 1
fi

mkdir -p "${TARGET_DIR}"
umask 077

TARGET_FILE="${TARGET_DIR}/${TARGET_NAME}.env"

if ! command -v infisical >/dev/null 2>&1; then
  echo "infisical CLI not found in PATH" >&2
  exit 1
fi

infisical export \
  --projectId "${INFISICAL_PROJECT_ID}" \
  --env "${INFISICAL_ENVIRONMENT:-prod}" \
  --path "${SECRET_PATH}" \
  --format dotenv > "${TARGET_FILE}"

chmod 600 "${TARGET_FILE}"
echo "Wrote ${TARGET_FILE}"
