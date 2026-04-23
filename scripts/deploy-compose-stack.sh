#!/usr/bin/env bash
# Render a runtime env file from the machine secret store and deploy a compose stack.

set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <stack-dir> <runtime-env-name> <infisical-path>" >&2
  exit 1
fi

STACK_DIR="$1"
RUNTIME_ENV_NAME="$2"
SECRET_PATH="$3"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_ENV_DIR="${RUNTIME_SECRET_DIR:-/run/homelab-control}"
RUNTIME_ENV_FILE="${RUNTIME_ENV_DIR}/${RUNTIME_ENV_NAME}.env"

"${ROOT_DIR}/scripts/render-env-from-infisical.sh" "${RUNTIME_ENV_NAME}" "${SECRET_PATH}"

docker compose --env-file "${RUNTIME_ENV_FILE}" -f "${STACK_DIR}/docker-compose.yml" up -d
