#!/usr/bin/env bash
# Create Authentik Proxy Provider + Application for Fava (if missing).
#
# Prereqs:
#   - Authentik running (docs/runbooks/authentik-deploy.md)
#   - Admin API token in ~/.config/homelab-control/authentik.config or Infisical /homelab/authentik
#
# Usage:
#   ./scripts/authentik_ensure_fava_proxy.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/authentik_api.sh
source "${ROOT_DIR}/scripts/lib/authentik_api.sh"

command -v jq >/dev/null || { echo "jq required" >&2; exit 1; }

authentik_load_config

PROVIDER_NAME="${AUTHENTIK_FAVA_PROVIDER_NAME:-fava-forward-auth}"
APP_NAME="${AUTHENTIK_FAVA_APP_NAME:-Fava (finance ledger)}"
APP_SLUG="${AUTHENTIK_FAVA_APP_SLUG:-fava}"
EXTERNAL_HOST="${AUTHENTIK_FAVA_EXTERNAL_HOST:-https://fava.dev-path.org}"

log() { echo "authentik_ensure_fava_proxy: $*"; }

# Optional: load token from Infisical homelab project
if [[ -z "${AUTHENTIK_API_TOKEN:-}" && -n "${INFISICAL_PROJECT_ID:-}" ]] && command -v infisical >/dev/null 2>&1; then
  path="${INFISICAL_AUTHENTIK_PATH:-/homelab/authentik}"
  dotenv="$(infisical export --domain "${INFISICAL_API_URL:-https://infisical.dev-path.org}" \
    --projectId "${INFISICAL_PROJECT_ID}" --env "${INFISICAL_ENVIRONMENT:-prod}" \
    --path "${path}" --format dotenv ${INFISICAL_TOKEN:+--token "$INFISICAL_TOKEN"} 2>/dev/null)" || true
  if [[ -n "${dotenv}" ]]; then
  while IFS= read -r line; do
    [[ "${line}" =~ ^AUTHENTIK_API_TOKEN= ]] && AUTHENTIK_API_TOKEN="${line#AUTHENTIK_API_TOKEN=}"
  done <<< "${dotenv}"
  fi
fi

authentik_require_token || exit 1

FLOW_PK="$(authentik_default_authorization_flow_pk)" || exit 1
log "authorization flow pk=${FLOW_PK}"

PROVIDER_PK="$(authentik_find_proxy_provider_pk "${PROVIDER_NAME}")"
if [[ -z "${PROVIDER_PK}" ]]; then
  log "create proxy provider ${PROVIDER_NAME}"
  BODY="$(jq -n \
    --arg name "${PROVIDER_NAME}" \
    --arg flow "${FLOW_PK}" \
    --arg ext "${EXTERNAL_HOST}" \
    '{
      name: $name,
      authorization_flow: $flow,
      external_host: $ext,
      mode: "forward_single",
      cookie_domain: "dev-path.org"
    }')"
  RESP="$(authentik_api POST "/providers/proxy/" "${BODY}")"
  PROVIDER_PK="$(echo "${RESP}" | jq -r '.pk // empty')"
  [[ -n "${PROVIDER_PK}" ]] || {
    echo "authentik_ensure_fava_proxy: create provider failed: $(echo "${RESP}" | jq -c '.')" >&2
    exit 1
  }
else
  log "proxy provider exists pk=${PROVIDER_PK}"
fi

APP_PK="$(authentik_find_application_pk "${APP_SLUG}")"
if [[ -z "${APP_PK}" ]]; then
  log "create application slug=${APP_SLUG}"
  BODY="$(jq -n \
    --arg name "${APP_NAME}" \
    --arg slug "${APP_SLUG}" \
    --arg provider "${PROVIDER_PK}" \
    --arg url "${EXTERNAL_HOST}" \
    '{
      name: $name,
      slug: $slug,
      provider: $provider,
      meta_launch_url: $url
    }')"
  RESP="$(authentik_api POST "/core/applications/" "${BODY}")"
  APP_PK="$(echo "${RESP}" | jq -r '.pk // empty')"
  [[ -n "${APP_PK}" ]] || {
    echo "authentik_ensure_fava_proxy: create application failed: $(echo "${RESP}" | jq -c '.')" >&2
    exit 1
  }
else
  log "application exists pk=${APP_PK}"
fi

log "OK — add application to Embedded Outpost in UI if not already:"
log "  Applications → Outposts → authentik Embedded Outpost → Applications → ${APP_NAME}"
log "Then: ./scripts/npm_apply_fava_authentik.sh"
