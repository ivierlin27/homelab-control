#!/usr/bin/env bash
# Retired Infisical renderer. Machine secrets are SOPS + age.
# Usage stays `render-env-from-infisical.sh <name> [ignored-path]` so old callers work.

set -euo pipefail

TARGET_NAME="${1:-}"
if [[ -z "${TARGET_NAME}" ]]; then
  echo "usage: $0 <target-name> [ignored-infisical-path]" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${ROOT_DIR}/scripts/sops-render.sh" "${TARGET_NAME}"
