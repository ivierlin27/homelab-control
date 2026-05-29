#!/usr/bin/env bash
# Register fava.dev-path.org in NPM via REST API (updates SQLite + regenerates nginx).
#
# Prereq: NPM_TOKEN via Infisical, Keychain, or /run/homelab-control/npm.env
#
# Usage:
#   ./scripts/npm_ensure_fava_proxy.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/npm_api.sh
source "${ROOT_DIR}/scripts/lib/npm_api.sh"

export PATH="${HOME}/bin:${PATH}"
npm_load_config
npm_require_jq

DOMAIN="${NPM_FAVA_DOMAIN:-fava.dev-path.org}"
UPSTREAM_HOST="${NPM_FAVA_UPSTREAM_HOST:-192.168.1.45}"
UPSTREAM_PORT="${NPM_FAVA_UPSTREAM_PORT:-5002}"
CERT_ID="${NPM_WILDCARD_CERT_ID:-5}"

log() { echo "npm_ensure_fava_proxy: $*"; }

PAYLOAD="$(jq -n \
  --arg domain "${DOMAIN}" \
  --arg host "${UPSTREAM_HOST}" \
  --argjson port "${UPSTREAM_PORT}" \
  --argjson cert "${CERT_ID}" \
  '{
    domain_names: [$domain],
    forward_scheme: "http",
    forward_host: $host,
    forward_port: $port,
    access_list_id: 0,
    certificate_id: $cert,
    ssl_forced: true,
    caching_enabled: false,
    block_exploits: true,
    advanced_config: "",
    meta: {},
    allow_websocket_upgrade: false,
    http2_support: true,
    enabled: true,
    locations: [],
    hsts_enabled: true,
    hsts_subdomains: true
  }')"

EXISTING_ID=""
if EXISTING_ID="$(npm_find_proxy_host_id "${DOMAIN}" 2>/dev/null)"; then
  log "update proxy host id=${EXISTING_ID}"
  RESP="$(npm_api PUT "/api/nginx/proxy-hosts/${EXISTING_ID}" "${PAYLOAD}")"
else
  log "create proxy host for ${DOMAIN}"
  RESP="$(npm_api POST /api/nginx/proxy-hosts "${PAYLOAD}")"
fi

if ! echo "${RESP}" | jq -e '.id' >/dev/null 2>&1; then
  echo "npm_ensure_fava_proxy: API error: $(echo "${RESP}" | jq -c '.error // .')" >&2
  exit 1
fi

HOST_ID="$(echo "${RESP}" | jq -r '.id')"
log "OK — NPM proxy host id=${HOST_ID} (visible in UI; nginx config regenerated)"

if command -v curl >/dev/null 2>&1; then
  CODE="$(curl -sk -o /dev/null -w '%{http_code}' --resolve "${DOMAIN}:443:192.168.1.42" "https://${DOMAIN}/" || true)"
  log "smoke https://${DOMAIN}/ → HTTP ${CODE:-failed}"
fi
