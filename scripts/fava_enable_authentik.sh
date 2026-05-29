#!/usr/bin/env bash
# End-to-end: Authentik app (API) + NPM forward-auth (API) for Fava.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${HOME}/bin:${PATH}"
if [[ -f "${HOME}/.config/homelab-control/npm.config" ]]; then
  # shellcheck disable=SC1091
  set -a && source "${HOME}/.config/homelab-control/npm.config" && set +a
fi
if [[ -f "${HOME}/.config/homelab-control/infisical-homelab.env" ]]; then
  # shellcheck disable=SC1091
  set -a && source "${HOME}/.config/homelab-control/infisical-homelab.env" && set +a
fi
"${ROOT_DIR}/scripts/authentik_ensure_fava_proxy.sh"
"${ROOT_DIR}/scripts/npm_apply_fava_authentik.sh"
