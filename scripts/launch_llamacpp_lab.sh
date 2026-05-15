#!/usr/bin/env bash
# Reusable launcher for ad-hoc llama.cpp `llama-server` endpoints used by the
# bench harness.
#
# Profile env file (sourced) must export:
#   LAB_NAME            short tag (used in OUT dirs)
#   LAB_IMAGE           image ref (e.g. ghcr.io/ggml-org/llama.cpp:server-cuda)
#   LAB_GPUS            "all" | "0" | "1" | "0,1"
#   LAB_PORT            host port (e.g. 8012)
#   LAB_API_KEY         bearer token
#   LAB_MODEL_PATH      absolute path to .gguf (host path, will be mounted)
#   LAB_SERVED_NAME     -a / --alias name exposed via OpenAI-compatible API
#   LAB_LLAMACPP_ARGS   extra llama-server args (string)
#   LAB_ENV_VARS        space-separated KEY=VAL pairs to set inside the container
#
# Usage:
#   LAB_PROFILE=~/.config/homelab-control/lab-llamacpp-qwen3-coder.env \
#     bash scripts/launch_llamacpp_lab.sh up
#   bash scripts/launch_llamacpp_lab.sh down

set -euo pipefail

CMD="${1:-up}"
NAME="homelab-lab-llamacpp"

if [[ "${CMD}" == "down" ]]; then
  podman rm -f "${NAME}" 2>/dev/null || true
  echo "down ${NAME}"
  exit 0
fi

if [[ -z "${LAB_PROFILE:-}" ]]; then
  echo "ERROR: set LAB_PROFILE to the env file" >&2
  exit 2
fi

# shellcheck disable=SC1090
. "${LAB_PROFILE}"

: "${LAB_NAME:?profile must set LAB_NAME}"
: "${LAB_IMAGE:?profile must set LAB_IMAGE}"
: "${LAB_GPUS:?profile must set LAB_GPUS}"
: "${LAB_PORT:?profile must set LAB_PORT}"
: "${LAB_API_KEY:?profile must set LAB_API_KEY}"
: "${LAB_MODEL_PATH:?profile must set LAB_MODEL_PATH}"
: "${LAB_SERVED_NAME:?profile must set LAB_SERVED_NAME}"
LAB_LLAMACPP_ARGS="${LAB_LLAMACPP_ARGS:-}"
LAB_ENV_VARS="${LAB_ENV_VARS:-}"

if [[ ! -f "${LAB_MODEL_PATH}" ]]; then
  echo "ERROR: LAB_MODEL_PATH does not exist on host: ${LAB_MODEL_PATH}" >&2
  exit 3
fi

podman rm -f "${NAME}" 2>/dev/null || true

ENV_FLAGS=()
for kv in ${LAB_ENV_VARS}; do
  ENV_FLAGS+=("-e" "${kv}")
done

MODEL_DIR="$(dirname "${LAB_MODEL_PATH}")"
MODEL_FILE="$(basename "${LAB_MODEL_PATH}")"

# llama-server defaults to port 8080; we override.
CMD_LINE=(
  podman run -d --name "${NAME}" --replace
  --device "nvidia.com/gpu=${LAB_GPUS}"
  --security-opt=label=disable
  -p "0.0.0.0:${LAB_PORT}:8080"
  -v "${MODEL_DIR}:/models:Z"
)
CMD_LINE+=("${ENV_FLAGS[@]}")
CMD_LINE+=(
  "${LAB_IMAGE}"
  --host 0.0.0.0 --port 8080
  --model "/models/${MODEL_FILE}"
  --alias "${LAB_SERVED_NAME}"
  --api-key "${LAB_API_KEY}"
)
# shellcheck disable=SC2206
EXTRA=( ${LAB_LLAMACPP_ARGS} )
CMD_LINE+=("${EXTRA[@]}")

echo "+ ${CMD_LINE[*]}"
"${CMD_LINE[@]}"

echo "Waiting for endpoint http://127.0.0.1:${LAB_PORT}/v1/models ..."
deadline=$(( $(date +%s) + 600 ))
while [[ $(date +%s) -lt $deadline ]]; do
  if curl -fsS -m 3 "http://127.0.0.1:${LAB_PORT}/v1/models" \
        -H "Authorization: Bearer ${LAB_API_KEY}" >/dev/null 2>&1; then
    echo "READY: http://127.0.0.1:${LAB_PORT}/v1  (model=${LAB_SERVED_NAME})"
    podman ps --filter "name=${NAME}"
    exit 0
  fi
  sleep 3
done
echo "TIMEOUT waiting for endpoint; recent logs:" >&2
podman logs --tail 120 "${NAME}" >&2
exit 1
