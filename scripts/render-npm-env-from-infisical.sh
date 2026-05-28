#!/usr/bin/env bash
# Render NPM login secrets from Infisical into tmpfs-backed /run/homelab-control/npm.env
#
# Infisical path (default): /homelab/npm
#   NPM_IDENTITY  — NPM admin email
#   NPM_SECRET    — NPM admin password
#   NPM_API_URL   — http://192.168.1.42:81
#
# Scripts call POST /api/tokens at runtime to obtain a short-lived JWT.
#
# Usage:
#   export INFISICAL_PROJECT_ID=...
#   export INFISICAL_TOKEN=...    # Infisical machine token (CLI auth)
#   ./scripts/render-npm-env-from-infisical.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRET_PATH="${INFISICAL_NPM_PATH:-/homelab/npm}"

"${ROOT_DIR}/scripts/render-env-from-infisical.sh" npm "${SECRET_PATH}"
