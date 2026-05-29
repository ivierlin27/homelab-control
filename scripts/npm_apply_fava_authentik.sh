#!/usr/bin/env bash
# Apply Authentik forward-auth to fava.dev-path.org via NPM REST API (Advanced config).
#
# Prereqs:
#   - NPM credentials (Infisical /homelab/npm) — same as npm_ensure_fava_proxy.sh
#   - AUTHENTIK_UPSTREAM in ~/.config/homelab-control/npm.config
#     e.g. http://192.168.1.73:9000 (embedded outpost on Authentik host)
#   - Authentik Proxy Provider + Application for Fava (UI or authentik_ensure_fava_proxy.sh)
#
# Usage:
#   ./scripts/npm_apply_fava_authentik.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/npm_api.sh
source "${ROOT_DIR}/scripts/lib/npm_api.sh"

export PATH="${HOME}/bin:${PATH}"
npm_load_config
npm_require_jq

DOMAIN="${NPM_FAVA_DOMAIN:-fava.dev-path.org}"
TEMPLATE="${ROOT_DIR}/config/nginx-proxy-manager/fava-authentik-advanced.conf"

log() { echo "npm_apply_fava_authentik: $*"; }

[[ -f "${TEMPLATE}" ]] || {
  echo "npm_apply_fava_authentik: missing ${TEMPLATE}" >&2
  exit 1
}

if ! curl -sk -o /dev/null -w '' --connect-timeout 3 "${AUTHENTIK_UPSTREAM%/}/-/health/live/" 2>/dev/null; then
  log "WARN — Authentik health check failed at ${AUTHENTIK_UPSTREAM}"
  log "      deploy Authentik first (docs/runbooks/authentik-deploy.md) or fix AUTHENTIK_UPSTREAM"
fi

HOST_ID="$(npm_find_proxy_host_id "${DOMAIN}")" || {
  echo "npm_apply_fava_authentik: no NPM proxy host for ${DOMAIN}; run npm_ensure_fava_proxy.sh first" >&2
  exit 1
}

ADVANCED="$(npm_render_fava_authentik_advanced "${TEMPLATE}")"
CURRENT="$(npm_get_proxy_host "${HOST_ID}")"
if ! echo "${CURRENT}" | jq -e '.id' >/dev/null 2>&1; then
  echo "npm_apply_fava_authentik: GET host failed: $(echo "${CURRENT}" | jq -c '.error // .')" >&2
  exit 1
fi

PAYLOAD="$(npm_proxy_host_put_body "${CURRENT}" "${ADVANCED}")"
log "update proxy host id=${HOST_ID} (advanced_config $(echo "${ADVANCED}" | wc -c) bytes)"
RESP="$(npm_put_proxy_host "${HOST_ID}" "${PAYLOAD}")"

if ! echo "${RESP}" | jq -e '.id' >/dev/null 2>&1; then
  echo "npm_apply_fava_authentik: PUT failed: $(echo "${RESP}" | jq -c '.error // .')" >&2
  exit 1
fi

log "OK — NPM regenerated nginx for ${DOMAIN}"

# Unauthenticated curl should redirect to Authentik when provider is configured.
CODE="$(curl -sk -o /dev/null -w '%{http_code}' --resolve "${DOMAIN}:443:192.168.1.42" "https://${DOMAIN}/" || true)"
log "smoke https://${DOMAIN}/ → HTTP ${CODE} (expect 302 to login after Authentik app exists)"
