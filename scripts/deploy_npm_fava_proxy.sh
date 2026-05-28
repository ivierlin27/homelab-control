#!/usr/bin/env bash
# Deploy fava.dev-path.org NPM proxy host (LXC 102) from homelab-control.
#
# Writes /data/nginx/proxy_host/18.conf on the NPM container and reloads nginx.
# Idempotent: overwrites 18.conf each run.
#
# Usage (from Mac or Alienware):
#   cd ~/git/homelab-control && ./scripts/deploy_npm_fava_proxy.sh
#
# Requires: SSH to proxmox.dev-path.org (Alienware has keys).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF_SRC="${ROOT_DIR}/config/nginx-proxy-manager/fava.dev-path.org.conf"
PROXMOX="${PROXMOX_SSH:-root@proxmox.dev-path.org}"
NPM_CT="${NPM_CT_ID:-102}"
NPM_CONF_ID="${NPM_FAVA_CONF_ID:-18}"

if [[ ! -f "${CONF_SRC}" ]]; then
  echo "deploy_npm_fava_proxy: missing ${CONF_SRC}" >&2
  exit 1
fi

log() { echo "deploy_npm_fava_proxy: $*"; }

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

log "done — add NPM UI entry manually if you want it visible in the dashboard (optional)"
