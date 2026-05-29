# shellcheck shell=bash
# NPM REST API helpers (Nginx Proxy Manager v2.x).
# Auth: POST /api/tokens with NPM_IDENTITY + NPM_SECRET → short-lived JWT (per run).
# Credentials live in Infisical (/homelab/npm), not in git or plain env files.

npm_load_config() {
  NPM_API_URL="${NPM_API_URL:-http://192.168.1.42:81}"
  NPM_WILDCARD_CERT_ID="${NPM_WILDCARD_CERT_ID:-5}"

  local config_dir="${HOMELAB_CONFIG_DIR:-${HOME}/.config/homelab-control}"
  local defaults="${NPM_CONFIG_FILE:-${config_dir}/npm.config}"
  local infisical_env="${INFISICAL_HOMELAB_ENV_FILE:-${config_dir}/infisical-homelab.env}"

  if [[ -f "${defaults}" ]]; then
    # shellcheck disable=SC1090
    set -a && source "${defaults}" && set +a
  fi
  if [[ -f "${infisical_env}" ]]; then
    # shellcheck disable=SC1090
    set -a && source "${infisical_env}" && set +a
  fi
  export INFISICAL_API_URL="${INFISICAL_API_URL:-https://infisical.dev-path.org}"
}

npm_require_jq() {
  command -v jq >/dev/null 2>&1 || {
    echo "npm_api: jq is required" >&2
    return 1
  }
}

