#!/usr/bin/env bash
# Remove duplicate NPM rows for fava.dev-path.org (keep highest id with a .conf file).
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/npm_api.sh
source "${ROOT_DIR}/scripts/lib/npm_api.sh"
export PATH="${HOME}/bin:${PATH}"
npm_load_config
npm_require_jq
DOMAIN="${NPM_FAVA_DOMAIN:-fava.dev-path.org}"
PROXMOX="${PROXMOX_SSH:-root@proxmox.dev-path.org}"
NPM_CT="${NPM_CT_ID:-102}"

rows="$(npm_api GET /api/nginx/proxy-hosts)"
ids="$(echo "${rows}" | jq -r --arg d "${DOMAIN}" '.[] | select(.domain_names[]? == $d) | .id')"
count="$(echo "${ids}" | grep -c . || true)"
if [[ "${count}" -le 1 ]]; then
  echo "npm_cleanup_duplicate_fava: no duplicates for ${DOMAIN}"
  exit 0
fi
echo "npm_cleanup_duplicate_fava: found ids: $(echo "${ids}" | tr '\n' ' ')"
keep=""
for id in ${ids}; do
  if ssh "${PROXMOX}" "pct exec ${NPM_CT} -- test -f /data/nginx/proxy_host/${id}.conf"; then
    keep="${id}"
  fi
done
if [[ -z "${keep}" ]]; then
  keep="$(echo "${ids}" | tail -1)"
fi
for id in ${ids}; do
  [[ "${id}" == "${keep}" ]] && continue
  echo "npm_cleanup_duplicate_fava: delete id=${id}"
  npm_api DELETE "/api/nginx/proxy-hosts/${id}" >/dev/null
done
echo "npm_cleanup_duplicate_fava: kept id=${keep}"
