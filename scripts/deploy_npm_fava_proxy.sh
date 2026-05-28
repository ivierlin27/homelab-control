#!/usr/bin/env bash
# Deploy fava.dev-path.org on NPM (LXC 102).
#
# Preferred: NPM REST API when NPM_TOKEN resolves (Infisical / Keychain / /run).
# Fallback: copy config/nginx-proxy-manager/fava.dev-path.org.conf to proxy_host/18.conf
#   (nginx works; host does not appear in NPM UI).
#
# Usage:
#   cd ~/git/homelab-control && ./scripts/deploy_npm_fava_proxy.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF_SRC="${ROOT_DIR}/config/nginx-proxy-manager/fava.dev-path.org.conf"
PROXMOX="${PROXMOX_SSH:-root@proxmox.dev-path.org}"
NPM_CT="${NPM_CT_ID:-102}"
NPM_CONF_ID="${NPM_FAVA_CONF_ID:-18}"
# shellcheck source=scripts/lib/npm_api.sh
source "${ROOT_DIR}/scripts/lib/npm_api.sh"

log() { echo "deploy_npm_fava_proxy: $*"; }

if npm_has_api_credentials; then
  log "NPM_TOKEN available — registering via REST API"
  exec "${ROOT_DIR}/scripts/npm_ensure_fava_proxy.sh"
fi

log "no NPM_TOKEN (Infisical / Keychain / /run) — file-only deploy (not in NPM UI)"
log "tip: docs/runbooks/nginx-proxy-manager.md"

if [[ ! -f "${CONF_SRC}" ]]; then
  echo "deploy_npm_fava_proxy: missing ${CONF_SRC}" >&2
  exit 1
fi

remote() {
  ssh "${PROXMOX}" "pct exec ${NPM_CT} -- $*"
}

log "install ${NPM_CONF_ID}.conf on NPM (CT ${NPM_CT})"
ssh "${PROXMOX}" "pct exec ${NPM_CT} -- tee /data/nginx/proxy_host/${NPM_CONF_ID}.conf" < "${CONF_SRC}" >/dev/null

log "nginx -t"
remote "nginx -t"

log "nginx reload"
remote "nginx -s reload"

log "smoke (from Proxmox host → NPM → Alienware Fava)"
if ssh "${PROXMOX}" "curl -s -o /dev/null -w '%{http_code}' --resolve fava.dev-path.org:443:127.0.0.1 -k https://fava.dev-path.org/" | grep -qE '^(200|302)$'; then
  log "OK — https://fava.dev-path.org responds"
else
  log "WARN — curl smoke failed; check alienware-fava.service and NPM logs" >&2
fi

log "done"
