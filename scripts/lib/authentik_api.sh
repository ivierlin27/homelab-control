# shellcheck shell=bash
# Authentik REST API helpers (self-hosted).

authentik_load_config() {
  local config_dir="${HOMELAB_CONFIG_DIR:-${HOME}/.config/homelab-control}"
  local defaults="${AUTHENTIK_CONFIG_FILE:-${config_dir}/authentik.config}"
  local npm_config="${NPM_CONFIG_FILE:-${config_dir}/npm.config}"
  local infisical_env="${INFISICAL_HOMELAB_ENV_FILE:-${config_dir}/infisical-homelab.env}"
  if [[ -f "${npm_config}" ]]; then
    # shellcheck disable=SC1090
    set -a && source "${npm_config}" && set +a
  fi
  if [[ -f "${infisical_env}" ]]; then
    # shellcheck disable=SC1090
    set -a && source "${infisical_env}" && set +a
  fi
  if [[ -f "${defaults}" ]]; then
    # shellcheck disable=SC1090
    set -a && source "${defaults}" && set +a
  fi
  export INFISICAL_API_URL="${INFISICAL_API_URL:-https://infisical.dev-path.org}"
  export PATH="${HOME}/bin:${PATH}"
  AUTHENTIK_URL="${AUTHENTIK_URL:-https://authentik.dev-path.org}"
  AUTHENTIK_API_URL="${AUTHENTIK_API_URL:-${AUTHENTIK_URL%/}/api/v3}"
  authentik_load_token_from_infisical || true
}

authentik_dotenv_unquote() {
  local v="$1"
  if [[ "${v}" =~ ^\".*\"$ ]]; then
    v="${v:1:${#v}-2}"
  elif [[ "${v}" =~ ^\'.*\'$ ]]; then
    v="${v:1:${#v}-2}"
  fi
  printf '%s' "${v}"
}

authentik_load_token_from_infisical() {
  [[ -n "${AUTHENTIK_API_TOKEN:-}" ]] && return 0
  command -v infisical >/dev/null 2>&1 || return 1
  [[ -n "${INFISICAL_PROJECT_ID:-}" ]] || return 1

  local path="${INFISICAL_AUTHENTIK_PATH:-/homelab/authentik}"
  local env_name="${INFISICAL_ENVIRONMENT:-prod}"
  local dotenv
  local -a infisical_args=(export
    --domain "${INFISICAL_API_URL:-https://infisical.dev-path.org}"
    --projectId "${INFISICAL_PROJECT_ID}"
    --env "${env_name}"
    --path "${path}"
    --format dotenv)
  if [[ -n "${INFISICAL_TOKEN:-}" ]]; then
    infisical_args+=(--token "${INFISICAL_TOKEN}")
  fi
  if ! dotenv="$(infisical "${infisical_args[@]}" 2>&1)"; then
    echo "authentik_api: infisical export failed (${path}): ${dotenv}" >&2
    return 1
  fi

  local _line _key _val
  while IFS= read -r _line || [[ -n "${_line}" ]]; do
    [[ -z "${_line}" || "${_line}" =~ ^# ]] && continue
    [[ "${_line}" != *=* ]] && continue
    _key="${_line%%=*}"
    _val="${_line#*=}"
    if [[ "${_key}" == "AUTHENTIK_API_TOKEN" ]]; then
      # shellcheck disable=SC2034
      AUTHENTIK_API_TOKEN="$(authentik_dotenv_unquote "${_val}")"
      [[ -n "${AUTHENTIK_API_TOKEN}" ]] && return 0
    fi
  done <<< "${dotenv}"
  echo "authentik_api: AUTHENTIK_API_TOKEN missing in Infisical ${path}" >&2
  return 1
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

# First flow slug matching provider invalidation (default install).
authentik_default_invalidation_flow_pk() {
  authentik_require_token || return 1
  local resp
  resp="$(authentik_api GET "/flows/instances/?search=default-provider-invalidation")"
  local pk
  pk="$(echo "${resp}" | jq -r '.results[0].pk // empty')"
  if [[ -z "${pk}" ]]; then
    resp="$(authentik_api GET "/flows/instances/?page_size=50")"
    pk="$(echo "${resp}" | jq -r '
      .results[] | select(.designation == "invalidation") | .pk' | head -1)"
  fi
  [[ -n "${pk}" ]] || {
    echo "authentik_api: no invalidation flow found" >&2
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

# Attach provider pk to the embedded outpost (required for forward-auth).
authentik_ensure_embedded_outpost_provider() {
  local provider_pk="$1"
  authentik_require_token || return 1
  local list_resp outpost_pk detail providers body
  list_resp="$(authentik_api GET "/outposts/instances/?search=embedded")"
  outpost_pk="$(echo "${list_resp}" | jq -r '
    .results[] | select(.managed == "goauthentik.io/outposts/embedded") | .pk' | head -1)"
  [[ -n "${outpost_pk}" ]] || outpost_pk="$(echo "${list_resp}" | jq -r '.results[0].pk // empty')"
  [[ -n "${outpost_pk}" ]] || {
    echo "authentik_api: no embedded outpost found" >&2
    return 1
  }
  detail="$(authentik_api GET "/outposts/instances/${outpost_pk}/")"
  providers="$(echo "${detail}" | jq -c --argjson pk "${provider_pk}" '
    (.providers // []) as $p | if ($p | index($pk)) != null then $p else ($p + [$pk]) end')"
  body="$(jq -n --argjson providers "${providers}" '{providers: $providers}')"
  authentik_api PATCH "/outposts/instances/${outpost_pk}/" "${body}" >/dev/null
}
