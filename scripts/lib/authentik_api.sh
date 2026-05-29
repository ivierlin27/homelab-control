# shellcheck shell=bash
# Authentik REST API helpers (self-hosted).

authentik_load_config() {
  local config_dir="${HOMELAB_CONFIG_DIR:-${HOME}/.config/homelab-control}"
  local defaults="${AUTHENTIK_CONFIG_FILE:-${config_dir}/authentik.config}"
  if [[ -f "${defaults}" ]]; then
    # shellcheck disable=SC1090
    set -a && source "${defaults}" && set +a
  fi
  AUTHENTIK_URL="${AUTHENTIK_URL:-https://authentik.dev-path.org}"
  AUTHENTIK_API_URL="${AUTHENTIK_API_URL:-${AUTHENTIK_URL%/}/api/v3}"
}

authentik_require_token() {
  authentik_load_config
  [[ -n "${AUTHENTIK_API_TOKEN:-}" ]] || {
    echo "authentik_api: set AUTHENTIK_API_TOKEN (Infisical /homelab/authentik or authentik.config)" >&2
    return 1
  }
}

authentik_api() {
  local method="$1" path="$2" data="${3:-}"
  authentik_require_token || return 1
  local -a curl_args=(-sk -X "${method}" "${AUTHENTIK_API_URL}${path}"
    -H "Authorization: Bearer ${AUTHENTIK_API_TOKEN}"
    -H "Content-Type: application/json"
    -H "Accept: application/json")
  if [[ -n "${data}" ]]; then
    curl_args+=(--data "${data}")
  fi
  curl "${curl_args[@]}"
}

# First flow slug matching provider authorization (default install).
authentik_default_authorization_flow_pk() {
  authentik_require_token || return 1
  local resp
  resp="$(authentik_api GET "/flows/instances/?search=default-provider-authorization")"
  local pk
  pk="$(echo "${resp}" | jq -r '.results[0].pk // empty')"
  if [[ -z "${pk}" ]]; then
    resp="$(authentik_api GET "/flows/instances/?page_size=50")"
    pk="$(echo "${resp}" | jq -r '
      .results[] | select(.designation == "authorization") | .pk' | head -1)"
  fi
  [[ -n "${pk}" ]] || {
    echo "authentik_api: no authorization flow found" >&2
    return 1
  }
  echo "${pk}"
}

authentik_find_proxy_provider_pk() {
  local name="$1"
  authentik_require_token || return 1
  local resp
  resp="$(authentik_api GET "/providers/proxy/?search=${name}")"
  echo "${resp}" | jq -r --arg n "${name}" '
    .results[] | select(.name == $n) | .pk' | head -1
}

authentik_find_application_pk() {
  local slug="$1"
  authentik_require_token || return 1
  local resp
  resp="$(authentik_api GET "/core/applications/?search=${slug}")"
  echo "${resp}" | jq -r --arg s "${slug}" '
    .results[] | select(.slug == $s) | .pk' | head -1
}