# Strip optional surrounding quotes from Infisical/dotenv values.
npm_dotenv_unquote() {
  local v="$1"
  if [[ "${v}" =~ ^\".*\"$ ]]; then
    v="${v:1:${#v}-2}"
  elif [[ "${v}" =~ ^\'.*\'$ ]]; then
    v="${v:1:${#v}-2}"
  fi
  printf '%s' "${v}"
}

# Load NPM_IDENTITY / NPM_SECRET (and optional NPM_API_URL) from a dotenv blob in memory.
npm_parse_dotenv_credentials() {
  local dotenv="$1"
  NPM_IDENTITY=""
  NPM_SECRET=""
  local line key val
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ -z "${line}" || "${line}" =~ ^# ]] && continue
    key="${line%%=*}"
    val="$(npm_dotenv_unquote "${line#*=}")"
    case "${key}" in
      NPM_IDENTITY) NPM_IDENTITY="${val}" ;;
      NPM_SECRET) NPM_SECRET="${val}" ;;
      NPM_API_URL) NPM_API_URL="${val}" ;;
    esac
  done <<< "${dotenv}"
  [[ -n "${NPM_IDENTITY}" && -n "${NPM_SECRET}" ]]
}

npm_load_runtime_credentials() {
  local runtime="${RUNTIME_SECRET_DIR:-/run/homelab-control}/npm.env"
  [[ -f "${runtime}" ]] || return 1
  local dotenv
  dotenv="$(<"${runtime}")"
  npm_parse_dotenv_credentials "${dotenv}"
}

npm_load_infisical_credentials() {
  command -v infisical >/dev/null 2>&1 || return 1
  [[ -n "${INFISICAL_PROJECT_ID:-}" ]] || return 1

  local path="${INFISICAL_NPM_PATH:-/homelab/npm}"
  local env_name="${INFISICAL_ENVIRONMENT:-prod}"
  local dotenv
  local -a infisical_args=(export
    --domain "${INFISICAL_API_URL}"
    --projectId "${INFISICAL_PROJECT_ID}"
    --env "${env_name}"
    --path "${path}"
    --format dotenv)
  if [[ -n "${INFISICAL_TOKEN:-}" ]]; then
    infisical_args+=(--token "${INFISICAL_TOKEN}")
  fi
  if ! dotenv="$(infisical "${infisical_args[@]}" 2>&1)"; then
    echo "npm_api: infisical export failed: ${dotenv}" >&2
    return 1
  fi
  npm_parse_dotenv_credentials "${dotenv}"
}

# Exchange admin email + password for a bearer JWT (default ~1 day TTL in NPM).
npm_login() {
  npm_require_jq || return 1
  [[ -n "${NPM_IDENTITY:-}" && -n "${NPM_SECRET:-}" ]] || {
    echo "npm_api: NPM_IDENTITY and NPM_SECRET required for login" >&2
    return 1
  }
  local resp token payload
  payload="$(jq -n --arg identity "${NPM_IDENTITY}" --arg secret "${NPM_SECRET}" \
    '{identity: $identity, secret: $secret}')"
  resp="$(curl -sk -X POST "${NPM_API_URL}/api/tokens" \
    -H "Content-Type: application/json" \
    -d "${payload}")"
  token="$(echo "${resp}" | jq -r '.token // empty')"
  if [[ -z "${token}" ]]; then
    if [[ -z "${resp}" ]]; then
      echo "npm_api: login failed: empty response from ${NPM_API_URL}/api/tokens (check URL/quotes in NPM_API_URL)" >&2
    else
      echo "npm_api: login failed: $(echo "${resp}" | jq -c '.error // .' 2>/dev/null || echo "${resp}")" >&2
    fi
    return 1
  fi
  echo "${token}"
}

# Resolve bearer JWT (stdout).
npm_resolve_token() {
  npm_load_config

  # Ephemeral JWT already in env (e.g. parent shell or infisical run exported NPM_TOKEN).
  if [[ -n "${NPM_TOKEN:-}" ]]; then
    echo "${NPM_TOKEN}"
    return 0
  fi

  # Credentials already injected (infisical run).
  if [[ -n "${NPM_IDENTITY:-}" && -n "${NPM_SECRET:-}" ]]; then
    npm_login && return 0
  fi

  # Rendered machine env on Linux (/run, mode 0600).
  if npm_load_runtime_credentials 2>/dev/null; then
    npm_login && return 0
  fi

  # Fetch from Infisical then login (Mac operator with INFISICAL_PROJECT_ID + CLI login).
  if npm_load_infisical_credentials 2>/dev/null; then
    npm_login && return 0
  fi

  cat >&2 <<'EOF'
npm_api: no NPM credentials. Store in Infisical path /homelab/npm:
  NPM_IDENTITY, NPM_SECRET, NPM_API_URL
Then either:
  infisical run --path /homelab/npm -- ./scripts/npm_ensure_fava_proxy.sh
or set INFISICAL_PROJECT_ID and run ./scripts/npm_ensure_fava_proxy.sh
See docs/runbooks/nginx-proxy-manager.md
EOF
  return 1
}

npm_has_api_credentials() {
  npm_load_config
  if [[ -n "${NPM_IDENTITY:-}" && -n "${NPM_SECRET:-}" ]]; then
    return 0
  fi
  if [[ -f "${RUNTIME_SECRET_DIR:-/run/homelab-control}/npm.env" ]]; then
    return 0
  fi
  if [[ -n "${INFISICAL_PROJECT_ID:-}" ]] && command -v infisical >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

npm_get_token() {
  npm_resolve_token
}

npm_api() {
  local method="$1" path="$2" data="${3:-}"
  local token
  token="$(npm_get_token)" || return 1
  local -a curl_args=(-sk -X "${method}" "${NPM_API_URL}${path}"
    -H "Authorization: Bearer ${token}"
    -H "Content-Type: application/json"
    -H "Accept: application/json")
  if [[ -n "${data}" ]]; then
    curl_args+=(--data "${data}")
  fi
  curl "${curl_args[@]}"
}

npm_find_proxy_host_id() {
  local domain="$1"
  npm_require_jq || return 1
  local rows id
  rows="$(npm_api GET /api/nginx/proxy-hosts)" || return 1
  id="$(echo "${rows}" | jq -r --arg d "${domain}" '
    .[] | select(.domain_names[]? == $d) | .id' | head -1)"
  if [[ -z "${id}" || "${id}" == "null" ]]; then
    return 1
  fi
  echo "${id}"
}
