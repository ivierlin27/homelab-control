#!/usr/bin/env bash
# Decrypt a SOPS env file into tmpfs /run/homelab-control/<name>.env (mode 0600).
#
# Usage: sops-render.sh <name>
#   looks for secrets/<name>.env relative to the repo root.
#
# On Proxmox hosts, private key is /root/.config/sops/age/keys.txt.
# On the Mac, set SOPS_AGE_KEY_FILE=~/.config/age/keys.txt (operator).

set -euo pipefail

NAME="${1:-}"
if [[ -z "${NAME}" ]]; then
  echo "usage: $0 <name>" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${ROOT_DIR}/secrets/${NAME}.env"
TARGET_DIR="${RUNTIME_SECRET_DIR:-/run/homelab-control}"
TARGET_FILE="${TARGET_DIR}/${NAME}.env"

if [[ ! -f "${SRC}" ]]; then
  echo "sops-render: missing ${SRC}" >&2
  exit 1
fi
if ! command -v sops >/dev/null 2>&1; then
  echo "sops-render: sops not in PATH" >&2
  exit 1
fi

if [[ -z "${SOPS_AGE_KEY_FILE:-}" ]]; then
  if [[ -f /root/.config/sops/age/keys.txt ]]; then
    export SOPS_AGE_KEY_FILE=/root/.config/sops/age/keys.txt
  elif [[ -f "${HOME}/.config/age/keys.txt" ]]; then
    export SOPS_AGE_KEY_FILE="${HOME}/.config/age/keys.txt"
  fi
fi

mkdir -p "${TARGET_DIR}"
umask 077
sops decrypt --input-type dotenv --output-type dotenv "${SRC}" > "${TARGET_FILE}"
chmod 600 "${TARGET_FILE}"
echo "Wrote ${TARGET_FILE}"
