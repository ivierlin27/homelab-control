#!/usr/bin/env bash
# Create a long-lived API token (expiring=false) via Authentik REST API.
#
# The UI often only lets you edit the token name; expiry is controlled by API or
# user attribute goauthentik.io/user/token-expires on the admin user.
#
# Usage:
#   AUTHENTIK_API_TOKEN=<short-lived-admin-token> ./scripts/authentik_create_api_token.sh
#   # or with authentik.config containing a still-valid token
#
# Prints view_key URL; fetch key and store in Infisical as AUTHENTIK_API_TOKEN.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/authentik_api.sh
source "${ROOT_DIR}/scripts/lib/authentik_api.sh"

command -v jq >/dev/null || { echo "jq required" >&2; exit 1; }

authentik_load_config
authentik_require_token || exit 1

IDENT="${AUTHENTIK_TOKEN_IDENTIFIER:-homelab-automation-long}"
EXPIRING="${AUTHENTIK_TOKEN_EXPIRING:-false}"

log() { echo "authentik_create_api_token: $*"; }

# Replace existing token with same identifier if present
EXISTING="$(authentik_api GET "/core/tokens/${IDENT}/" 2>/dev/null | jq -r '.identifier // empty' || true)"
if [[ "${EXISTING}" == "${IDENT}" ]]; then
  log "delete existing token ${IDENT}"
  authentik_api DELETE "/core/tokens/${IDENT}/" >/dev/null || true
fi

BODY="$(jq -n \
  --arg id "${IDENT}" \
  --argjson expiring "${EXPIRING}" \
  '{identifier: $id, intent: "api", expiring: $expiring}')"

log "create token identifier=${IDENT} expiring=${EXPIRING}"
RESP="$(authentik_api POST "/core/tokens/" "${BODY}")"
if ! echo "${RESP}" | jq -e '.identifier' >/dev/null 2>&1; then
  echo "authentik_create_api_token: create failed: $(echo "${RESP}" | jq -c '.')" >&2
  echo "If 400/403 on expiring=false, set on akadmin (Directory → Users → akadmin → Attributes):" >&2
  echo '  {"goauthentik.io/user/token-expires": false}' >&2
  exit 1
fi

KEY_RESP="$(authentik_api GET "/core/tokens/${IDENT}/view_key/")"
KEY="$(echo "${KEY_RESP}" | jq -r '.key // empty')"
if [[ -z "${KEY}" ]]; then
  log "created; fetch key manually:"
  echo "  ${AUTHENTIK_URL}/api/v3/core/tokens/${IDENT}/view_key/"
  exit 0
fi

log "OK — store this in Infisical AUTHENTIK_API_TOKEN (not printed to avoid log leakage)"
echo "${KEY}"
